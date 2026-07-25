# Wiza + LinkedIn CSV Enrichment

Fills missing `email 1`, `email 2`, and `phone` in a lead sheet
(**`doctors` / `genomics` / `nutritionist`**, pick with `--dataset`) by opening
each lead's LinkedIn profile, reading the **Wiza** panel there — clicking
**Reveal contact info** and waiting for it to resolve — and writing the values
back, slowly and resumably so your LinkedIn account stays safe.

It drives **dedicated Chrome profiles** (each logged into LinkedIn + Wiza once)
and reads the panel over Chrome's DevTools **DOM protocol across every frame** —
no automation flags on the browser, so Wiza's anti-bot treats it as a normal
window. See [wiza/cdp.py](wiza/cdp.py) for the why. Several accounts can run at
once, each on its own slice of the sheet.

Full design: [docs/superpowers/specs/2026-07-15-wiza-sales-nav-enrichment-design.md](docs/superpowers/specs/2026-07-15-wiza-sales-nav-enrichment-design.md)

---

## How it works

1. Reads the chosen dataset, finds rows **not yet checked** (all value cells
   blank — see the `NF` marker below).
2. For each, opens its profile in a dedicated, logged-in Chrome (Wiza
   installed), in a **background tab** so it never steals window focus.
3. Clicks **Reveal contact info** once, then waits through Wiza's "Finding
   contact data…" until the panel resolves to values *or* "No email/phone found"
   (never a blind timeout).
4. Scrapes the panel: first **2 emails → `email 1`, `email 2`**; first
   **phone → `phone`**. Empty fields on a checked row are stamped **`NF`**.
5. Writes to **`master - <dataset>.enriched.csv`** (your original is never
   touched; a backup is saved under `backups/`), flushing after every lead so a
   stop or crash never loses progress and a rerun resumes where it left off.

---

## Setup on a new computer — do this once

You need: **Python 3.12+**, **Google Chrome**, and a **Wiza account** with
credits + a LinkedIn Sales Navigator seat.

### Step 0 — Get the code + dependencies

```powershell
git clone https://github.com/ketan-2905/automation.git
cd automation
python -m pip install -r requirements.txt
```

> The leads file `master - gyms.csv` is **not** in the repo (it's personal
> data). Copy your own `master - gyms.csv` into this folder before running.

### Why there are two login steps (read this)

Wiza's servers **refuse to serve the panel to any browser that a program
launched or attached a debugger to** — that's the "agent" detection. The trick
this tool uses: **you log in with a completely normal, hand-opened Chrome**, and
that saved session lives in a dedicated profile folder. Later, the tool opens
that *same profile* as a plain Chrome and only reads the finished page over a
low-level channel Wiza doesn't watch. So Wiza only ever sees a human login.

The dedicated profile lives at
`%LOCALAPPDATA%\wiza-automation\chrome-profile` — deliberately outside this
project folder (which may be in OneDrive) so its cookies never sync to a cloud.

### Step 1 — Create the profile + log into LinkedIn

```powershell
python -m wiza.browser
```

