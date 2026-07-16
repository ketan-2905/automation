"""One-off checker: confirm the Wiza extension loads and the panel is scraped.

    python -m wiza.calibrate
    python -m wiza.calibrate "https://www.linkedin.com/sales/lead/....."

Without a URL it opens the browser and waits for YOU to navigate to a lead
profile, then looks for the panel when you press Enter. It reports whether the
Wiza extension actually loaded, where the panel lives (main page vs iframe),
dumps the panel HTML to tests/fixtures/, and prints what it extracted.
"""
from __future__ import annotations

import sys

from . import browser, config, wiza_panel

SAMPLE_URL = "https://www.linkedin.com/sales/lead/ACwAABv4_WUBUaucxoUMUOKk2a2vD67JEo8J4YY,NAME_SEARCH,05TH"


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else None

    pw, ctx, page = browser.launch(headless=False, check_chrome=False)
    try:
        # 1. Get to a lead page FIRST. Wiza is Manifest V3, so its service
        #    worker stays dormant until a relevant page loads — checking for it
        #    on about:blank gives a meaningless "not loaded".
        target = url or SAMPLE_URL
        print("Opening:", target[:90])
        try:
            page.goto(target, wait_until="domcontentloaded")
        except Exception as e:
            print("  Navigation problem:", str(e)[:160])
            print("  Navigate to a sales/lead profile manually in the window.")

        if wiza_panel.is_blocked(page):
            print("\nLinkedIn is showing a login/checkpoint page.")
            print("Log into Sales Navigator in this window first.")

        print()
        print("In the browser window: confirm you're on a sales/lead profile and")
        print("the Wiza panel is showing emails/phones on the right.")
        input("Then press Enter here to scrape it... ")

        # 2. Now the extension check is meaningful.
        print("\nChecking the Wiza extension...")
        if browser.wiza_is_loaded(ctx, wait_seconds=5):
            print("  OK — Wiza extension is loaded and running.")
        else:
            ids = browser.loaded_extension_ids(ctx)
            print("  Wiza's service worker isn't running right now.")
            print(f"  Extensions seen: {sorted(ids) if ids else 'none'}")
            print("  (MV3 workers sleep when idle — this is only a problem if")
            print("   the panel below also fails to appear.)")

        # 3. Find and read the panel.
        print("\nLooking for the Wiza panel...")
        root = wiza_panel.wait_for_panel(page, config.PANEL_TIMEOUT_MS)
        if root is None:
            print("Panel NOT found. Checklist:")
            print("  - Are you on a linkedin.com/sales/lead/... page?")
            print("  - Is the Wiza side panel visible with emails/phones?")
            print("  - Is Wiza signed in (click its toolbar icon)?")
        else:
            where = "main page" if root is page else "iframe"
            print(f"Panel found in: {where}")
            el = root.query_selector(config.PANEL_SELECTOR)
            if el is not None:
                config.FIXTURE_DUMP.mkdir(parents=True, exist_ok=True)
                out = config.FIXTURE_DUMP / "wiza_panel_live.html"
                out.write_text(el.inner_html(), encoding="utf-8")
                print("Saved panel HTML to:", out)
            result = wiza_panel.extract_from_root(root)
            print("\n--- EXTRACTED ---")
            print("emails:", result["emails"])
            print("phones:", result["phones"])
            print("\nWould write ->")
            for col, val in zip(config.EMAIL_COLUMNS, result["emails"]):
                print(f"  {col}: {val}")
            for col, val in zip(config.PHONE_COLUMNS, result["phones"]):
                print(f"  {col}: {val}")

        input("\nPress Enter to close the browser...")
    finally:
        browser.stop(pw, ctx)


if __name__ == "__main__":
    main()
