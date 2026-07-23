"""Main orchestrator: walk target rows, read the Wiza panel, write back to CSV.

Uses the CDP scraper (wiza/cdp.py): it launches a NORMAL Chrome (so Wiza's
anti-bot doesn't block it) and reads each profile's panel through the low-level
DOM domain across every frame. See wiza/cdp.py for why.

Usage (from the project root):
    python -m wiza.run                 # process up to DAILY_CAP profiles
    python -m wiza.run --limit 3       # small smoke test
    python -m wiza.run --dry-run       # read + print, don't modify the CSV
    python -m wiza.run --daily-cap 200 # override the per-run cap
    python -m wiza.run --verbose       # per-profile poll trace
"""
from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

from . import cdp, config, csv_store


def _sleep_between(count):
    delay = random.uniform(config.MIN_DELAY, config.MAX_DELAY)
    if count and count % config.LONG_PAUSE_EVERY == 0:
        delay = random.uniform(*config.LONG_PAUSE)
    time.sleep(delay)


def _lead_url(df, idx, no_sales_nav):
    """Which page to open for this lead.

    A Sales Navigator lead page needs a Sales Nav seat — an account without one
    just lands on an upsell page, so no Wiza panel ever appears and the row comes
    back empty. Those accounts open the plain /in/ profile instead, where the
    extension works exactly the same.
    """
    if no_sales_nav:
        for col in (config.COL_LINKEDIN_URL, config.COL_DEFAULT_URL):
            if col in df.columns:
                u = str(df.at[idx, col]).strip()
                if u:
                    return u
    return str(df.at[idx, config.COL_URL]).strip()


def _record(df, idx, result, out_path):
    """Apply one lead's outcome and flush, so a stop never loses finished work."""
    emails, phones = result["emails"], result["phones"]
    if emails or phones:
        csv_store.apply_result(df, idx, emails, phones)      # -> done
    elif result.get("resolved"):
        # Panel gave a definitive "No email/phone found" — final.
        csv_store.apply_result(df, idx, [], [])              # -> not_found
    else:
        # Panel never resolved (stuck 'Finding contact data...' or never
        # loaded). Any reveal we clicked persists on Wiza's side, so a later
        # run reads it instantly — keep the row retryable.
        csv_store.mark(df, idx, "error")
    csv_store.save(df, out_path)


def _run_concurrent(chrome, df, idxs, args, out_path):
    """Process leads with several tabs in flight, writing each as it resolves."""
    items = []
    for idx in idxs:
        url = _lead_url(df, idx, args.no_sales_nav)
        name = str(df.at[idx, config.COL_NAME]) if config.COL_NAME in df.columns else ""
        if not url:
            csv_store.mark(df, idx, "error")
            continue
        items.append((idx, url, name or str(idx)))
    csv_store.save(df, out_path)

    stats = {"n": 0}

    def on_result(idx, result):
        name = str(df.at[idx, config.COL_NAME]) if config.COL_NAME in df.columns else ""
        if result.get("blocked"):
            print("!! LinkedIn checkpoint/login wall detected — stopping to "
                  "protect the account.")
            return
        if result.get("rate_limited"):
            print("!! Wiza fair-use limit hit ('too many requests in too short "
                  "of time') — stopping. These rows stay retryable; wait a "
                  "while, then rerun with a smaller --concurrency / --delay.")
            return
        print(f"[{idx}] {name}: emails={result['emails'][:2]} "
              f"phone={result['phones'][:1]}")
        if not args.dry_run:
            _record(df, idx, result, out_path)
        stats["n"] += 1

    try:
        chrome.scrape_many(items, concurrency=args.concurrency,
                           on_result=on_result, open_stagger=args.delay)
    except KeyboardInterrupt:
        print("\nInterrupted — finished rows are already saved.")
    return stats["n"]


