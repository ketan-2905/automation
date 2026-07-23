"""Launch a persistent, authenticated Chrome context via Playwright.

By default (config.USE_EXISTING_CHROME_PROFILE = True) this drives your REAL
Chrome profile — the one already logged into Sales Navigator with the Wiza
extension installed — so no separate login/setup is needed.

IMPORTANT: Chrome locks its user-data directory. You must fully quit Chrome
(all windows, all profiles) before a run, and not reopen it until the run ends.

Set USE_EXISTING_CHROME_PROFILE = False to use a dedicated profile instead
(`python -m wiza.browser` logs into it once).
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import config

_CHROME_RUNNING_MSG = """
Chrome is currently running, so its profile is locked and this run can't start.

  1. Save your work and QUIT Chrome completely (close every window).
  2. Check Task Manager -> no `chrome.exe` remaining (Chrome can linger in the
     background; if so, End Task on it, or disable
     Settings -> System -> "Continue running background apps").
  3. Re-run this command, and don't open Chrome until the run finishes.

Tip: `python -m wiza.run --daily-cap 50` keeps each session shorter.
"""

_DEFAULT_DIR_WARNING = """
Driving your REAL Chrome profile in place does not work on Chrome 136+.

Chrome refuses to expose its debugging interface when running on its DEFAULT
user-data directory — a deliberate anti-cookie-theft measure — so Playwright
waits forever and the launch hangs.

Use the standalone profile instead (this also lets you keep browsing normally
while a run is going):

    python -m wiza.clone_profile        # copy your profile (Chrome closed)
      -- or --
    python -m wiza.browser              # fresh login, no credential copy

then set USE_EXISTING_CHROME_PROFILE = False in wiza/config.py
"""

_NO_PROFILE_MSG = """
No standalone Chrome profile found yet.

Create one (either is fine):

    python -m wiza.clone_profile   # clone your existing profile (Chrome closed)
    python -m wiza.browser         # fresh profile, log into LinkedIn + Wiza once
"""


def chrome_is_running() -> bool:
    """True if any chrome.exe process is alive (Windows)."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH"],
            capture_output=True, text=True, timeout=15,
        ).stdout.lower()
        return "chrome.exe" in out
    except Exception:
        return False  # can't tell — let Playwright surface any real problem


_CHROME_EXE_CANDIDATES = [
    Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
    Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe",
]

_NO_CHROME_EXE_MSG = """
Couldn't find chrome.exe in the usual locations. Install Google Chrome, or set
CHROME_EXE in wiza/config.py to its full path.
"""


def _chrome_exe() -> str:
    override = getattr(config, "CHROME_EXE", None)
    if override and Path(override).exists():
        return str(override)
    for c in _CHROME_EXE_CANDIDATES:
        if c and c.exists():
            return str(c)
    raise RuntimeError(_NO_CHROME_EXE_MSG)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# Handle to the Chrome we launch ourselves, so stop() can shut it down.
_CHROME_PROC = None


