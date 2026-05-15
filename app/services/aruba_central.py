from typing import Any, Dict, List, Optional, Tuple

import requests


class ArubaCentralError(Exception):
    pass


def _normalize_mac(mac: str) -> str:
    return mac.lower().replace(":", "").replace("-", "").replace(".", "")


class ArubaCentralClient:
    # Sites endpoints tried in order; first non-404 wins.
    _SITES_ENDPOINTS = [
        "/central/v2/sites",               # Classic Central
        "/network-monitoring/v1/sites-health",  # New Central
    ]

    # AP listing endpoints tried in order; first non-404 wins.
    # Returns APs with serial, mac, and site assignment.
    _APS_ENDPOINTS = [
        "/monitoring/v2/aps",              # Classic Central
        "/network-monitoring/v1/access-points",  # New Central
    ]

    def __init__(self, base_url: str, access_token: str):
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {access_token}"})

    # ------------------------------------------------------------------
    # Sites
    # ------------------------------------------------------------------

    def get_site_names(self) -> List[str]:
        last_error: Optional[Exception] = None
        for endpoint in self._SITES_ENDPOINTS:
            try:
                names = self._fetch_site_names(endpoint)
                if names is not None:
                    return names
            except ArubaCentralError as exc:
                last_error = exc
        raise ArubaCentralError(
            f"Could not fetch sites from any known endpoint. Last error: {last_error}"
        )

    def _fetch_site_names(self, path: str) -> Optional[List[str]]:
        names: List[str] = []
        offset = 0
        limit = 1000

        while True:
            try:
                resp = self._session.get(
                    self.base_url + path,
                    params={"offset": offset, "limit": limit},
                    timeout=30,
                )
            except requests.RequestException as exc:
                raise ArubaCentralError(f"Request failed for {path}: {exc}") from exc

            if resp.status_code == 404:
                return None

            if not resp.ok:
                raise ArubaCentralError(
                    f"API error {resp.status_code} from {path}: {resp.text[:400]}"
                )

            data: Dict[str, Any] = resp.json()
            items: List[Any] = (
                data.get("items") or data.get("data") or data.get("sites") or []
            )

            for item in items:
                if not isinstance(item, dict):
                    continue
                name = (
                    item.get("site_name") or item.get("siteName") or item.get("name")
                )
                if name:
                    names.append(str(name).strip())

            total: int = data.get("total") or data.get("count") or 0
            if not items or offset + limit >= total:
                break
            offset += limit

        return sorted(set(names))

    # ------------------------------------------------------------------
    # Access points
    # ------------------------------------------------------------------

    def get_aps_with_sites(self) -> List[Dict[str, str]]:
        """Return all APs with their site assignments.

        Each item is a dict with:
          serial      – normalised serial number (upper-case)
          mac         – normalised MAC (lower-case, no separators)
          site_name   – Aruba Central site name
          ap_name     – AP hostname / name
        """
        last_error: Optional[Exception] = None
        for endpoint in self._APS_ENDPOINTS:
            try:
                aps = self._fetch_aps(endpoint)
                if aps is not None:
                    return aps
            except ArubaCentralError as exc:
                last_error = exc
        raise ArubaCentralError(
            f"Could not fetch APs from any known endpoint. Last error: {last_error}"
        )

    def _fetch_aps(self, path: str) -> Optional[List[Dict[str, str]]]:
        aps: List[Dict[str, str]] = []
        offset = 0
        limit = 1000

        while True:
            try:
                resp = self._session.get(
                    self.base_url + path,
                    params={"offset": offset, "limit": limit},
                    timeout=30,
                )
            except requests.RequestException as exc:
                raise ArubaCentralError(f"Request failed for {path}: {exc}") from exc

            if resp.status_code == 404:
                return None

            if not resp.ok:
                raise ArubaCentralError(
                    f"API error {resp.status_code} from {path}: {resp.text[:400]}"
                )

            data: Dict[str, Any] = resp.json()
            items: List[Any] = (
                data.get("aps")
                or data.get("items")
                or data.get("data")
                or []
            )

            for item in items:
                if not isinstance(item, dict):
                    continue
                ap = self._parse_ap(item)
                if ap:
                    aps.append(ap)

            total: int = data.get("total") or data.get("count") or 0
            if not items or offset + limit >= total:
                break
            offset += limit

        return aps

    @staticmethod
    def _parse_ap(item: Dict[str, Any]) -> Optional[Dict[str, str]]:
        serial = str(
            item.get("serial") or item.get("serialNumber") or item.get("serial_number") or ""
        ).strip().upper()

        raw_mac = str(
            item.get("macaddr") or item.get("mac_address") or item.get("macAddress")
            or item.get("mac") or ""
        ).strip()
        mac = _normalize_mac(raw_mac)

        site_name = str(
            item.get("site") or item.get("site_name") or item.get("siteName") or ""
        ).strip()

        ap_name = str(
            item.get("ap_name") or item.get("name") or item.get("hostname") or ""
        ).strip()

        if not site_name:
            return None  # skip APs not assigned to a site

        return {"serial": serial, "mac": mac, "site_name": site_name, "ap_name": ap_name}
