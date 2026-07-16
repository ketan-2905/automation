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
        # PUT is required on modern Chrome; GET to /json/new is rejected.
        return self._http("/json/new?" + quote(url, safe=""), method="PUT")

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

    def _read_all_labels(self):
        """Aggregate cursor-pointer label text from every target/frame."""
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

        values = []
        n_targets = 0
        blocked = False
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
                except Exception:
                    pass
                finally:
                    try:
                        call("Target.detachFromTarget", {"sessionId": sid})
                    except Exception:
                        pass
        finally:
            ws.close()
        return values, n_targets, blocked

    def _read_once(self, ws_url=None):
        values, n_targets, blocked = self._read_all_labels()
        result = wiza_panel.classify(values)          # dedups across frames
        result["panel_found"] = bool(values)
        result["blocked"] = blocked
        result["_n_labels"] = len(values)
        result["_n_targets"] = n_targets
        return result

    def dump_panel_html(self, ws_url, path):
        """Save the aggregated label values for inspection (debug)."""
        values, _, _ = self._read_all_labels()
        from pathlib import Path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("\n".join(values), encoding="utf-8")
        return bool(values)

    def scrape(self, url, initial_wait=4, poll_s=2, settle_window=6,
              min_wait=8, empty_timeout=18, max_wait=40):
        """Open the URL and poll until the panel's contacts settle.

        Wiza fills the panel progressively: the first email shows within a few
        seconds, then more emails, then phones (phone lookups are slowest). The
        first value can sit UNCHANGED for a while before the rest stream in, so
        "unchanged" alone doesn't mean done. We therefore keep polling until:
          * we have data, AND it hasn't changed for `settle_window` seconds,
            AND at least `min_wait` seconds have passed (so slow phones aren't
            missed), or
          * the panel is up but still empty after `empty_timeout` (no data), or
          * `max_wait` is reached (hard cap).
        Returns the best (last non-empty) read.
        """
        info = self.open_tab(url)
        tid = info.get("id")
        ws_url = info.get("webSocketDebuggerUrl")
        best = {"emails": [], "phones": [], "panel_found": False}
        try:
            time.sleep(initial_wait)  # let Wiza start, no socket attached
            start = time.time()
            last_sig = None
            last_change = start
            while time.time() - start < max_wait:
                r = self._read_once(ws_url)
                if r.get("blocked"):
                    best = {"emails": [], "phones": [], "panel_found": False,
                            "blocked": True}
                    break
                if r["emails"] or r["phones"]:
                    best = r
                sig = (tuple(r["emails"]), tuple(r["phones"]))
                now = time.time()
                if self.debug:
                    print(f"  [{now - start:4.0f}s] labels={r['_n_labels']} "
                          f"in {r['_n_targets']} frame(s)  "
                          f"emails={len(r['emails'])} phones={len(r['phones'])}")
                if sig != last_sig:
                    last_sig = sig
                    last_change = now

                has_data = bool(best["emails"] or best["phones"])
                if has_data and (now - last_change) >= settle_window \
                        and (now - start) >= min_wait:
                    break
                if not has_data and r["panel_found"] \
                        and (now - start) >= empty_timeout:
                    break
                time.sleep(poll_s)
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