def launch(headless=False, check_chrome=True):
    """Launch a NORMAL Chrome ourselves and attach Playwright to it over CDP.

    Why not launch_persistent_context? Playwright's own Chrome launch carries
    automation flags (--no-sandbox, --enable-automation, a big --disable-features
    list, etc.). Wiza's backend / Cloudflare refuse that browser, so the panel
    never fills in. By starting the exact same chrome.exe a human would (only
    adding a debugging port) and connecting to it, Wiza sees a normal browser
    and works — while Playwright can still drive the page. Returns (pw, ctx, page).
    """
    global _CHROME_PROC

    if config.USE_EXISTING_CHROME_PROFILE:
        # Legacy in-place path — doesn't work on Chrome 136+. Kept for docs.
        raise RuntimeError(_DEFAULT_DIR_WARNING)

    if not config.PROFILE_DIR.exists():
        raise RuntimeError(_NO_PROFILE_MSG)

    port = _free_port()
    chrome_args = [
        _chrome_exe(),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={config.PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--hide-crash-restore-bubble",
        # Hide the navigator.webdriver tell without adding any automation flags.
        "--disable-blink-features=AutomationControlled",
    ]
    if headless:
        chrome_args.append("--headless=new")

    _CHROME_PROC = subprocess.Popen(
        chrome_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    pw = sync_playwright().start()
    endpoint = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    browser = None
    last_err = None
    while time.time() < deadline:
        try:
            browser = pw.chromium.connect_over_cdp(endpoint)
            break
        except Exception as e:
            last_err = e
            time.sleep(0.5)

    if browser is None:
        pw.stop()
        _kill_chrome()
        raise RuntimeError(f"Couldn't attach to Chrome on {endpoint}: {last_err}")

    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    ctx.set_default_navigation_timeout(config.NAV_TIMEOUT_MS)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    return pw, ctx, page


def _kill_chrome():
    global _CHROME_PROC
    if _CHROME_PROC is not None:
        try:
            _CHROME_PROC.terminate()
        except Exception:
            pass
        _CHROME_PROC = None


WIZA_EXT_ID = "pjmlkdacmaejhkdcflncbpcpidkggoio"  # Wiza - Phone Number & Email Finder


def loaded_extension_ids(ctx):
    """Extension IDs Chrome actually loaded (via their background workers/pages).

    Wiza is MV3, so it shows up as a service worker. Empty result usually means
    extensions were disabled at launch.
    """
    ids = set()
    for group in (getattr(ctx, "service_workers", []), getattr(ctx, "background_pages", [])):
        for item in group:
            url = getattr(item, "url", "") or ""
            if url.startswith("chrome-extension://"):
                ids.add(url.split("/")[2])
    return ids


def wiza_is_loaded(ctx, wait_seconds=8):
    """Poll briefly — the extension's worker can take a moment to spin up."""
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if WIZA_EXT_ID in loaded_extension_ids(ctx):
            return True
        time.sleep(0.5)
    return False


def stop(pw, ctx):
    try:
        browser = ctx.browser
        if browser is not None:
            browser.close()   # disconnect + close the Chrome we attached to
    except Exception:
        pass
    finally:
        try:
            pw.stop()
        finally:
            _kill_chrome()   # backstop in case the browser didn't exit


def setup():
    """Only needed when USE_EXISTING_CHROME_PROFILE = False.

    Opens the dedicated profile so you can log into LinkedIn + Wiza once.
    """
    if config.USE_EXISTING_CHROME_PROFILE:
        print("USE_EXISTING_CHROME_PROFILE is True — no setup needed.")
        print(f"Using your real profile: {config.CHROME_PROFILE_DIRECTORY}")
        print("Verify it with:  python -m wiza.calibrate")
        return

    print("Opening a SEPARATE Chrome profile at:", config.PROFILE_DIR)
    print("(Your normal Chrome can stay open — this one has its own lock.)")
    print()
    print("In the window that opens:")
    print("  1) Log into LinkedIn + Sales Navigator.")
    print("  2) Install the Wiza extension from the Chrome Web Store, sign into Wiza.")
    print("  3) Open any sales/lead profile and check the contact panel loads.")
    print("  4) Close the window when done — the login is saved for future runs.")
    print()
    # Create the profile dir up front — launch() otherwise refuses to start when
    # it doesn't exist yet, but creating it is the whole point of setup().
    config.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    pw, ctx, page = launch(headless=False, check_chrome=False)
    try:
        page.goto("https://www.linkedin.com/sales/", wait_until="domcontentloaded")
    except Exception:
        pass

    closed = {"v": False}
    ctx.on("close", lambda *_: closed.__setitem__("v", True))
    print("Waiting for you to finish... (close the Chrome window when done)")
    try:
        while not closed["v"]:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        try:
            pw.stop()
        except Exception:
            pass
    print("Setup complete. Now set USE_EXISTING_CHROME_PROFILE = False in wiza/config.py")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Open a dedicated Chrome profile so you can log into "
                    "LinkedIn + Wiza once. Use --profile to keep several "
                    "accounts side by side.")
    ap.add_argument("--profile", default=None,
                    help="name for this profile, e.g. a2 (default: the original)")
    a = ap.parse_args()
    if a.profile:
        # Point the module at this named profile for the duration of setup.
        config.PROFILE_DIR = config.profile_dir(a.profile)
    setup()
