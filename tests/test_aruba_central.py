from unittest.mock import MagicMock, patch

import pytest
from app.services.aruba_central import (
    ArubaCentralClient,
    ArubaCentralError,
    _normalize_mac,
)


def ok(body, code=200):
    return {"code": code, "msg": body, "headers": {}}


def client_with(responses):
    """Client whose pycentral connection returns the given command() results."""
    c = ArubaCentralClient("https://central.example.com/", "tok")
    c._central = MagicMock()
    c._central.command.side_effect = responses
    return c


def test_normalize_mac():
    assert _normalize_mac("AA:BB-CC.DD:ee:FF") == "aabbccddeeff"


def test_base_url_trailing_slash_and_pycentral_token():
    c = ArubaCentralClient("https://x.example.com///", "tok")
    assert c.base_url == "https://x.example.com"
    assert c._central.central_info["token"]["access_token"] == "tok"
    assert c._central.central_info["base_url"] == "https://x.example.com"


def test_base_url_without_scheme_gets_https():
    c = ArubaCentralClient("apigw.example.com", "tok")
    assert c._central.central_info["base_url"] == "https://apigw.example.com"


def test_invalid_base_url_raises_aruba_error():
    with pytest.raises(ArubaCentralError, match="Invalid Aruba Central configuration"):
        ArubaCentralClient("", "tok")


def test_pycentral_sysexit_on_init_is_wrapped():
    with patch("app.services.aruba_central.ArubaCentralBase", side_effect=SystemExit("bye")):
        with pytest.raises(ArubaCentralError):
            ArubaCentralClient("https://x", "tok")


def test_get_site_names_dedupes_sorts_and_strips():
    c = client_with([ok({"sites": [{"site_name": " B "}, {"siteName": "A"}, {"name": "B"}, "junk", {}]})])
    assert c.get_site_names() == ["A", "B"]


def test_get_site_names_falls_back_on_404():
    c = client_with([ok("", 404), ok({"items": [{"name": "HQ"}]})])
    assert c.get_site_names() == ["HQ"]
    urls = [call.kwargs['apiPath'] for call in c._central.command.call_args_list]
    assert urls[0].endswith("/central/v2/sites")
    assert urls[1].endswith("/network-monitoring/v1/sites-health")


def test_get_site_names_paginates():
    page1 = ok({"items": [{"name": "A"}], "total": 1500})
    page2 = ok({"items": [{"name": "B"}], "total": 1500})
    c = client_with([page1, page2])
    assert c.get_site_names() == ["A", "B"]
    offsets = [call.kwargs["apiParams"]["offset"] for call in c._central.command.call_args_list]
    assert offsets == [0, 1000]


def test_get_site_names_all_endpoints_404():
    c = client_with([ok("", 404), ok("", 404)])
    with pytest.raises(ArubaCentralError, match="Could not fetch sites"):
        c.get_site_names()


def test_get_site_names_http_error_includes_status():
    c = client_with([ok("nope", 401), ok("nope", 401)])
    with pytest.raises(ArubaCentralError, match="401"):
        c.get_site_names()


def test_command_exception_wrapped():
    c = client_with([RuntimeError("boom"), RuntimeError("boom")])
    with pytest.raises(ArubaCentralError, match="boom"):
        c.get_site_names()


def test_pycentral_sysexit_during_request_is_wrapped():
    # pycentral's command() calls exit() on connection failures
    c = client_with([SystemExit("exiting..."), SystemExit("exiting...")])
    with pytest.raises(ArubaCentralError):
        c.get_site_names()


def test_non_json_body_is_error():
    c = client_with([ok("<html>oops</html>"), ok("<html>oops</html>")])
    with pytest.raises(ArubaCentralError, match="non-JSON"):
        c.get_site_names()


def test_parse_ap_normalizes_and_handles_aliases():
    ap = ArubaCentralClient._parse_ap(
        {"serialNumber": " cn123 ", "macaddr": "AA:BB:CC:DD:EE:FF", "site": "HQ", "name": "ap1"}
    )
    assert ap == {"serial": "CN123", "mac": "aabbccddeeff", "site_name": "HQ", "ap_name": "ap1", "group_name": ""}


@pytest.mark.parametrize("key", ["group_name", "group", "groupName"])
def test_parse_ap_reads_group(key):
    ap = ArubaCentralClient._parse_ap({"serial": "s", "site": "HQ", key: " Campus "})
    assert ap["group_name"] == "Campus"


def test_parse_ap_skips_unassigned():
    assert ArubaCentralClient._parse_ap({"serial": "X", "macaddr": "aa"}) is None
    assert ArubaCentralClient._parse_ap({"serial": "X", "site": "  "}) is None


def test_get_aps_with_sites_filters_unassigned():
    c = client_with([ok({"aps": [
        {"serial": "s1", "macaddr": "aa:bb:cc:dd:ee:01", "site": "HQ", "ap_name": "a"},
        {"serial": "s2", "macaddr": "aa:bb:cc:dd:ee:02"},
    ]})])
    aps = c.get_aps_with_sites()
    assert [a["serial"] for a in aps] == ["S1"]


def test_get_aps_falls_back_to_new_central():
    c = client_with([ok("", 404), ok({"items": [{"serialNumber": "s", "siteName": "HQ"}]})])
    assert c.get_aps_with_sites()[0]["site_name"] == "HQ"
