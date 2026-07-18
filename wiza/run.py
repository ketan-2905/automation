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

from . import cdp, config, csv_store


def _sleep_between(count):
    delay = random.uniform(config.MIN_DELAY, config.MAX_DELAY)
    if count and count % config.LONG_PAUSE_EVERY == 0:
        delay = random.uniform(*config.LONG_PAUSE)
    time.sleep(delay)


def main():
    ap = argparse.ArgumentParser(description="Enrich leads from the Wiza panel.")
    ap.add_argument("--limit", type=int, default=None, help="process at most N (overrides cap)")
    ap.add_argument("--daily-cap", type=int, default=config.DAILY_CAP)
    ap.add_argument("--dry-run", action="store_true", help="print results, don't write CSV")
    ap.add_argument("--headless", action="store_true", help="not recommended (extension/detection)")
    ap.add_argument("--verbose", action="store_true", help="show the per-profile poll trace")
    ap.add_argument("--start-row", type=int, default=None,
                    help="skip leads before this 1-based CSV data row (header not counted)")
    args = ap.parse_args()

    df = csv_store.prepare()
    idxs = csv_store.targets(df)
    if args.start_row is not None:
        idxs = [i for i in idxs if i >= args.start_row - 1]
    cap = args.limit if args.limit is not None else args.daily_cap
    idxs = idxs[:cap]

    print(f"{len(idxs)} profile(s) to process this run (cap {cap}). Output: {config.CSV_OUTPUT}")
    if not idxs:
        print("Nothing to do — all target rows already processed.")
        return

    chrome = cdp.CdpChrome(headless=args.headless, debug=args.verbose)
    processed = 0
    try:
        for count, idx in enumerate(idxs):
            url = str(df.at[idx, config.COL_URL]).strip()
            name = str(df.at[idx, config.COL_NAME]) if config.COL_NAME in df.columns else ""
            if not url:
                csv_store.mark(df, idx, "error")
                csv_store.save(df)
                continue

            try:
                result = chrome.scrape(url)
            except Exception as e:
                print(f"[{idx}] {name}: scrape error: {str(e)[:160]}")
                csv_store.mark(df, idx, "error")
                csv_store.save(df)
                _sleep_between(count)
                continue

            if result.get("blocked"):
                print("!! LinkedIn checkpoint/login wall detected — stopping to protect the account.")
                csv_store.save(df)
                break

            emails, phones = result["emails"], result["phones"]
            print(f"[{idx}] {name}: emails={emails[:2]} phone={phones[:1]}")

            if not args.dry_run:
                if not (emails or phones) and result.get("clicked_reveal"):
                    # We pressed 'Reveal contact info' but the lookup didn't
                    # finish in time. The reveal persists on Wiza's side, so a
                    # later run reads it instantly — keep the row retryable
                    # instead of burying a spent credit as not_found.
                    csv_store.mark(df, idx, "error")
                else:
                    csv_store.apply_result(df, idx, emails, phones)
                csv_store.save(df)  # flush every row so a crash never loses progress

            processed += 1
            _sleep_between(count)
    finally:
        chrome.close()
        print(f"Done. Processed {processed} profile(s). Output: {config.CSV_OUTPUT}")


if __name__ == "__main__":
    main()
