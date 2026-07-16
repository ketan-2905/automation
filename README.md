# Wiza + Sales Navigator CSV Enrichment

Fills missing `email one`, `email 2`, and `phone` in **`master - gyms.csv`** by
opening each lead's Sales Navigator profile, reading the **Wiza** panel that
auto-loads there, and writing the values back — slowly and resumably, so your
LinkedIn account stays safe.

It drives a **dedicated Chrome profile** (logged into LinkedIn + Wiza once) and
reads the panel over Chrome's DevTools **DOM protocol across every frame** — no
automation flags on the browser, so Wiza's anti-bot treats it as a normal
window. See [wiza/cdp.py](wiza/cdp.py) for the why.

Full design: [docs/superpowers/specs/2026-07-15-wiza-sales-nav-enrichment-design.md](docs/superpowers/specs/2026-07-15-wiza-sales-nav-enrichment-design.md)

---

## How it works

1. Reads the CSV, finds rows with **no** email and **no** phone (~2,366 of 2,500).
2. For each, opens its `profileUrl` in a dedicated, logged-in Chrome (Wiza
   installed). The panel auto-loads — no clicking.
3. Scrapes the panel: first **2 emails → `email one`, `email 2`**; first
   **phone → `phone`**.
4. Writes to **`master - gyms.enriched.csv`** (your original is never touched;
   a backup is saved under `backups/`).
5. Tracks a `wiza_status` column, so you can stop/restart across days and it
   resumes where it left off.

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

## Running

```powershell
# small smoke test — 3 profiles, no writes
python -m wiza.run --limit 3 --dry-run

# a real daily batch (default cap = 150)
python -m wiza.run

# bigger/smaller batch
python -m wiza.run --daily-cap 200
```

Each profile takes ~10–20s to read the panel, plus a random **10–25s** pause
(with occasional longer breathers). 150 profiles ≈ 1–2 hrs. Run it once a day;
over ~16 days it clears all 2,366. Results are flushed to the CSV after
**every** profile, so a crash or a stop never loses progress — just run it again.
Add `--verbose` to watch the per-profile poll trace.

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
  config.py       paths, columns, profile, pacing/safety knobs
  wiza_panel.py   panel parsing (the tested core)
  cdp.py          the scraper: normal Chrome + DOM-domain read across frames
  csv_store.py    load / filter / safe write-back + resume
  browser.py      one-time profile setup (python -m wiza.browser)
  run.py          the main loop
tests/            unit tests + fixture
master - gyms.csv             your input (never modified)
master - gyms.enriched.csv    the output (created on first run)
backups/                      timestamped copy of the original

%LOCALAPPDATA%\wiza-automation\chrome-profile\   the dedicated profile (NOT in OneDrive)
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
