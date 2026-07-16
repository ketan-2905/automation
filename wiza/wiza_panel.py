"""Extraction logic for the Wiza contact panel.

Two layers:
  * Pure functions (`parse_html`, `classify`) — no browser, fully unit-tested
    against a saved real-HTML fixture.
  * Live helpers (`wait_for_panel`, `extract_from_root`, `is_blocked`,
    `find_panel_root`) — used by the browser run.

The panel puts every contact value inside a `<label class="... cursor-pointer">`
element (emails and the inner phone-number label). We collect those text values
and classify each as an email or a phone via regex, so we don't depend on the
Vue scope hashes (data-v-*) that change between extension versions.
"""
from __future__ import annotations

import re
import time
from html.parser import HTMLParser

from . import config

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _digit_count(s: str) -> int:
    return sum(ch.isdigit() for ch in s)


def classify(values):
    """Split a list of raw label strings into ordered, de-duped emails/phones."""
    emails, phones = [], []
    for raw in values:
        v = " ".join((raw or "").split())  # collapse whitespace/newlines
        if not v:
            continue
        m = EMAIL_RE.search(v)
        if m:
            e = m.group(0).lower()
            if e not in emails:
                emails.append(e)
        elif _digit_count(v) >= 7:  # looks like a phone number
            if v not in phones:
                phones.append(v)
    return {"emails": emails, "phones": phones}


class _LabelCollector(HTMLParser):
    """Collect text of every <label> whose class contains 'cursor-pointer'."""

    def __init__(self):
        super().__init__()
        self._stack = []          # one frame per open <label>
        self.values = []

    def handle_starttag(self, tag, attrs):
        if tag == "label":
            cls = dict(attrs).get("class", "") or ""
            self._stack.append({"target": "cursor-pointer" in cls, "text": []})

    def handle_data(self, data):
        # Route text into the innermost open target label (phone numbers nest a
        # non-target label around the target one).
        for frame in reversed(self._stack):
            if frame["target"]:
                frame["text"].append(data)
                break

    def handle_endtag(self, tag):
        if tag == "label" and self._stack:
            frame = self._stack.pop()
            if frame["target"]:
                val = "".join(frame["text"]).strip()
                if val:
                    self.values.append(val)


def parse_label_values(html: str):
    """Return the raw text of all cursor-pointer labels in the given HTML."""
    p = _LabelCollector()
    p.feed(html)
    return p.values


def parse_html(html: str):
    """Parse panel HTML into {'emails': [...], 'phones': [...]}."""
    return classify(parse_label_values(html))


# --------------------------------------------------------------------------
# Live (Playwright) helpers
# --------------------------------------------------------------------------

def find_panel_root(page):
    """Return the Page or Frame that currently contains the Wiza panel, or None.

    The panel is normally injected into the main document, but Wiza may render
    it inside an iframe; we check both.
    """
    try:
        if page.query_selector(config.PANEL_SELECTOR):
            return page
    except Exception:
        pass
    for frame in page.frames:
        try:
            if frame.query_selector(config.PANEL_SELECTOR):
                return frame
        except Exception:
            continue
    return None


def wait_for_panel(page, timeout_ms=None):
    """Poll until the panel has at least one value label; return its root or None."""
    timeout_ms = timeout_ms or config.PANEL_TIMEOUT_MS
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        root = find_panel_root(page)
        if root is not None:
            try:
                if root.query_selector_all(config.VALUE_SELECTOR):
                    return root
            except Exception:
                pass
        page.wait_for_timeout(500)
    return None


def extract_from_root(root):
    """Read the panel values from a Page/Frame root -> {'emails', 'phones'}."""
    els = root.query_selector_all(config.VALUE_SELECTOR)
    values = []
    for el in els:
        try:
            values.append((el.inner_text() or "").strip())
        except Exception:
            continue
    return classify(values)


def is_blocked(page):
    """True if LinkedIn bounced us to a login/checkpoint/authwall page.

    Must catch `/sales/login`: when the session is dead EVERY lead URL redirects
    there. Without it a logged-out run would mark 150 rows `not_found` and then
    skip them forever on resume — silently losing leads that do have data.
    """
    try:
        url = (page.url or "").lower()
    except Exception:
        return False
    markers = ("checkpoint", "/authwall", "/login", "/uas/", "/signup")
    return any(m in url for m in markers)
