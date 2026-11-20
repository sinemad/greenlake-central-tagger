from unittest.mock import patch

import pytest
import requests

from app.services import greenlake_devices as gd
from app.services.greenlake_devices import GreenlakeDeviceClient, GreenlakeDeviceError


@pytest.fixture
def client():
    c = GreenlakeDeviceClient("https://gl.example.com/", "id", "secret", tag_key="Site")
    c._access_token = "tok"
    c._token_expires_at = float("inf")
    return c


def test_chunks():
    assert list(gd._chunks([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]
    assert list(gd._chunks([], 25)) == []


def test_normalizers():
    assert gd._normalize_mac("AA:BB-CC.DD:EE:FF") == "aabbccddeeff"
    assert gd._normalize_serial(" cn1 ") == "CN1"


def test_build_device_lookup(client):
    lookup = client.build_device_lookup([
        {"id": "d1", "serialNumber": "sn1", "macAddress": "AA:BB:CC:DD:EE:01"},
        {"id": "d2", "serial_number": "sn2"},
        {"serialNumber": "no-id"},
    ])
    assert lookup == {"SN1": "d1", "aabbccddee01": "d1", "SN2": "d2"}


def test_fetch_token_success_sets_expiry(resp):
    c = GreenlakeDeviceClient("https://gl", "id", "secret")
    with patch.object(gd.requests, "post", return_value=resp({"access_token": "abc", "expires_in": 100})) as post, \
         patch.object(gd.time, "monotonic", return_value=1000.0):
        assert c._ensure_token() == "abc"
    assert post.call_args.kwargs["data"]["grant_type"] == "client_credentials"
    assert c._token_expires_at == 1000.0 + 100 - gd._TOKEN_REFRESH_BUFFER


def test_fetch_token_failure(resp):
    c = GreenlakeDeviceClient("https://gl", "id", "secret")
    with patch.object(gd.requests, "post", return_value=resp(status=400, text="bad")):
        with pytest.raises(GreenlakeDeviceError, match="400"):
            c._ensure_token()


def test_fetch_token_network_error():
    c = GreenlakeDeviceClient("https://gl", "id", "secret")
    with patch.object(gd.requests, "post", side_effect=requests.ConnectionError("down")):
        with pytest.raises(GreenlakeDeviceError, match="Token request failed"):
            c._ensure_token()


def test_expired_token_is_refreshed(resp):
    c = GreenlakeDeviceClient("https://gl", "id", "secret")
    c._access_token = "old"
    c._token_expires_at = 0.0
    with patch.object(gd.requests, "post", return_value=resp({"access_token": "new"})):
        assert c._ensure_token() == "new"


def test_request_retries_once_on_401(client, resp):
    responses = [resp(status=401, text="expired"), resp({"ok": True})]
    with patch.object(gd.requests, "request", side_effect=responses) as req, \
         patch.object(gd.requests, "post", return_value=resp({"access_token": "fresh"})):
        assert client._request("GET", "/x") == {"ok": True}
    assert req.call_count == 2
    assert req.call_args.kwargs["headers"]["Authorization"] == "Bearer fresh"


def test_request_error_raises(client, resp):
    with patch.object(gd.requests, "request", return_value=resp(status=500, text="oops", reason="ISE")):
        with pytest.raises(GreenlakeDeviceError, match="500"):
            client._request("GET", "/x")


def test_request_non_json_and_empty(client, resp):
    with patch.object(gd.requests, "request", return_value=resp(text="plain")):
        assert client._request("GET", "/x") == {"raw": "plain"}
    with patch.object(gd.requests, "request", return_value=resp(text="")):
        assert client._request("GET", "/x") == {}


def test_list_devices_paginates(client):
    pages = [
        {"items": [{"id": "1"}, {"id": "2"}], "total": 3},
        {"items": [{"id": "3"}], "total": 3},
    ]
    with patch.object(client, "_request", side_effect=pages) as req:
        devices = client.list_devices()
    assert [d["id"] for d in devices] == ["1", "2", "3"]
    assert [c.kwargs["params"]["offset"] for c in req.call_args_list] == [0, 2]


def test_list_devices_empty(client):
    with patch.object(client, "_request", return_value={"items": [], "total": 0}):
        assert client.list_devices() == []


def test_patch_device_tags_multiple_keys(client):
    with patch.object(client, "_request", return_value={}) as req:
        client.patch_device_tags(["d1"], {"Site": "HQ", "Group": "Campus"})
    assert req.call_args.kwargs["json_payload"] == {"tags": {"Site": "HQ", "Group": "Campus"}}


def test_patch_tags_batches_of_25(client):
    ids = [f"d{i}" for i in range(60)]
    with patch.object(client, "_request", return_value={}) as req:
        results = client.patch_tags(ids, "HQ")
    assert [len(r["devices"]) for r in results] == [25, 25, 10]
    first = req.call_args_list[0]
    assert first.args[:2] == ("PATCH", gd._DEVICES_PATCH_PATH)
    assert first.kwargs["json_payload"] == {"tags": {"Site": "HQ"}}
    assert first.kwargs["content_type"] == "application/merge-patch+json"
    assert first.kwargs["params"][0] == ("id", "d0")
