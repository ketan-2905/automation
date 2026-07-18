"""Central configuration: paths, column names, pacing/safety knobs."""
import sys
from pathlib import Path

# Project root = the folder that contains this "wiza" package.
ROOT = Path(__file__).resolve().parent.parent

# --- Files ---
CSV_INPUT = ROOT / "master - gyms.csv"            # never written to
CSV_OUTPUT = ROOT / "master - gyms.enriched.csv"  # working copy we update
BACKUP_DIR = ROOT / "backups"
FIXTURE_DUMP = ROOT / "tests" / "fixtures"

# --- Which Chrome profile to drive ---
# This project uses a dedicated, standalone Chrome profile at PROFILE_DIR that
# you log into once via `python -m wiza.browser` (LinkedIn + Wiza extension).
# It has its own lock, so you can keep browsing in normal Chrome during a run.
#
# True  = drive a real Chrome profile in place. DOESN'T WORK on Chrome 136+:
#         Chrome refuses remote debugging on its DEFAULT user-data dir (an
#         anti-cookie-theft measure), so Playwright hangs forever on launch.
#         Left here only for documentation — keep this False.
USE_EXISTING_CHROME_PROFILE = False

# Only used if USE_EXISTING_CHROME_PROFILE = True (not recommended; see above).
# Run `python -m wiza.profiles` to list local Chrome profiles.
CHROME_USER_DATA_DIR = Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
CHROME_PROFILE_DIRECTORY = "Default"

# Standalone profile used when USE_EXISTING_CHROME_PROFILE = False.
# Deliberately OUTSIDE the project folder: a Chrome profile holds cookies/login
# data for every site signed into it, so it must never sync to any cloud folder.
# AppData / Application Support are local-only. Created on first `python -m wiza.browser`.
if sys.platform == "darwin":
    PROFILE_DIR = Path.home() / "Library" / "Application Support" / "wiza-automation" / "chrome-profile"
    CHROME_EXE = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
else:
    PROFILE_DIR = Path.home() / "AppData" / "Local" / "wiza-automation" / "chrome-profile"

# --- CSV columns ---
COL_URL = "profileUrl"
COL_NAME = "fullName"
COL_EMAIL1 = "email one"
COL_EMAIL2 = "email 2"
COL_PHONE = "phone"
COL_STATUS = "wiza_status"   # added by us: '', done, not_found, error

# Field mapping: first N emails -> these columns, first M phones -> these.
# Flip these two lists if you meant "2 phones + 1 email".
EMAIL_COLUMNS = [COL_EMAIL1, COL_EMAIL2]
PHONE_COLUMNS = [COL_PHONE]

# --- Wiza panel selectors (structure/text based, not Vue hashes) ---
PANEL_SELECTOR = ".prospect-info"
VALUE_SELECTOR = ".prospect-info label.cursor-pointer"

# --- Pacing / anti-ban (all tunable) ---
MIN_DELAY = 10           # seconds between profiles (lower bound)
MAX_DELAY = 25           # seconds between profiles (upper bound)
DAILY_CAP = 150          # max profiles per run by default
LONG_PAUSE_EVERY = 40    # every N profiles, take a longer breather
LONG_PAUSE = (60, 120)   # seconds range for the long breather

# --- Timeouts (ms) ---
PANEL_TIMEOUT_MS = 25000
NAV_TIMEOUT_MS = 45000
