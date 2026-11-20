from typing import Any, Dict, List, Optional, Tuple

from pycentral.base import ArubaCentralBase


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
        try:
            self._central = ArubaCentralBase(
                central_info={
                    "base_url": self.base_url,
                    "token": {"access_token": access_token},
                },
                ssl_verify=True,
            )
        # pycentral calls sys.exit()/exit() on bad input; never let that escape.
        except (SystemExit, KeyError, ValueError) as exc:
            raise ArubaCentralError(f"Invalid Aruba Central configuration: {exc}") from exc

    def _get_page(self, path: str, offset: int, limit: int) -> Optional[Dict[str, Any]]:
        """GET one page via pycentral. Returns None on 404, the JSON body otherwise."""
        try:
            result = self._central.command(
                apiMethod="GET",
                apiPath=path,
                apiParams={"offset": offset, "limit": limit},
            )
        # pycentral calls exit() on unexpected failures (e.g. connection errors).
        except (SystemExit, Exception) as exc:
            raise ArubaCentralError(f"Request failed for {path}: {exc}") from exc

        code = result.get("code")
        msg = result.get("msg")
        if code == 404:
            return None
        if not (isinstance(code, int) and 200 <= code < 300):
            raise ArubaCentralError(f"API error {code} from {path}: {str(msg)[:400]}")
        if not isinstance(msg, dict):
            raise ArubaCentralError(f"Unexpected non-JSON response from {path}: {str(msg)[:400]}")
        return msg

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
            data = self._get_page(path, offset, limit)
            if data is None:
                return None
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
          group_name  – Central group name (empty if the endpoint doesn't report one)
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
            data = self._get_page(path, offset, limit)
            if data is None:
                return None
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

        group_name = str(
            item.get("group_name") or item.get("group") or item.get("groupName") or ""
        ).strip()

        if not site_name:
            return None  # skip APs not assigned to a site

        return {
            "serial": serial,
            "mac": mac,
            "site_name": site_name,
            "ap_name": ap_name,
            "group_name": group_name,
        }
