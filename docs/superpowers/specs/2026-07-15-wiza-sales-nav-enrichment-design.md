# Wiza + Sales Navigator CSV Enrichment — Design

**Date:** 2026-07-15
**Status:** Draft for review

## Problem

We have `master - gyms.csv` — 2,500 lead rows exported from a LinkedIn Sales
Navigator search. Each row has a `profileUrl` (a `linkedin.com/sales/lead/...`
URL). About **2,366 rows have no contact info** (no email, no phone).

For each of those profiles, the **Wiza** browser extension — already installed
and signed in on a dedicated Chrome profile — injects a contact panel into the
Sales Navigator lead page and **auto-loads** the person's emails and phone
numbers (the account has unlimited email + phone credits). No button click is
required; the data is present as soon as the lead page finishes loading.

We want to automate: open each profile → read the Wiza panel → write the
emails/phones back into that CSV row.

## Goal & Non-Goals

**Goal:** Fill `email one`, `email 2`, and `phone` for rows currently missing
contact info, by reading the Wiza panel, safely and resumably.

**Non-goals:**
- Do NOT capture anything beyond the mapped fields (no phone-type labels, no
  validity flags, no extra emails past the second).
- Do NOT touch rows that already have contact info.
- No Wiza API (we only have the extension).
- No fast/parallel scraping — this is deliberately slow to protect the account.

## Field Mapping

The CSV columns are: `email one`, `email 2`, `phone` (2 email columns, 1 phone
column). Per profile the Wiza panel lists N emails and M phones. We map:

| Panel value      | CSV column  |
|------------------|-------------|
| emails[0]        | `email one` |
| emails[1]        | `email 2`   |
| phones[0]        | `phone`     |

Extra emails/phones beyond these are ignored. If a profile has only one email,
`email 2` stays empty; if no phone, `phone` stays empty.

> One-line switch: a `FIELD_MAP` config makes it trivial to flip to
> "2 phones + 1 email" if that was the intent instead.

## The Wiza Panel — Confirmed Structure

Captured live from a real lead page. The panel is Vue-rendered markup:

- Root container: `div.reveal-form` → `div.prospect-info`
- Two sections, each with a header `<p>` whose text is `" Emails"` or
  `" Phone numbers"`, followed by a `div.space-y-2` of `<button>` rows.
- **Every contact value lives in `label.cursor-pointer`** — e.g.
  `jesse@leonardleadership.com`, `+1 (432) 254-8467`.
- Phone rows nest an outer `<label>` (icon + "Mobile Number"/"Landline"
  tooltip) around the inner `label.cursor-pointer` holding the number.
- Vue scope hashes (`data-v-451687f4`, `data-v-005ea263`) are NOT relied on —
  they change between extension versions.

**Extraction rule (order-independent, resilient):** collect the text of every
`label.cursor-pointer` inside `div.prospect-info`, then classify each string:

- matches email regex → email
- matches phone regex (`+`/digits with separators) → phone

This survives section reordering and markup churn better than positional
selectors. A saved copy of this HTML becomes a **test fixture** so the parser is
unit-tested against real markup.

**Open question resolved during setup:** whether the panel renders in the main
document, a shadow DOM, or an iframe. A one-shot `inspect` command determines
this once; the extractor then targets the right frame/root. (From the captured
HTML it appears to be injected DOM, not a cross-origin iframe.)

## Approach

**A — Playwright driving a dedicated Chrome profile** (chosen).

A Python + Playwright script launches real Chrome (`channel="chrome"`,
headed) against a **dedicated user-data directory** that has LinkedIn/Sales
Navigator logged in and the Wiza extension installed & signed in. Extensions
require a persistent context and headed mode, which this satisfies.

Rejected: Wiza API (not available), Tampermonkey userscript (safer but
semi-manual — kept as a documented fallback if the account gets flagged).

## Components (small, single-purpose modules)

1. **`browser.py`** — launches the persistent Chrome context with
   anti-automation flags (`--disable-blink-features=AutomationControlled`,
   realistic UA/viewport). Provides a `setup` entry point: opens the browser so
   the user logs into LinkedIn + Wiza once; that state persists in the profile
   dir for all later runs.
   - *What it does:* give the rest of the app a ready, authenticated page.
   - *Depends on:* Playwright, a profile directory path.

