"""The reveal click is the ONLY thing we're allowed to press in Wiza's panel.

The panel renders other buttons right next to it ('Get started', 'View all
contact info with Wiza', the per-value 'Copy' buttons). Matching the label
loosely would let us press one of those, so the match must stay exact.
"""
from wiza.cdp import _find_reveal_button


def _button(text):
    """A minimal CDP-shaped <button> node wrapping a text node."""
    return {
        "nodeType": 1, "nodeName": "BUTTON", "nodeId": 1, "backendNodeId": 11,
        "children": [{"nodeType": 3, "nodeValue": text, "children": []}],
    }


def _root(*buttons):
    return {"nodeType": 9, "nodeName": "#document", "children": list(buttons)}


def test_finds_the_reveal_button():
    assert _find_reveal_button(_root(_button("Reveal contact info"))) is not None


def test_match_is_case_and_whitespace_insensitive():
    node = _find_reveal_button(_root(_button("  REVEAL   Contact\n Info ")))
    assert node is not None


def test_ignores_every_other_panel_button():
    for label in ("Get started", "View all contact info with Wiza", "Copy",
                  "No email found", "No phone found", "Prospect", "Contacts",
                  "Upgrade to reveal contact info now", "Reveal contact info "
                  "for 5 more leads"):
        assert _find_reveal_button(_root(_button(label))) is None, label


def test_picks_reveal_out_of_a_crowded_panel():
    root = _root(_button("Get started"), _button("Reveal contact info"),
                 _button("Copy"))
    node = _find_reveal_button(root)
    assert node is not None
    assert "reveal" in node["children"][0]["nodeValue"].lower()


def test_no_buttons_at_all():
    assert _find_reveal_button(_root()) is None


def test_detects_wiza_fair_use_warning():
    """Wiza's throttle message must halt a run, not be silently timed out on."""
    from wiza.cdp import _is_rate_limited
    real = ("you have reached fair use limits. you've made too many requests "
            "in too short of time. please try again later.")
    assert _is_rate_limited(real)
    assert _is_rate_limited("too many requests")
    # A healthy panel must never trip it.
    assert not _is_rate_limited(
        "emailvalid emailmario@kineticplaygroundsf.comcopy reveal contact info")
    assert not _is_rate_limited("email no email found phone number no phone found")
