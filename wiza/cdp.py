"""Read the Wiza panel WITHOUT tripping CDP / "agentic" detection.

Background
----------
Wiza's backend refuses to serve the contact panel when a DevTools *Runtime*
debugging channel is active on the page. That channel is exactly what Playwright
turns on the moment it attaches to a tab (`connect_over_cdp` / `launch`), which
is why the panel loads fine in a Chrome you open by hand but stays empty in the
automated one. It's the debugging *connection*, not any launch flag.

Strategy (avoids the detection entirely)
-----------------------------------------
1. Launch a *normal* Chrome with a debugging port — the same browser that works
   when you open it manually. No automation flags.
2. Open each profile URL as an ordinary browser tab through the DevTools HTTP
   endpoint (`PUT /json/new?<url>`). Opening a tab this way runs NO CDP command
   against the page, so Wiza loads and the panel populates just like it does for
   a human.
3. Wait for the panel to fill in (no socket attached during this window).
4. Only THEN attach a websocket and read the *finished* DOM using ONLY the
   low-level `DOM` domain (never `Runtime`), which the anti-bot doesn't watch.
   Parse that HTML with `wiza_panel.parse_html`.

Try it standalone:
    python -m wiza.cdp
    python -m wiza.cdp "https://www.linkedin.com/sales/lead/....."
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from urllib.parse import quote

import websocket  # websocket-client (synchronous)

from . import config, wiza_panel
from .browser import _NO_PROFILE_MSG, _chrome_exe, _free_port

SAMPLE_URL = "https://www.linkedin.com/sales/lead/ACwAAEueeDIBaG60FCNpGlcb-UIVgjNyPEBYZmE,NAME_SEARCH,in82"


# --------------------------------------------------------------------------
# Walking the CDP node tree (from DOM.getDocument depth=-1 pierce=True).
# Nodes look like {nodeName, nodeType, nodeValue, attributes:[k,v,...],
# children:[...], contentDocument:{...}, shadowRoots:[...], templateContent}.
# `pierce` is what lets us descend into Wiza's shadow DOM / any iframe.
# --------------------------------------------------------------------------

def _attrs(node):
    a = node.get("attributes") or []
    return {a[i]: a[i + 1] for i in range(0, len(a) - 1, 2)}


def _walk(node):
    """Depth-first over the whole pierced tree, including shadow + iframe docs."""
    yield node
    for child in node.get("children") or []:
        yield from _walk(child)
    if node.get("contentDocument"):
        yield from _walk(node["contentDocument"])
    for sr in node.get("shadowRoots") or []:
        yield from _walk(sr)
    if node.get("templateContent"):
        yield from _walk(node["templateContent"])


def _node_text(node):
    if node.get("nodeType") == 3:  # text node
        return node.get("nodeValue") or ""
    return "".join(_node_text(c) for c in node.get("children") or [])


def _find_panel_node(root):
    """First element whose class contains 'prospect-info' (the Wiza panel)."""
    for n in _walk(root):
        if n.get("nodeType") == 1 and "prospect-info" in _attrs(n).get("class", ""):
            return n
    return None


def _collect_label_values(root):
    """Text of every <label class*='cursor-pointer'> — one email/phone each."""
    values = []
    for n in _walk(root):
        if (n.get("nodeName") or "").lower() == "label" \
                and "cursor-pointer" in _attrs(n).get("class", ""):
            t = _node_text(n).strip()
            if t:
                values.append(t)
    return values


def _find_reveal_button(root):
    """The panel's 'Reveal contact info' <button>, or None.

    On plans with reveal credits Wiza shows masked values (`***@…`) behind this
    button instead of filling the panel directly. Matched by its visible text so
    the Vue scope hashes (data-v-*) don't matter.
    """
    for n in _walk(root):
        if n.get("nodeType") == 1 and (n.get("nodeName") or "").lower() == "button":
            txt = " ".join(_node_text(n).split()).lower()
            if "reveal contact info" in txt:
                return n
    return None


def _panel_text(root):
    """Normalized lowercase text of every `.prospect-info` panel under root.

    Only meaningful for the Wiza plugin frame — LinkedIn's OWN Sales Navigator
    page also has `.prospect-info` sections that say 'No email found', so the
    caller must scope this to the plugin.wiza.co target or it will read the
    wrong panel.
    """
    parts = []
    for n in _walk(root):
        if n.get("nodeType") == 1 and "prospect-info" in _attrs(n).get("class", ""):
            parts.append(" ".join(_node_text(n).split()))
    return " ".join(parts).lower()


class CdpChrome:
    """A normal Chrome we drive only through HTTP + the DOM domain."""

    def __init__(self, headless=False, debug=False):
        if not config.PROFILE_DIR.exists():
            raise RuntimeError(_NO_PROFILE_MSG)

        self.debug = debug
        self.port = _free_port()
        args = [
            _chrome_exe(),
            f"--remote-debugging-port={self.port}",
            "--remote-allow-origins=*",   # required or Chrome 111+ rejects the ws
            f"--user-data-dir={config.PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if headless:
            args.append("--headless=new")
        self.proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.browser_ws = None
        self._wait_ready()

    # --- HTTP control endpoint ------------------------------------------------
    def _http(self, path, method="GET"):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode("utf-8", "replace")
        body = body.strip()
        return json.loads(body) if body[:1] in ("{", "[") else body

    def _wait_ready(self, timeout=30):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                ver = self._http("/json/version")
                self.browser_ws = ver.get("webSocketDebuggerUrl")
                return
            except Exception as e:  # not up yet
                last = e
                time.sleep(0.5)
        raise RuntimeError(f"Chrome debug endpoint never came up: {last}")

    def open_tab(self, url):
        """Open URL in a BACKGROUND tab so the run never steals focus.

        `PUT /json/new` opens the tab in the FOREGROUND and pulls the whole
        Chrome window to the front — disruptive if you're working elsewhere.
        `Target.createTarget` with background=True creates the tab without
        activating it, so the window stays put. It's still a browser-level tab
        creation with NO page Runtime session attached, so Wiza loads exactly
        as it does via /json/new. Returns {"id": targetId}.
        """
        ws = websocket.create_connection(self.browser_ws, timeout=15)
        try:
            ws.send(json.dumps({
                "id": 1, "method": "Target.createTarget",
                "params": {"url": url, "background": True},
            }))
            while True:
                got = json.loads(ws.recv())
                if got.get("id") == 1:
                    tid = got.get("result", {}).get("targetId")
                    if not tid:
                        # Fall back to the HTTP endpoint if the browser rejected
                        # background creation for any reason.
                        return self._http("/json/new?" + quote(url, safe=""), method="PUT")
                    return {"id": tid}
        finally:
            ws.close()

    def close_tab(self, target_id):
        if not target_id:
            return
        try:
            self._http(f"/json/close/{target_id}")
        except Exception:
            pass

    # --- reading the DOM across ALL targets (no Runtime domain) ---------------
    #
    # Wiza renders the real, data-filled panel inside its own cross-origin
    # iframe, which Chrome runs as a SEPARATE target (out-of-process). A plain
    # DOM.getDocument on the page target — even with pierce=True — can't cross
    # into it (pierce only reaches same-process iframes + shadow DOM). So we
    # connect to the BROWSER-level endpoint, enumerate every target, attach to
    # each (flatten sessions), and read each one's DOM with the DOM domain only.
    # Never Runtime -> still invisible to Wiza's anti-bot.

    def _read_all_labels(self, click_reveal=False):
        """Aggregate cursor-pointer label text from every target/frame.

        With click_reveal=True, also presses the panel's 'Reveal contact info'
        button when it shows up — via DOM coords + Input.dispatchMouseEvent,
        never the Runtime domain, so it stays invisible to Wiza's anti-bot and
        the page sees a trusted click. Returns (values, n_targets, blocked,
        clicked_reveal, wiza_panel_text) — the last being the Wiza frame's
        panel text so the caller can tell 'still searching' (empty) apart from
        'resolved to No email/phone found'.
        """
        ws = websocket.create_connection(self.browser_ws, timeout=45)
        counter = {"i": 0}

        def call(method, params=None, sid=None):
            counter["i"] += 1
            mid = counter["i"]
            msg = {"id": mid, "method": method, "params": params or {}}
            if sid:
                msg["sessionId"] = sid
            ws.send(json.dumps(msg))
            while True:
                got = json.loads(ws.recv())
                if got.get("id") == mid:
                    return got

        def click_node(node, sid):
            """Trusted click at the node's center (DOM + Input domains only)."""
            try:
                call("DOM.scrollIntoViewIfNeeded", {"nodeId": node["nodeId"]}, sid=sid)
            except Exception:
                pass  # already in view / not supported — coords check below decides
            q = call("DOM.getContentQuads", {"nodeId": node["nodeId"]}, sid=sid)
            quads = q.get("result", {}).get("quads") or []
            if not quads:
                return False
            xs, ys = quads[0][0::2], quads[0][1::2]
            cx, cy = sum(xs) / 4.0, sum(ys) / 4.0
            if cx <= 0 or cy <= 0:
                return False  # off-screen
            base = {"x": cx, "y": cy, "button": "left", "clickCount": 1}
            call("Input.dispatchMouseEvent", {**base, "type": "mouseMoved",
                                              "button": "none", "clickCount": 0}, sid=sid)
            call("Input.dispatchMouseEvent", {**base, "type": "mousePressed"}, sid=sid)
            call("Input.dispatchMouseEvent", {**base, "type": "mouseReleased"}, sid=sid)
            return True

        values = []
        n_targets = 0
        blocked = False
        clicked_reveal = False
        wiza_panel_text = ""
        try:
            try:
                call("Target.setDiscoverTargets", {"discover": True})
            except Exception:
                pass
            infos = call("Target.getTargets").get("result", {}).get("targetInfos", [])
            for t in infos:
                if t.get("type") not in ("page", "iframe", "other", "webview", "background_page"):
                    continue
                url = (t.get("url") or "").lower()
                if "linkedin.com" in url and any(
                        m in url for m in ("checkpoint", "/authwall", "/login", "/uas/", "/signup")):
                    blocked = True
                try:
                    att = call("Target.attachToTarget",
                               {"targetId": t["targetId"], "flatten": True})
                    sid = att.get("result", {}).get("sessionId")
                    if not sid:
                        continue
                except Exception:
                    continue
                try:
                    doc = call("DOM.getDocument", {"depth": -1, "pierce": True}, sid=sid)
                    root = doc.get("result", {}).get("root")
                    if root:
                        found = _collect_label_values(root)
                        if found:
                            n_targets += 1
                        values.extend(found)
                        # Read the resolved/searching state ONLY from Wiza's own
                        # frame — LinkedIn's native panel has a decoy "No email
                        # found" that would fool the terminal-state check.
                        if "plugin.wiza.co" in url:
                            wiza_panel_text += " " + _panel_text(root)
                        if click_reveal and not clicked_reveal:
                            btn = _find_reveal_button(root)
                            if btn is not None:
                                try:
                                    clicked_reveal = click_node(btn, sid)
                                except Exception:
                                    pass
                except Exception:
                    pass
                finally:
                    try:
                        call("Target.detachFromTarget", {"sessionId": sid})
                    except Exception:
                        pass
        finally:
            ws.close()
        return values, n_targets, blocked, clicked_reveal, wiza_panel_text

    def _read_once(self, ws_url=None, click_reveal=False):
        values, n_targets, blocked, clicked, ptext = self._read_all_labels(click_reveal)
        result = wiza_panel.classify(values)          # dedups across frames
        result["panel_found"] = bool(values)
        result["blocked"] = blocked
        result["clicked_reveal"] = clicked
        # Terminal-state signals read from the Wiza frame's panel text. While
        # "Finding contact data..." is showing, this text is empty, so both are
        # False and we keep waiting; they flip True only once Wiza resolves.
        result["no_email"] = "no email found" in ptext
        result["no_phone"] = "no phone found" in ptext
        result["_n_labels"] = len(values)
        result["_n_targets"] = n_targets
        return result

    def dump_panel_html(self, ws_url, path):
        """Save the aggregated label values for inspection (debug)."""
        values, _, _, _, _ = self._read_all_labels()
        from pathlib import Path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("\n".join(values), encoding="utf-8")
        return bool(values)

    def scrape(self, url, initial_wait=4, poll_s=2, settle_window=6,
              min_wait=8, empty_timeout=18, max_wait=40,
              reveal_empty_timeout=75):
        """Open the URL and poll until the panel *resolves* to a final state.

        The Wiza panel ends in one of two terminal states, and we wait for one
        of them rather than a blind timeout — because "Finding contact data..."
        can run for well over a minute:
          * RESOLVED WITH DATA — an email and/or phone value appears. We then
            wait `settle_window` more (past `min_wait`) so a slow-arriving phone
            or 2nd email isn't cut off, and return it.
          * RESOLVED EMPTY — the panel shows "No email found" / "No phone found"
            (read from Wiza's own frame, not LinkedIn's decoy panel). We stop
            immediately; there's nothing to wait for.
        While neither has happened (masked preview, or the search spinner) the
        panel's Wiza text is empty, so we keep polling up to a hard safety cap
        (`empty_timeout`, or `reveal_empty_timeout` once we've clicked reveal).

        If the values are gated behind a 'Reveal contact info' button (masked
        `***@…` preview), the poll clicks it — a trusted DOM+Input click, never
        Runtime — and restarts the wait clocks so the post-click lookup gets its
        full window. Returns the best (last non-empty) read.
        """
        info = self.open_tab(url)
        tid = info.get("id")
        ws_url = info.get("webSocketDebuggerUrl")
        best = {"emails": [], "phones": [], "panel_found": False}
        try:
            time.sleep(initial_wait)  # let Wiza start, no socket attached
            start = time.time()
            deadline = start + max_wait
            t0 = start          # min_wait/empty_timeout clock; reset on reveal
            reveal_clicks = 0   # retry if a click missed; bounded so the
            last_sig = None     # deadline extension can't loop forever
            last_change = start
            resolved_final = False   # did the panel reach a DEFINITIVE state?
            while time.time() < deadline:
                r = self._read_once(ws_url, click_reveal=reveal_clicks < 3)
                if r.get("blocked"):
                    best = {"emails": [], "phones": [], "panel_found": False,
                            "blocked": True}
                    break
                now = time.time()
                if r.get("clicked_reveal"):
                    # Values were gated behind the button; we pressed it —
                    # "Finding contact data..." now runs, so give the lookup
                    # a fresh window to stream results in.
                    reveal_clicks += 1
                    t0 = now
                    last_change = now
                    deadline = max(deadline, now + reveal_empty_timeout + 15)
                    if self.debug:
                        print(f"  [{now - start:4.0f}s] clicked 'Reveal contact info'")
                if r["emails"] or r["phones"]:
                    best = r
                # The panel has RESOLVED once it shows values or an explicit
                # "No ... found". Fold the terminal markers into the signature
                # so a searching->resolved transition counts as a change and
                # the settle timer restarts from it.
                resolved_empty = r["no_email"] or r["no_phone"]
                sig = (tuple(r["emails"]), tuple(r["phones"]),
                       r["no_email"], r["no_phone"])
                if self.debug:
                    tag = ""
                    if resolved_empty and not (r["emails"] or r["phones"]):
                        tag = "  <no contact found>"
                    print(f"  [{now - start:4.0f}s] labels={r['_n_labels']} "
                          f"in {r['_n_targets']} frame(s)  "
                          f"emails={len(r['emails'])} phones={len(r['phones'])}{tag}")
                if sig != last_sig:
                    last_sig = sig
                    last_change = now

                has_data = bool(best["emails"] or best["phones"])
                # Fully resolved to nothing (BOTH "No email found" AND "No phone
                # found", no data) — there's nothing left to wait for, so stop
                # at once instead of burning the settle window. Guarded by a
                # reveal click or min_wait so a direct load can't false-trigger
                # before Wiza has actually populated.
                fully_empty = r["no_email"] and r["no_phone"] and not has_data
                if fully_empty and (reveal_clicks or (now - t0) >= min_wait):
                    resolved_final = True
                    if self.debug:
                        print(f"  [{now - start:4.0f}s] no contact found — moving on")
                    break
                resolved = has_data or resolved_empty
                # Partially resolved (e.g. a value present, or only one side is
                # "not found" while the other may still stream in) — wait for it
                # to hold steady so a late 2nd email / slow phone still lands.
                if resolved and (now - last_change) >= settle_window \
                        and (now - t0) >= min_wait:
                    resolved_final = True
                    break
                # Safety net: panel is up but never resolved (stuck spinner).
                # Give the reveal lookup much longer than a plain load.
                empty_after = reveal_empty_timeout if reveal_clicks else empty_timeout
                if not resolved and r["panel_found"] \
                        and (now - t0) >= empty_after:
                    break
                time.sleep(poll_s)
            best["clicked_reveal"] = reveal_clicks > 0
            # True only when the panel reached a definitive state (data or an
            # explicit "No ... found"). False means we gave up on a stuck
            # spinner / never-loaded panel — the caller should retry, not bury
            # it as not_found.
            best["resolved"] = resolved_final or bool(best["emails"] or best["phones"])
            if self.debug:
                try:
                    dump = config.FIXTURE_DUMP / "wiza_panel_live.html"
                    if self.dump_panel_html(ws_url, dump):
                        print(f"  (dumped live panel HTML -> {dump})")
                except Exception as e:
                    print("  (panel dump failed:", str(e)[:120], ")")
        finally:
            self.close_tab(tid)
        return best

    def close(self):
        try:
            self.proc.terminate()
        except Exception:
            pass


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else SAMPLE_URL
    print("Launching normal Chrome (no automation flags) and opening:")
    print(" ", url[:90])
    chrome = CdpChrome(headless=False, debug=True)
    try:
        print("Waiting for the Wiza panel to load & settle (like a human would)...")
        result = chrome.scrape(url)
        print("\n--- EXTRACTED ---")
        if not result.get("panel_found"):
            print("Panel not found in the page. Is this a /sales/lead/ URL and is")
            print("Wiza signed in? (It works in your manual window, so it should here.)")
        print("emails:", result["emails"])
        print("phones:", result["phones"])
    finally:
        input("\nPress Enter to close the browser...")
        chrome.close()


if __name__ == "__main__":
    main()
