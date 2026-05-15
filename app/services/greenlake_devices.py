import json
import time
from typing import Any, Dict, Iterator, List, Optional

import requests

GREENLAKE_TOKEN_URL = "https://sso.common.cloud.hpe.com/as/token.oauth2"
_TOKEN_REFRESH_BUFFER = 60  # seconds before expiry to refresh

_DEVICES_LIST_PATH = "/devices/v1/devices"
_DEVICES_PATCH_PATH = "/devices/v2beta1/devices"
_PATCH_BATCH_SIZE = 25
_PAGE_SIZE = 2000  # devices API supports up to 2000 per page


class GreenlakeDeviceError(Exception):
    pass


def _normalize_mac(mac: str) -> str:
    return mac.lower().replace(":", "").replace("-", "").replace(".", "")


def _normalize_serial(serial: str) -> str:
    return serial.strip().upper()


def _chunks(lst: List[Any], size: int) -> Iterator[List[Any]]:
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


class GreenlakeDeviceClient:
    def __init__(
        self,
        api_url: str,
        client_id: str,
        client_secret: str,
        tag_key: str = "ArubaCentralSite",
    ):
        self.base_url = api_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self.tag_key = tag_key
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _fetch_token(self) -> None:
        try:
            resp = requests.post(
                GREENLAKE_TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise GreenlakeDeviceError(f"Token request failed: {exc}") from exc

        if not resp.ok:
            raise GreenlakeDeviceError(
                f"Failed to obtain GreenLake access token ({resp.status_code}): {resp.text[:400]}"
            )

        data = resp.json()
        self._access_token = data["access_token"]
        expires_in: int = data.get("expires_in", 900)
        self._token_expires_at = time.monotonic() + expires_in - _TOKEN_REFRESH_BUFFER

    def _ensure_token(self) -> str:
        if not self._access_token or time.monotonic() >= self._token_expires_at:
            self._fetch_token()
        return self._access_token  # type: ignore[return-value]

    def _auth_headers(self, content_type: str = "application/json") -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._ensure_token()}",
            "Content-Type": content_type,
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _do_request(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        params: Optional[Any],
        json_payload: Optional[Dict],
    ) -> requests.Response:
        try:
            return requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=json_payload,
                timeout=60,
            )
        except requests.RequestException as exc:
            raise GreenlakeDeviceError(f"Request failed ({method} {url}): {exc}") from exc

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Any] = None,
        json_payload: Optional[Dict] = None,
        content_type: str = "application/json",
    ) -> Any:
        url = f"{self.base_url}{path}"
        headers = self._auth_headers(content_type)
        response = self._do_request(method, url, headers, params, json_payload)

        if response.status_code == 401:
            self._access_token = None
            headers = self._auth_headers(content_type)
            response = self._do_request(method, url, headers, params, json_payload)

        if not response.ok:
            raise GreenlakeDeviceError(
                f"GreenLake API {response.status_code} {response.reason} "
                f"from {method} {path}: {response.text[:400]}"
            )

        if response.text:
            try:
                return response.json()
            except json.JSONDecodeError:
                return {"raw": response.text}
        return {}

    # ------------------------------------------------------------------
    # Devices
    # ------------------------------------------------------------------

    def list_devices(self) -> List[Dict[str, Any]]:
        """Return all devices, paginated."""
        results: List[Dict[str, Any]] = []
        offset = 0

        while True:
            data = self._request(
                "GET",
                _DEVICES_LIST_PATH,
                params={"limit": _PAGE_SIZE, "offset": offset},
            )
            items: List[Any] = data.get("items") or []
            results.extend(item for item in items if isinstance(item, dict))
            total: int = data.get("total") or 0
            offset += len(items)
            if not items or offset >= total:
                break

        return results

    def build_device_lookup(
        self, devices: List[Dict[str, Any]]
    ) -> Dict[str, str]:
        """Return {normalised_serial: device_id, normalised_mac: device_id}."""
        lookup: Dict[str, str] = {}
        for d in devices:
            device_id = d.get("id", "")
            if not device_id:
                continue
            serial = d.get("serialNumber") or d.get("serial_number") or ""
            mac = d.get("macAddress") or d.get("mac_address") or ""
            if serial:
                lookup[_normalize_serial(serial)] = device_id
            if mac:
                lookup[_normalize_mac(mac)] = device_id
        return lookup

    def patch_tags(
        self,
        device_ids: List[str],
        tag_value: str,
    ) -> List[Dict[str, Any]]:
        """PATCH the site tag onto devices in batches of 25.
        Returns list of {batch, result} dicts."""
        results = []
        for batch in _chunks(device_ids, _PATCH_BATCH_SIZE):
            result = self._request(
                "PATCH",
                _DEVICES_PATCH_PATH,
                params=[("id", did) for did in batch],
                json_payload={"tags": {self.tag_key: tag_value}},
                content_type="application/merge-patch+json",
            )
            results.append({"devices": batch, "result": result})
        return results
