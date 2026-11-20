import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "update_gateways.py"


@pytest.fixture
def ug(tmp_path, monkeypatch):
    """Load the script as a module with INDEX_HTML/MAIN_PY redirected to temp files,
    so tests never touch the real app files."""
    spec = importlib.util.spec_from_file_location("update_gateways", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    index = tmp_path / "index.html"
    index.write_text(INDEX_HTML, encoding="utf-8")
    main_py = tmp_path / "main.py"
    main_py.write_text('GREENLAKE_API_URL = "https://old.api.greenlake.hpe.com"\n', encoding="utf-8")
    monkeypatch.setattr(mod, "INDEX_HTML", index)
    monkeypatch.setattr(mod, "MAIN_PY", main_py)
    return mod


INDEX_HTML = """<select>
          <optgroup label="Classic Central">
            <option value="https://old-classic.example.com">Old</option>
          </optgroup>
          <optgroup label="New Central">
            <option value="https://old-new.example.com">Old</option>
          </optgroup>
        </select>
<a id="gl-api-url-display" class="x" href="https://old.api.greenlake.hpe.com">https://old.api.greenlake.hpe.com</a>
"""


def soup(html):
    return BeautifulSoup(html, "html.parser")


TABLE = """
<table>
  <tr><th>Region</th><th>API Gateway URL</th></tr>
  <tr><td>US-1</td><td>apigw-prod2.central.arubanetworks.com</td></tr>
  <tr><td>EU-1</td><td>https://eu-apigw.central.arubanetworks.com</td></tr>
  <tr><td>Internal</td><td>internal.example.com</td></tr>
  <tr><td></td><td>blank-label.example.com</td></tr>
  <tr><td>Short</td></tr>
</table>
"""


def test_col_index(ug):
    assert ug._col_index(["region", "url"], "url") == 1
    assert ug._col_index(["region", "url"], "nope") is None


def test_normalise_url(ug):
    assert ug._normalise_url("  host.example.com ") == "https://host.example.com"
    assert ug._normalise_url("http://h") == "http://h"
    assert ug._normalise_url("") == ""


def test_parse_gateway_table(ug):
    rows = ug._parse_gateway_table(soup(TABLE), ("region",), ("url",))
    assert rows == [
        ("US-1", "https://apigw-prod2.central.arubanetworks.com"),
        ("EU-1", "https://eu-apigw.central.arubanetworks.com"),
    ]  # Internal skipped, blank label skipped, short row skipped


def test_parse_skips_tables_without_matching_headers(ug):
    html = "<table><tr><th>Foo</th></tr><tr><td>x</td></tr></table>" + TABLE
    assert len(ug._parse_gateway_table(soup(html), ("region",), ("url",))) == 2


def test_parse_no_tables(ug):
    assert ug._parse_gateway_table(soup("<p>none</p>"), ("region",), ("url",)) == []


def test_build_optgroup(ug):
    out = ug._build_optgroup("Classic Central", [("US", "https://u")])
    assert out.splitlines() == [
        '          <optgroup label="Classic Central">',
        '            <option value="https://u">US</option>',
        "          </optgroup>",
    ]


def test_update_index_html_replaces_both_groups(ug):
    changed = ug.update_index_html(
        [("US", "https://new-classic.example.com")],
        [("EU", "https://new-new.example.com")],
    )
    html = ug.INDEX_HTML.read_text()
    assert changed
    assert "old-classic" not in html and "old-new" not in html
    assert 'value="https://new-classic.example.com">US' in html
    assert 'value="https://new-new.example.com">EU' in html
    assert html.count("<optgroup") == 2


def test_update_index_html_only_touches_nonempty_lists(ug):
    ug.update_index_html([("US", "https://new-classic.example.com")], [])
    html = ug.INDEX_HTML.read_text()
    assert "new-classic" in html and "old-new.example.com" in html


def test_update_index_html_idempotent(ug):
    gws = ([("US", "https://c.example.com")], [("EU", "https://n.example.com")])
    assert ug.update_index_html(*gws) is True
    assert ug.update_index_html(*gws) is False


def test_update_greenlake_url_updates_both_files(ug):
    assert ug.update_greenlake_url("https://new.api.greenlake.hpe.com") is True
    assert 'GREENLAKE_API_URL = "https://new.api.greenlake.hpe.com"' in ug.MAIN_PY.read_text()
    html = ug.INDEX_HTML.read_text()
    assert 'href="https://new.api.greenlake.hpe.com">https://new.api.greenlake.hpe.com</a>' in html
    assert ug.update_greenlake_url("https://new.api.greenlake.hpe.com") is False


def test_fetch_greenlake_api_url(ug):
    page = soup("<p>Use https://global.api.greenlake.hpe.com/ for calls</p>")
    with patch.object(ug, "fetch_soup", return_value=page):
        assert ug.fetch_greenlake_api_url() == "https://global.api.greenlake.hpe.com"


def test_fetch_greenlake_api_url_not_found(ug):
    with patch.object(ug, "fetch_soup", return_value=soup("<p>nothing</p>")):
        assert ug.fetch_greenlake_api_url() is None


def test_fetch_soup_raises_on_http_error(ug):
    class R:
        text = ""
        def raise_for_status(self):
            raise RuntimeError("404")
    with patch.object(ug.requests, "get", return_value=R()):
        with pytest.raises(RuntimeError):
            ug.fetch_soup("https://x")


def test_run_once_updates_files(ug):
    with patch.object(ug, "fetch_classic_gateways", return_value=[("US", "https://c.example.com")]), \
         patch.object(ug, "fetch_new_central_gateways", return_value=[("EU", "https://n.example.com")]), \
         patch.object(ug, "fetch_greenlake_api_url", return_value="https://new.api.greenlake.hpe.com"):
        ug.run_once()
    assert "c.example.com" in ug.INDEX_HTML.read_text()
    assert "new.api.greenlake" in ug.MAIN_PY.read_text()


def test_run_once_skips_update_when_nothing_found(ug):
    before = ug.INDEX_HTML.read_text()
    with patch.object(ug, "fetch_classic_gateways", side_effect=RuntimeError("down")), \
         patch.object(ug, "fetch_new_central_gateways", return_value=[]), \
         patch.object(ug, "fetch_greenlake_api_url", return_value=None):
        ug.run_once()  # must not raise
    assert ug.INDEX_HTML.read_text() == before


def test_run_once_survives_greenlake_failure(ug):
    with patch.object(ug, "fetch_classic_gateways", return_value=[("US", "https://c.example.com")]), \
         patch.object(ug, "fetch_new_central_gateways", return_value=[]), \
         patch.object(ug, "fetch_greenlake_api_url", side_effect=RuntimeError("boom")):
        ug.run_once()
    assert "c.example.com" in ug.INDEX_HTML.read_text()


def test_main_single_run_without_args(ug, monkeypatch):
    monkeypatch.setattr(ug.sys, "argv", ["update_gateways.py"])
    with patch.object(ug, "run_once") as ro:
        ug.main()
    ro.assert_called_once()


def test_main_loop_mode_sleeps_interval(ug, monkeypatch):
    monkeypatch.setattr(ug.sys, "argv", ["update_gateways.py", "42"])
    with patch.object(ug, "run_once") as ro, \
         patch.object(ug.time, "sleep", side_effect=KeyboardInterrupt) as sl:
        with pytest.raises(KeyboardInterrupt):
            ug.main()
    ro.assert_called_once()
    sl.assert_called_once_with(42)


def test_real_index_html_has_both_optgroups():
    """Guards the contract the script depends on: the real page contains both blocks."""
    html = (SCRIPT.parent.parent / "app" / "static" / "index.html").read_text()
    assert '<optgroup label="Classic Central">' in html
    assert '<optgroup label="New Central">' in html
    assert 'id="gl-api-url-display"' in html
