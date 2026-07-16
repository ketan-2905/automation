"""Clone your existing Chrome profile into a standalone one for automation.

    python -m wiza.clone_profile

Why: Chrome locks its whole user-data directory, so driving your real profile
(Profile 8) means no Chrome at all during a run. A standalone profile has its
own lock, so automation and your normal browsing can run side by side.

What it copies: config.CHROME_PROFILE_DIRECTORY -> config.PROFILE_DIR/Default,
plus the user-data-level `Local State` (holds the key that decrypts cookies, so
the LinkedIn session and Wiza sign-in carry over). Caches are skipped.

SECURITY — read this:
  * A Chrome profile contains cookies and saved logins for EVERY site signed
    into it, not just LinkedIn. The clone is a second copy of all of that.
  * That's why the destination is under %LOCALAPPDATA% and NOT in this project
    folder — the project lives in OneDrive and would sync those credentials to
    the cloud.
  * Delete the clone when you're done:  python -m wiza.clone_profile --delete
  * If the source profile's password is changed / sessions revoked, the clone's
    sessions die too (that's a good thing).

Chrome must be fully closed while copying, or the files will be locked/torn.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

from . import browser, config

# Directories that are pure cache — skipping them keeps the copy small and fast.
SKIP_DIRS = {
    "cache", "code cache", "gpucache", "media cache", "dawncache",
    "graphitedawncache", "cachestorage", "component_crx_cache",
    "extensions_crx_cache", "optimization_guide_model_store",
    "optimization_guide_prediction_model_downloads", "crashpad",
    "shadercache", "grshadercache", "segmentation_platform",
}


def _ignore(dirpath, names):
    """shutil.copytree ignore fn: skip cache dirs and lock files."""
    skipped = []
    for n in names:
        full = os.path.join(dirpath, n)
        if os.path.isdir(full) and n.lower() in SKIP_DIRS:
            skipped.append(n)
        elif n.lower() in {"lockfile", "singletonlock", "singletoncookie", "singletonsocket"}:
            skipped.append(n)
    return set(skipped)


def delete_clone():
    if config.PROFILE_DIR.exists():
        shutil.rmtree(config.PROFILE_DIR, ignore_errors=True)
        print("Deleted clone:", config.PROFILE_DIR)
    else:
        print("Nothing to delete at:", config.PROFILE_DIR)


def clone():
    src_udd = config.CHROME_USER_DATA_DIR
    src_profile = src_udd / config.CHROME_PROFILE_DIRECTORY
    dest_udd = config.PROFILE_DIR
    dest_profile = dest_udd / "Default"

    if not src_profile.is_dir():
        sys.exit(f"Source profile not found: {src_profile}\nRun: python -m wiza.profiles")

    if browser.chrome_is_running():
        sys.exit(
            "Chrome is running — its files are locked and the copy would be corrupt.\n"
            "Quit Chrome completely (check Task Manager for chrome.exe), then re-run.\n"
            "This is the ONLY time you need Chrome closed; after this, runs work "
            "alongside your normal browsing."
        )

    print(f"Source : {src_profile}")
    print(f"Dest   : {dest_profile}")
    print("(destination is outside OneDrive, so credentials don't sync to the cloud)")
    print()

    if dest_udd.exists():
        print("Removing previous clone...")
        shutil.rmtree(dest_udd, ignore_errors=True)
    dest_profile.mkdir(parents=True, exist_ok=True)

    print("Copying profile (skipping caches)... this can take a minute.")
    shutil.copytree(src_profile, dest_profile, ignore=_ignore, dirs_exist_ok=True)

    # Local State lives at the user-data-dir level and holds the DPAPI-wrapped
    # key used to decrypt Cookies/Login Data. Without it the session won't work.
    src_ls = src_udd / "Local State"
    if src_ls.exists():
        shutil.copy2(src_ls, dest_udd / "Local State")
        print("Copied Local State (cookie decryption key).")

    size = sum(
        os.path.getsize(os.path.join(r, f))
        for r, _, fs in os.walk(dest_udd) for f in fs
        if os.path.exists(os.path.join(r, f))
    )
    print(f"\nDone. Clone size: {size/1e6:.0f} MB")
    print("\nNext:")
    print("  1. Set USE_EXISTING_CHROME_PROFILE = False in wiza/config.py")
    print("  2. python -m wiza.calibrate     (Chrome may stay open now)")
    print("  3. python -m wiza.run --limit 3 --dry-run")
    print("\nWhen finished with the project:  python -m wiza.clone_profile --delete")


def main():
    ap = argparse.ArgumentParser(description="Clone a Chrome profile for automation.")
    ap.add_argument("--delete", action="store_true", help="delete the clone and exit")
    args = ap.parse_args()
    if args.delete:
        delete_clone()
    else:
        clone()


if __name__ == "__main__":
    main()
