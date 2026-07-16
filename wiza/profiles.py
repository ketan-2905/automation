"""List the Chrome profiles on this machine and show which one we'll drive.

    python -m wiza.profiles

Use it to pick the value for CHROME_PROFILE_DIRECTORY in config.py. Profiles
that have the Wiza extension installed are flagged with [WIZA].
"""
from __future__ import annotations

import glob
import json
import os

from . import config

WIZA_EXT_ID = "pjmlkdacmaejhkdcflncbpcpidkggoio"  # Wiza - Phone Number & Email Finder


def has_wiza(user_data_dir, profile_dir) -> bool:
    return os.path.isdir(os.path.join(user_data_dir, profile_dir, "Extensions", WIZA_EXT_ID))


def list_profiles():
    """Return [(dir_name, display_name, email, has_wiza), ...]."""
    udd = str(config.CHROME_USER_DATA_DIR)
    local_state = os.path.join(udd, "Local State")
    if not os.path.exists(local_state):
        return []
    with open(local_state, encoding="utf-8") as f:
        data = json.load(f)
    cache = data.get("profile", {}).get("info_cache", {})
    rows = []
    for dir_name, info in sorted(cache.items()):
        rows.append((
            dir_name,
            info.get("name", ""),
            info.get("user_name", ""),
            has_wiza(udd, dir_name),
        ))
    return rows


def main():
    print("Chrome user data dir:", config.CHROME_USER_DATA_DIR)
    print("Currently configured :", config.CHROME_PROFILE_DIRECTORY)
    print()
    rows = list_profiles()
    if not rows:
        print("No profiles found — check CHROME_USER_DATA_DIR in config.py")
        return
    for dir_name, name, email, wiza in rows:
        marker = " <-- configured" if dir_name == config.CHROME_PROFILE_DIRECTORY else ""
        flag = "[WIZA]" if wiza else "      "
        print(f"{flag} {dir_name:<12} {name:<28} {email}{marker}")
    print()
    print("Set CHROME_PROFILE_DIRECTORY in wiza/config.py to the directory name you want.")


if __name__ == "__main__":
    main()
