"""One command to enrich a range of rows across every logged-in profile.

You say where to start and how many rows; this works out the rest — which
profiles are available, how to split the range so none of them overlap, how many
tabs each should run, where to log, and merging the results back at the end.

    python -m wiza.go --start 1584 --count 400

    python -m wiza.go --start 1584 --count 400 --dry-run   # just show the plan
    python -m wiza.go --start 1584 --count 400 --profiles a1,a2

Each profile runs as its own process against its own slice and its own CSV, so
they never overwrite each other; `wiza.merge` folds everything back into
`master - gyms.enriched.csv` when they're done.
"""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
import threading
import time

from . import config

# Tabs in flight per profile. Wiza's ~60s lookup is spent waiting on their
# server, so a handful of leads can share that window; beyond this the gain
# flattens out while the burst looks less human to LinkedIn.
DEFAULT_CONCURRENCY = 5

# Seconds between launching each worker. Chrome needs a moment to load a large
# profile before it opens its debug port, and starting several at the very same
# instant makes the later ones time out waiting for it.
LAUNCH_STAGGER = 8

_PREFIX = "chrome-profile"


def discover_profiles():
    """Every Chrome profile that's been set up, default first.

    A profile only exists once you've logged it into LinkedIn + Wiza via
    `python -m wiza.browser [--profile NAME]`, so this is a good proxy for
    "accounts that are ready to work".
    """
    found = []
    if config.PROFILE_DIR.exists():
        found.append(None)                      # the original, unnamed profile
    base = config.PROFILE_DIR.parent
    if base.exists():
        for p in sorted(base.glob(f"{_PREFIX}-*")):
            if p.is_dir():
                found.append(p.name[len(_PREFIX) + 1:])
    return found


def _label(profile):
    return profile or "main"


def _pump(proc, tag, width, log, lock):
    """Stream one worker's output, tagged, to the console and the log file."""
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        with lock:
            print(f"{tag:<{width}} | {line}", flush=True)
            log.write(f"{tag} | {line}\n")
            log.flush()


def main():
    ap = argparse.ArgumentParser(
        description="Run the whole enrichment across every profile with one command.")
    ap.add_argument("--start", type=int, required=True,
                    help="first CSV data row to work on (1-based, no header)")
    ap.add_argument("--count", type=int, required=True,
                    help="how many rows from --start to cover")
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                    help=f"tabs in flight per profile (default {DEFAULT_CONCURRENCY})")
    ap.add_argument("--delay", type=float, default=8.0,
                    help="seconds between starting leads within each profile "
                         "(default 8). Raise it if Wiza reports fair-use limits.")
    ap.add_argument("--profiles", default=None,
                    help="comma-separated profile names (default: all set up)")
    ap.add_argument("--no-sales-nav", default="", metavar="a2,a3",
                    help="profiles with NO Sales Navigator seat — they open the "
                         "plain /in/ profile instead of the /sales/lead/ page")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit without touching LinkedIn")
    ap.add_argument("--no-merge", action="store_true",
                    help="skip the merge step at the end")
    ap.add_argument("--verbose", action="store_true",
                    help="per-lead poll trace from every worker")
    args = ap.parse_args()

    if args.count < 1:
        ap.error("--count must be at least 1")

    if args.profiles:
        profiles = [None if p.strip() in ("", "main") else p.strip()
                    for p in args.profiles.split(",")]
    else:
        profiles = discover_profiles()
    if not profiles:
        print("No Chrome profile is set up yet. Create one with:\n"
              "    python -m wiza.browser")
        return 1

    plain = {s.strip() for s in args.no_sales_nav.split(",") if s.strip()}

    n = len(profiles)
    start, end = args.start, args.start + args.count - 1

    print(f"Rows {start}–{end} ({args.count}) across {n} profile(s), "
          f"{args.concurrency} tab(s) each — up to {n * args.concurrency} leads at once.")
    for i, p in enumerate(profiles, 1):
        how = "/in/ profile" if _label(p) in plain else "sales nav"
        print(f"  {_label(p):<10} shard {i}/{n}  {how:<13} ->  "
              f"{config.output_csv(p or f'shard{i}of{n}').name}")

    if args.dry_run:
        print("\nDry run — nothing launched.")
        return 0

    log_dir = config.ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = log_dir / f"run-{stamp}.log"
    print(f"\nLogging to {log_path}\n")

    width = max(len(_label(p)) for p in profiles)
    lock = threading.Lock()
    procs, threads = [], []
    t_start = time.time()

    with open(log_path, "w", encoding="utf-8") as log:
        log.write(f"rows {start}-{end} across {profiles} "
                  f"concurrency={args.concurrency}\n")
        try:
            for i, p in enumerate(profiles, 1):
                cmd = [sys.executable, "-m", "wiza.run",
                       "--shard", f"{i}/{n}",
                       "--start-row", str(start), "--end-row", str(end),
                       "--concurrency", str(args.concurrency),
                       "--delay", str(args.delay),
                       "--limit", str(args.count)]
                if p:
                    cmd += ["--profile", p]
                if _label(p) in plain:
                    cmd.append("--no-sales-nav")
                if args.verbose:
                    cmd.append("--verbose")
                proc = subprocess.Popen(
                    cmd, cwd=config.ROOT, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1)
                procs.append(proc)
                t = threading.Thread(target=_pump,
                                     args=(proc, _label(p), width, log, lock),
                                     daemon=True)
                t.start()
                threads.append(t)
                if i < n:
                    time.sleep(LAUNCH_STAGGER)

            for proc in procs:
                proc.wait()
            for t in threads:
                t.join(timeout=5)
        except KeyboardInterrupt:
            print("\nStopping workers… (finished rows are already saved)")
            for proc in procs:
                try:
                    proc.terminate()
                except Exception:
                    pass
            for proc in procs:
                try:
                    proc.wait(timeout=20)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

    mins = (time.time() - t_start) / 60
    print(f"\nAll workers finished in {mins:.1f} min.")

    if args.no_merge:
        print("Skipping merge (--no-merge). Run `python -m wiza.merge` when ready.")
        return 0

    print("Merging worker files back into the main sheet…")
    from . import merge as merge_mod
    files = merge_mod.worker_files()
    if files:
        merge_mod.merge(files)
    else:
        print("  (no worker files found)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