2. **`wiza_panel.py`** — pure extraction. Input: page/frame HTML or a Playwright
   locator root. Output: `{"emails": [...], "phones": [...]}`. Contains the
   selector + regex logic and a `wait_for_panel()` helper.
   - *Unit-testable core* — tested against the saved real-HTML fixture, no
     browser needed.
   - *Depends on:* nothing but the HTML string / locator.

3. **`csv_store.py`** — load rows, select targets (missing contact info), write
   results back safely. Writes to a working copy (`*.enriched.csv`) and keeps a
   timestamped backup of the original; never edits `master - gyms.csv` in place.
   Adds a `wiza_status` column (`pending`/`done`/`not_found`/`error`) so reruns
   skip finished rows (resumability).
   - *Depends on:* Python `csv`/`pandas`, file paths.

4. **`run.py`** — orchestrator. Loops target rows: navigate → `wait_for_panel`
   → extract → map → save. Enforces pacing and safety (below). Flushes to disk
   every row so a crash never loses progress. CLI flags for `--limit`,
   `--daily-cap`, `--dry-run`, `--start-at`.
   - *Depends on:* the three modules above.

5. **`calibrate.py` / `inspect` command** — one-off helper: open a single known
   profile, detect where the panel lives (document/shadow/iframe), dump its HTML
   to `tests/fixtures/`, and confirm the extractor returns the expected values.
   Run once before the first bulk run and any time Wiza changes its UI.

## Data Flow

```
master - gyms.csv
      │  csv_store.load() → target rows (missing contact info)
      ▼
run.py loop ──▶ browser.page.goto(profileUrl)
      │               │ wiza_panel.wait_for_panel(page)
      │               ▼
      │        wiza_panel.extract() → {emails, phones}
      │               │ map → email one / email 2 / phone
      ▼               ▼
csv_store.write_row(status) ──▶ master - gyms.enriched.csv  (+ backup)
```

## Safety / Anti-Ban (the whole ballgame)

LinkedIn actively detects automation; violating ToS risks account restriction.
Mitigations, all configurable:

- Randomized **10–25s** delay between profiles; occasional longer pauses.
- **Daily cap** (default **150**) so 2,366 profiles spread over ~16 sessions.
- Human-like touches: small scroll on each page, non-headless real Chrome.
- **Block detection:** if a page shows a LinkedIn "unusual activity" /
  checkpoint / login wall instead of a lead page, the run **stops immediately**
  and records where it stopped.
- Resume-from-last via `wiza_status`, so a stop is never lost progress.

The user accepts the residual account risk; the tool minimizes but cannot
eliminate it.

## Error Handling

| Situation                                   | Behavior                                    |
|---------------------------------------------|---------------------------------------------|
| Panel never loads within timeout            | mark row `not_found`, continue              |
| Panel loads but no emails/phones            | mark `not_found`, continue                  |
| Navigation error / timeout                  | mark `error`, continue (retry on next run)  |
| LinkedIn checkpoint / login wall detected   | **halt run**, log last row                  |
| Chrome profile already open elsewhere       | fail fast with a clear message              |

Every row result is flushed to disk immediately.

## Testing

- **`wiza_panel`**: unit tests feed the saved real HTML fixture and assert the
  exact emails/phones and the correct empty cases (one email, no phone, empty
  panel). This is the highest-value, fully-deterministic test.
- **`csv_store`**: unit tests for target selection, status tracking,
  backup/copy behavior, and correct field mapping into `email one`/`email 2`/
  `phone` without corrupting other columns (embedded newlines, UTF-8).
- **Browser driving** (`browser`, `run`): verified manually via the `inspect`
  command and a small `--limit 3 --dry-run` smoke run before any full run.

## Assumptions

- The dedicated Chrome profile stays logged into LinkedIn + Wiza with credits.
- `profileUrl` values are valid `sales/lead/...` URLs (they are in the sample).
- The user does not browse in the automation profile while a run is active.
- Python 3.11+ available on the Windows machine; Playwright + Chrome installed.

## Deliverables

- `browser.py`, `wiza_panel.py`, `csv_store.py`, `run.py`, `calibrate.py`
- `tests/` with the HTML fixture and unit tests
- `requirements.txt`, `README.md` (setup + run instructions, safety notes)
