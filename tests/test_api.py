from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import main
from app.services.aruba_central import ArubaCentralError
from app.services.greenlake_devices import GreenlakeDeviceError

ARUBA = {"base_url": "https://central", "access_token": "t"}
GL = {"client_id": "i", "client_secret": "s"}

APS = [
    {"serial": "S1", "mac": "m1", "site_name": "HQ", "ap_name": "ap1", "group_name": "Campus"},
    {"serial": "S2", "mac": "m2", "site_name": "HQ", "ap_name": "ap2", "group_name": "Lab"},
    {"serial": "S3", "mac": "m3", "site_name": "Branch", "ap_name": "ap3", "group_name": ""},
    {"serial": "S4", "mac": "m4", "site_name": "Branch", "ap_name": "ap4", "group_name": ""},
]


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_root_serves_index(client):
    r = client.get("/")
    assert r.status_code == 200 and "html" in r.headers["content-type"]


def test_sites_ok(client):
    with patch.object(main.ArubaCentralClient, "get_site_names", return_value=["A"]):
        r = client.post("/api/sites", json={"aruba": ARUBA})
    assert r.json() == {"sites": ["A"]}


def test_sites_aruba_error_is_400(client):
    with patch.object(main.ArubaCentralClient, "get_site_names", side_effect=ArubaCentralError("bad")):
        r = client.post("/api/sites", json={"aruba": ARUBA})
    assert r.status_code == 400 and r.json()["detail"] == "bad"


def test_sites_validation_error(client):
    assert client.post("/api/sites", json={"aruba": {}}).status_code == 422


def sync(client, **extra):
    return client.post("/api/sync", json={"aruba": ARUBA, "greenlake": GL, **extra})


def patched_sync(client, devices_lookup, **extra):
    with patch.object(main.ArubaCentralClient, "get_aps_with_sites", return_value=APS), \
         patch.object(main.GreenlakeDeviceClient, "list_devices", return_value=[]), \
         patch.object(main.GreenlakeDeviceClient, "build_device_lookup", return_value=devices_lookup), \
         patch.object(main.GreenlakeDeviceClient, "patch_device_tags", return_value=[{"devices": [], "result": {}}]) as pt:
        return sync(client, **extra), pt


def test_sync_matches_by_serial_then_mac_and_reports_unmatched(client):
    r, pt = patched_sync(client, {"S1": "d1", "m2": "d2", "S3": "d3"})
    body = r.json()
    assert r.status_code == 200
    assert body["ap_count"] == 4 and body["site_count"] == 2
    assert {m["greenlake_device_id"] for m in body["matched"]} == {"d1", "d2", "d3"}
    assert [u["serial"] for u in body["unmatched"]] == ["S4"]
    calls = {tuple(sorted(c.args[1].items())): c.args[0] for c in pt.call_args_list}
    assert calls == {
        (("ArubaCentralGroup", "Campus"), ("ArubaCentralSite", "HQ")): ["d1"],
        (("ArubaCentralGroup", "Lab"), ("ArubaCentralSite", "HQ")): ["d2"],
        (("ArubaCentralSite", "Branch"),): ["d3"],
    }
    assert {m["group"] for m in body["matched"]} == {"Campus", "Lab", None}


def test_sync_selected_sites_filters(client):
    r, pt = patched_sync(client, {"S1": "d1", "S3": "d3"}, selected_sites=["HQ"])
    body = r.json()
    assert body["sites"] == ["HQ"] and body["ap_count"] == 2
    assert pt.call_count == 1


def test_sync_no_aps_short_circuits(client):
    with patch.object(main.ArubaCentralClient, "get_aps_with_sites", return_value=APS):
        r = sync(client, selected_sites=["Nope"])
    assert r.json() == {"site_count": 0, "ap_count": 0, "matched": [], "unmatched": [], "patch_results": []}


def test_sync_aruba_error_400(client):
    with patch.object(main.ArubaCentralClient, "get_aps_with_sites", side_effect=ArubaCentralError("a")):
        assert sync(client).status_code == 400


def test_sync_greenlake_list_error_400(client):
    with patch.object(main.ArubaCentralClient, "get_aps_with_sites", return_value=APS), \
         patch.object(main.GreenlakeDeviceClient, "list_devices", side_effect=GreenlakeDeviceError("g")):
        assert sync(client).status_code == 400


def test_sync_patch_error_500(client):
    with patch.object(main.ArubaCentralClient, "get_aps_with_sites", return_value=APS), \
         patch.object(main.GreenlakeDeviceClient, "list_devices", return_value=[]), \
         patch.object(main.GreenlakeDeviceClient, "build_device_lookup", return_value={"S1": "d1"}), \
         patch.object(main.GreenlakeDeviceClient, "patch_device_tags", side_effect=GreenlakeDeviceError("p")):
        r = sync(client)
    assert r.status_code == 500 and "Tag patch failed" in r.json()["detail"]


def test_unhandled_error_does_not_leak_details(client):
    with patch.object(main.ArubaCentralClient, "get_site_names", side_effect=RuntimeError("secret-token-123")):
        r = client.post("/api/sites", json={"aruba": ARUBA})
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal server error"}
    assert "secret-token-123" not in r.text
    assert "Traceback" not in r.text


def test_sync_selected_sites_filters_group_calls(client):
    r, pt = patched_sync(client, {"S1": "d1", "S2": "d2"}, selected_sites=["HQ"])
    assert pt.call_count == 2  # Campus and Lab are separate payloads


def test_sync_groups_disabled_uses_site_tag_only(client):
    with patch.object(main.ArubaCentralClient, "get_aps_with_sites", return_value=APS), \
         patch.object(main.GreenlakeDeviceClient, "list_devices", return_value=[]), \
         patch.object(main.GreenlakeDeviceClient, "build_device_lookup", return_value={"S1": "d1", "S2": "d2"}), \
         patch.object(main.GreenlakeDeviceClient, "patch_device_tags", return_value=[]) as pt:
        r = client.post("/api/sync", json={
            "aruba": ARUBA, "greenlake": {**GL, "tag_groups": False}, "selected_sites": ["HQ"],
        })
    assert r.status_code == 200
    pt.assert_called_once_with(["d1", "d2"], {"ArubaCentralSite": "HQ"})


def test_sync_custom_group_tag_key(client):
    with patch.object(main.ArubaCentralClient, "get_aps_with_sites", return_value=APS[:1]), \
         patch.object(main.GreenlakeDeviceClient, "list_devices", return_value=[]), \
         patch.object(main.GreenlakeDeviceClient, "build_device_lookup", return_value={"S1": "d1"}), \
         patch.object(main.GreenlakeDeviceClient, "patch_device_tags", return_value=[]) as pt:
        client.post("/api/sync", json={"aruba": ARUBA, "greenlake": {**GL, "group_tag_key": "CG"}})
    assert pt.call_args.args[1] == {"ArubaCentralSite": "HQ", "CG": "Campus"}


def test_sync_same_tag_keys_rejected(client):
    r = client.post("/api/sync", json={"aruba": ARUBA, "greenlake": {**GL, "group_tag_key": "ArubaCentralSite"}})
    assert r.status_code == 400
