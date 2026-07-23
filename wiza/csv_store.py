"""Load / filter / write the leads CSV safely and resumably.

We never edit `master - gyms.csv` in place. On first run we back it up and
create `master - gyms.enriched.csv` (with an added `wiza_status` column); all
updates go there. A row is a target if it has no contact info and hasn't been
processed yet, so reruns skip finished rows.
"""
from __future__ import annotations

import datetime as dt
import shutil

import pandas as pd

from . import config


def _load(path):
    # keep everything as strings; blanks stay '' (not NaN) for clean checks/writes
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")


def prepare(out_path=None):
    """Ensure the working copy at `out_path` exists (+ a one-time backup).

    A parallel worker gets its own file so workers never overwrite each other,
    but it's SEEDED from the shared enriched sheet — so it still sees every row
    already finished and skips them. `python -m wiza.merge` folds the workers'
    files back into the shared one.
    """
    out = out_path or config.CSV_OUTPUT
    config.BACKUP_DIR.mkdir(exist_ok=True)
    if not out.exists():
        src = config.CSV_OUTPUT if config.CSV_OUTPUT.exists() else config.CSV_INPUT
        df = _load(src)
        if src == config.CSV_INPUT:
            ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(config.CSV_INPUT, config.BACKUP_DIR / f"master - gyms.{ts}.csv")
        if config.COL_STATUS not in df.columns:
            df[config.COL_STATUS] = ""
        df.to_csv(out, index=False, encoding="utf-8")
    df = _load(out)
    if config.COL_STATUS not in df.columns:
        df[config.COL_STATUS] = ""
    return df


def is_missing(row) -> bool:
    """True if the row has no email and no phone yet."""
    e1 = str(row.get(config.COL_EMAIL1, "")).strip()
    e2 = str(row.get(config.COL_EMAIL2, "")).strip()
    ph = str(row.get(config.COL_PHONE, "")).strip()
    return not (e1 or e2 or ph)


def targets(df):
    """Row indices still needing enrichment (missing contact + not yet processed)."""
    finished = {"done", "not_found"}
    idxs = []
    for i, row in df.iterrows():
        status = str(row.get(config.COL_STATUS, "")).strip().lower()
        if status in finished:
            continue
        if is_missing(row):
            idxs.append(i)
    return idxs


def apply_result(df, idx, emails, phones):
    """Write first N emails / first M phones into the mapped columns."""
    for col, val in zip(config.EMAIL_COLUMNS, emails):
        df.at[idx, col] = val
    for col, val in zip(config.PHONE_COLUMNS, phones):
        df.at[idx, col] = val
    df.at[idx, config.COL_STATUS] = "done" if (emails or phones) else "not_found"


def mark(df, idx, status):
    df.at[idx, config.COL_STATUS] = status


def save(df, out_path=None):
    df.to_csv(out_path or config.CSV_OUTPUT, index=False, encoding="utf-8")
