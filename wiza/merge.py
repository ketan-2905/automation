"""Fold parallel workers' CSVs back into the shared enriched sheet.

Each worker writes its own file (they'd otherwise overwrite each other, since
every worker rewrites the WHOLE sheet on save). This merges them:

    python -m wiza.merge                 # merge every worker file it finds
    python -m wiza.merge --dry-run       # show what would change, write nothing

A row is taken from a worker file when that worker finished it and the shared
sheet hasn't — so merging is safe to run repeatedly, and never overwrites a
result with a blank.
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil

from . import config, csv_store

_FINISHED = {"done", "not_found", "error"}
_VALUE_COLS = config.EMAIL_COLUMNS + config.PHONE_COLUMNS


def worker_files():
    """Every `master - gyms.enriched.<tag>.csv` sitting next to the main sheet."""
    main = config.CSV_OUTPUT
    return sorted(
        p for p in main.parent.glob(f"{main.stem}.*.csv") if p != main
    )


def _rank(status):
    """Prefer a real answer over a retryable error over nothing at all."""
    s = (status or "").strip().lower()
    return {"done": 3, "not_found": 2, "error": 1}.get(s, 0)


def merge(paths, dry_run=False):
    base = csv_store.prepare()          # the shared sheet
    changed = 0
    per_file = {}

    for path in paths:
        try:
            other = csv_store._load(path)
        except Exception as e:
            print(f"  ! skipping {path.name}: {str(e)[:100]}")
            continue
        n = 0
        for idx in other.index:
            if idx not in base.index:
                continue
            theirs = _rank(other.at[idx, config.COL_STATUS])
            if theirs == 0 or theirs <= _rank(base.at[idx, config.COL_STATUS]):
                continue
            for col in _VALUE_COLS:
                if col in other.columns:
                    base.at[idx, col] = other.at[idx, col]
            base.at[idx, config.COL_STATUS] = other.at[idx, config.COL_STATUS]
            n += 1
        per_file[path.name] = n
        changed += n

    for name, n in per_file.items():
        print(f"  {name}: {n} row(s) merged in")

    if dry_run:
        print(f"\nDry run — {changed} row(s) would change. Nothing written.")
        return changed

    if changed:
        ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        config.BACKUP_DIR.mkdir(exist_ok=True)
        shutil.copy2(config.CSV_OUTPUT,
                     config.BACKUP_DIR / f"{config.CSV_OUTPUT.stem}.premerge-{ts}.csv")
        csv_store.save(base)
    print(f"\n{changed} row(s) merged into {config.CSV_OUTPUT}")
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*", help="worker CSVs (default: auto-detect)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from pathlib import Path
    paths = [Path(f) for f in args.files] if args.files else worker_files()
    if not paths:
        print("No worker CSVs found next to", config.CSV_OUTPUT.name)
        return
    print(f"Merging {len(paths)} worker file(s) into {config.CSV_OUTPUT.name}:")
    merge(paths, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