A blank Chrome window opens on the dedicated profile. In it: **log into LinkedIn
+ Sales Navigator**, then **close the window**. (This step just needs the
profile to exist and hold your LinkedIn session — LinkedIn doesn't block it.)

### Step 2 — Log into Wiza the *human* way (this is the anti-agent part)

Open the **same profile** with a normal, hand-launched Chrome — **not** through
any script — because Wiza's site blocks automation-launched windows:

```powershell
& "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe" --user-data-dir="$env:LOCALAPPDATA\wiza-automation\chrome-profile"
```

In that window:

1. Go to the **Chrome Web Store** → install **"Wiza - Phone Number & Email
   Finder"**.
2. Go to **`wiza.co/login`** and sign in (use email + password; the Google
   pop-up can hang). Ignore the never-ending tab spinner — what matters is the
   page content changing to your Wiza dashboard.
3. Open any Sales Navigator lead and confirm the **Wiza panel fills in** with
   emails/phones.
4. **Close the window.** The LinkedIn + Wiza sessions are now saved in the
   profile, permanently, for every future run.

> If Chrome opens on the wrong profile (a person picker appears), pick/confirm
> the only profile shown — it's a fresh single-profile directory.

### Step 3 — Confirm the scraper works

```powershell
python -m wiza.cdp
```

It launches the profile, opens the sample lead, and prints the emails/phones it
extracts (with a per-poll trace). Expect something like
`emails=[...] phones=[...]`. Test a specific lead with
`python -m wiza.cdp "https://www.linkedin.com/sales/lead/....."`.

**Once Step 3 prints contacts, you're set up.** Re-run the login steps only if
LinkedIn or Wiza later signs you out.

---

## Running side by side

Because the tool uses its **own** profile, you can keep browsing in your normal
Chrome while a run goes — they don't share a lock. Just don't open that
*dedicated* profile in another window during a run.

---

## Running — the one command

`wiza.go` is the orchestrator. You give it a **dataset**, a **start row**, and a
**count**; it works out the rest — which profiles are set up, how to split the
rows between them with no overlap, how many tabs each runs, tagged logging, and
merging every worker's results back into the one enriched sheet at the end.

```bash
# process 400 rows of the doctors sheet, one profile, 3 tabs at a time
python -m wiza.go --start 1 --count 400 --dataset doctors --concurrency 3 --delay 10 --profiles main

# see the plan without touching LinkedIn
python -m wiza.go --start 1 --count 400 --dataset doctors --dry-run
```

Finished rows are skipped automatically, so `--start 1 --count <big>` just sweeps
whatever's left — you don't have to track a row number. Results flush after every
lead, so Ctrl+C never loses progress; rerun the same command to resume.

At the end it prints the **start time, end time, and duration** (down to the
second), and the same is written into `logs/run-<stamp>.log`.

### Every flag

| Flag | Meaning | Default |
|------|---------|---------|
| `--dataset` | which sheet: `doctors`, `genomics`, `nutritionist` | `nutritionist` |
| `--start` | first data row (1-based, header not counted) | required |
| `--count` | how many rows from `--start` to cover | required |
| `--concurrency` | leads in flight **per profile** (parallel tabs) | 5 |
| `--delay` | seconds between *starting* leads, per profile — raise if Wiza rate-limits | 8 |
| `--profiles` | comma list, e.g. `main,a2` (default: all set up) | all |
| `--no-sales-nav` | profiles with no Sales Nav seat — open plain `/in/` pages | — |
| `--verbose` | per-lead poll trace from every worker | off |
| `--dry-run` | print the plan and exit | off |
| `--no-merge` | don't merge worker files at the end | off |

> **`--delay` vs `--concurrency`:** concurrency is *how many* run at once; delay
> is *how fast new ones start*. Wiza's fair-use limit measures requests over
> time, so **delay is the lever** for throttling — if you hit the limit, raise
> `--delay` (to 15–20) rather than just lowering concurrency.

### The individual commands

`wiza.go` drives these under the hood; you can also run them directly.

```bash
# ONE profile / dataset (what go spawns per worker)
python -m wiza.run --dataset genomics --start-row 1 --end-row 500 --concurrency 3 --delay 10

# merge worker files into the dataset's main sheet (go does this automatically)
python -m wiza.merge --dataset genomics
python -m wiza.merge --dataset genomics --dry-run   # preview, write nothing

# log a NEW Chrome profile into LinkedIn + Wiza (once per account)
python -m wiza.browser --profile a2

# verify the scraper on a single lead
python -m wiza.cdp "https://www.linkedin.com/in/....."
```

### Several accounts at once

Each account is a separate Chrome profile (`main`, `a2`, `a3`, …), logged in once
with `wiza.browser --profile <name>`. Give `wiza.go` the list and it shards the
rows so no two profiles ever touch the same lead:

```bash
python -m wiza.go --start 1 --count 1000 --dataset doctors \
  --concurrency 3 --delay 10 --profiles main,a2 --no-sales-nav a2
```

Every worker writes its **own** file (`master - doctors.enriched.a2.csv`, …)
because each rewrites the whole sheet on save; `wiza.merge` folds them back into
`master - doctors.enriched.csv` at the end (with a backup, never overwriting a
result with a blank).

### The `NF` marker

When a lead is checked and a field turns up nothing, that cell is stamped **`NF`**
(not found). So a **blank** cell always means "not checked yet" — which is what
lets a run start at any row and safely skip everything already done, whether it
found data or `NF`.

### Keeping the Mac awake

Wrap any command in `caffeinate` so sleep can't interrupt a long run:

```bash
caffeinate -dims python -m wiza.go --start 1 --count 1000 --dataset doctors --profiles main
```

### If LinkedIn gets suspicious

The run **stops itself** the moment it hits a LinkedIn login/checkpoint page.
Wait a day, browse LinkedIn normally for a bit, then resume with a smaller
`--daily-cap`.

---

## Tuning

Edit `wiza/config.py`:

| Setting                  | Meaning                                    |
|--------------------------|--------------------------------------------|
| `PROFILE_DIR`            | the dedicated Chrome profile the tool drives |
| `MIN_DELAY` / `MAX_DELAY`| seconds of pause between profiles          |
| `DAILY_CAP`              | default max profiles per run               |
| `LONG_PAUSE_EVERY`       | take a longer breather every N profiles    |
| `EMAIL_COLUMNS` / `PHONE_COLUMNS` | change the field mapping / order  |

Panel load/settle timing lives in `CdpChrome.scrape(...)` in
[wiza/cdp.py](wiza/cdp.py) (`min_wait`, `settle_window`, `max_wait`).

To switch to **2 phones + 1 email**, set
`EMAIL_COLUMNS = [COL_EMAIL1]` and `PHONE_COLUMNS = [COL_PHONE, ...]` (add a
second phone column name).

---

## Tests

```powershell
python -m pytest -q
```

Covers the panel parser (against a real-markup fixture) and the CSV
target-selection / field-mapping logic. No browser needed.

---

## Files

```
wiza/
  config.py       datasets, columns, profiles, pacing/safety knobs
  wiza_panel.py   panel parsing (the tested core)
  cdp.py          the scraper: normal Chrome + DOM-domain read across frames,
                  reveal-click, concurrent multi-tab, rate-limit detection
  csv_store.py    load / filter / safe write-back + resume + NF marking
  run.py          one profile / dataset (sequential or concurrent)
  go.py           orchestrator: all profiles, sharding, logging, merge, timing
  merge.py        fold worker files back into the dataset's main sheet
  browser.py      one-time profile setup (python -m wiza.browser [--profile N])
tests/            unit tests + fixture

master - doctors.csv            \
master - genomics.csv            > your inputs (never modified)
master - nutritionist.csv       /
master - <dataset>.enriched.csv   the output per dataset (created on first run)
master - <dataset>.enriched.<tag>.csv   per-worker files (merged, then stale)
backups/                          timestamped copies before each risky write
logs/                             run-<stamp>.log per orchestrated run

~/Library/Application Support/wiza-automation/chrome-profile[-<name>]/
                                  the dedicated profiles (macOS; local-only)
```

---

## Important caveats

- Automating Sales Navigator is against LinkedIn's ToS; throttling reduces but
  can't eliminate account risk. Go slow.
- Your normal Chrome can stay open during a run — just don't open the
  *dedicated* `wiza-automation` profile in another window at the same time.
- If Wiza changes its panel markup, re-run `python -m wiza.cdp` to see what it
  now extracts; the parser keys on `<label class="…cursor-pointer">`, adjust in
  [wiza/wiza_panel.py](wiza/wiza_panel.py) if needed.