def main():
    ap = argparse.ArgumentParser(description="Enrich leads from the Wiza panel.")
    ap.add_argument("--limit", type=int, default=None, help="process at most N (overrides cap)")
    ap.add_argument("--daily-cap", type=int, default=config.DAILY_CAP)
    ap.add_argument("--dry-run", action="store_true", help="print results, don't write CSV")
    ap.add_argument("--headless", action="store_true", help="not recommended (extension/detection)")
    ap.add_argument("--verbose", action="store_true", help="show the per-profile poll trace")
    ap.add_argument("--start-row", type=int, default=None,
                    help="skip leads before this 1-based CSV data row (header not counted)")
    ap.add_argument("--end-row", type=int, default=None,
                    help="stop after this 1-based CSV data row (inclusive)")
    ap.add_argument("--profile", default=None,
                    help="named Chrome profile to drive (see `wiza.browser --profile`)")
    ap.add_argument("--no-sales-nav", action="store_true",
                    help="this account has no Sales Navigator seat — open the "
                         "plain /in/ profile instead of the /sales/lead/ page")
    ap.add_argument("--concurrency", type=int, default=1,
                    help="leads to process at once, in parallel tabs (default 1)")
    ap.add_argument("--delay", type=float, default=8.0,
                    help="seconds between starting leads, per worker (default 8). "
                         "Raise it if Wiza reports fair-use limits.")
    ap.add_argument("--shard", default=None, metavar="I/N",
                    help="take only shard I of N of the work, e.g. 1/4 — lets "
                         "several profiles run at once without overlapping")
    ap.add_argument("--output", default=None,
                    help="CSV to write (default: per-profile file when --profile "
                         "is set, so parallel workers never overwrite each other)")
    args = ap.parse_args()

    # Each worker needs its own file: every worker rewrites the WHOLE sheet on
    # save, so sharing one file would make them clobber each other.
    if args.output:
        out_path = Path(args.output)
    else:
        tag = args.profile
        if not tag and args.shard:
            tag = "shard" + args.shard.replace("/", "of")
        out_path = config.output_csv(tag)

    df = csv_store.prepare(out_path)
    idxs = csv_store.targets(df)
    if args.start_row is not None:
        idxs = [i for i in idxs if i >= args.start_row - 1]
    if args.end_row is not None:
        idxs = [i for i in idxs if i <= args.end_row - 1]
    if args.shard:
        try:
            si, sn = (int(x) for x in args.shard.split("/"))
        except ValueError:
            ap.error("--shard must look like I/N, e.g. 2/4")
        if not (1 <= si <= sn):
            ap.error(f"--shard {args.shard}: need 1 <= I <= N")
        # Shard on the ROW INDEX, not on position in this worker's target list.
        # Each worker reads its own CSV, so their target lists differ (different
        # rows already finished) — slicing by position would then hand the same
        # lead to two workers, wasting a reveal on it twice. Row index is the
        # same everywhere, so these sets are always disjoint.
        idxs = [i for i in idxs if i % sn == si - 1]
    cap = args.limit if args.limit is not None else args.daily_cap
    idxs = idxs[:cap]

    who = f"[{args.profile}] " if args.profile else ""
    print(f"{who}{len(idxs)} profile(s) this run (cap {cap}, concurrency "
          f"{args.concurrency}). Output: {out_path}")
    if not idxs:
        print("Nothing to do — all target rows already processed.")
        return

    chrome = cdp.CdpChrome(headless=args.headless, debug=args.verbose,
                           profile=args.profile)
    processed = 0
    if args.concurrency > 1:
        processed = _run_concurrent(chrome, df, idxs, args, out_path)
        chrome.close()
        print(f"Done. Processed {processed} profile(s). Output: {out_path}")
        return
    try:
        for count, idx in enumerate(idxs):
            url = _lead_url(df, idx, args.no_sales_nav)
            name = str(df.at[idx, config.COL_NAME]) if config.COL_NAME in df.columns else ""
            if not url:
                csv_store.mark(df, idx, "error")
                csv_store.save(df, out_path)
                continue

            try:
                result = chrome.scrape(url)
            except Exception as e:
                print(f"[{idx}] {name}: scrape error: {str(e)[:160]}")
                csv_store.mark(df, idx, "error")
                csv_store.save(df, out_path)
                _sleep_between(count)
                continue

            if result.get("blocked"):
                print("!! LinkedIn checkpoint/login wall detected — stopping to protect the account.")
                csv_store.save(df, out_path)
                break

            if result.get("rate_limited"):
                print("!! Wiza fair-use limit hit ('too many requests in too "
                      "short of time') — stopping. This row stays retryable; "
                      "wait a while, then rerun more slowly.")
                csv_store.save(df, out_path)
                break

            emails, phones = result["emails"], result["phones"]
            print(f"[{idx}] {name}: emails={emails[:2]} phone={phones[:1]}")

            if not args.dry_run:
                _record(df, idx, result, out_path)  # flush every row

            processed += 1
            _sleep_between(count)
    finally:
        chrome.close()
        print(f"Done. Processed {processed} profile(s). Output: {out_path}")


if __name__ == "__main__":
    main()
