from pathlib import Path

from wiza import wiza_panel

FIXTURE = Path(__file__).parent / "fixtures" / "wiza_panel_sample.html"


def test_parse_real_panel_html():
    html = FIXTURE.read_text(encoding="utf-8")
    res = wiza_panel.parse_html(html)
    assert res["emails"] == [
        "jesse@leonardleadership.com",
        "jleonard@f45training.com",
    ]
    assert res["phones"] == [
        "+1 (432) 254-8467",
        "+1 (575) 623-7629",
        "+1 (575) 420-5411",
    ]


def test_classify_separates_and_orders():
    res = wiza_panel.classify(["a@b.com", "+1 (432) 254-8467", "   ", "x@y.io"])
    assert res["emails"] == ["a@b.com", "x@y.io"]
    assert res["phones"] == ["+1 (432) 254-8467"]


def test_classify_dedupes_case_insensitively():
    res = wiza_panel.classify(["A@B.com", "a@b.com"])
    assert res["emails"] == ["a@b.com"]


def test_classify_ignores_short_numbers():
    # a value with too few digits is neither an email nor a phone
    res = wiza_panel.classify(["Suite 210"])
    assert res == {"emails": [], "phones": []}


class _FakePage:
    def __init__(self, url):
        self.url = url


def test_is_blocked_detects_sales_login_redirect():
    # a dead session redirects every lead URL here — must halt, not mark not_found
    assert wiza_panel.is_blocked(_FakePage("https://www.linkedin.com/sales/login")) is True


def test_is_blocked_detects_checkpoint_and_authwall():
    assert wiza_panel.is_blocked(_FakePage("https://www.linkedin.com/checkpoint/challenge")) is True
    assert wiza_panel.is_blocked(_FakePage("https://www.linkedin.com/authwall?x=1")) is True


def test_is_blocked_allows_real_lead_page():
    url = "https://www.linkedin.com/sales/lead/ACwAABv4_WUB,NAME_SEARCH,05TH"
    assert wiza_panel.is_blocked(_FakePage(url)) is False


def test_empty_panel():
    assert wiza_panel.parse_html("<div class='prospect-info'></div>") == {
        "emails": [],
        "phones": [],
    }
