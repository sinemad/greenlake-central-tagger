import json
import time
from typing import Any, Dict, List, Optional, Set

import requests

GREENLAKE_TOKEN_URL = "https://sso.common.cloud.hpe.com/as/token.oauth2"
# Refresh the token this many seconds before it actually expires
_TOKEN_REFRESH_BUFFER = 60


class GreenlakeError(Exception):
    pass


class GreenlakeTagClient:
    _PAGE_SIZE = 100  # conservative page size; API max is unspecified

    def __init__(
        self,
        api_url: str,
        client_id: str,
        client_secret: str,
        tenant_id: Optional[str] = None,
        tenant_path_prefix: Optional[str] = None,
        tags_endpoint: str = "/tags/v1/tags",
        resources_endpoint: str = "/inventory/v1/resources",
        tagged_resources_endpoint: str = "/tags/v1/tag-resources",
        assignment_endpoint: str = "/tags/v1/tag-resources",
        tag_key: str = "ArubaCentralSite",
    ):
        self.base_url = api_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self.tenant_id = tenant_id
        self.tenant_path_prefix = tenant_path_prefix
        self.tag_key = tag_key
        self.tags_endpoint = self._resolve_endpoint(tags_endpoint)
        self.resources_endpoint = self._resolve_endpoint(resources_endpoint)
        self.tagged_resources_endpoint = self._resolve_endpoint(tagged_resources_endpoint)
        self.assignment_endpoint = self._resolve_endpoint(assignment_endpoint)

    def _resolve_endpoint(self, endpoint: str) -> str:
        if self.tenant_id:
            try:
                endpoint = endpoint.format(tenant_id=self.tenant_id)
            except Exception:
                pass
        if self.tenant_path_prefix:
            prefix = self.tenant_path_prefix
            if self.tenant_id:
                try:
                    prefix = prefix.format(tenant_id=self.tenant_id)
                except Exception:
                    pass
            endpoint = prefix.rstrip("/") + endpoint
        return endpoint

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
            raise GreenlakeError(f"Token request failed: {exc}") from exc

        if not resp.ok:
            raise GreenlakeError(
                f"Failed to obtain GreenLake access token "
                f"({resp.status_code}): {resp.text[:400]}"
            )

        data = resp.json()
        self._access_token = data["access_token"]
        expires_in: int = data.get("expires_in", 900)
        self._token_expires_at = time.monotonic() + expires_in - _TOKEN_REFRESH_BUFFER

    def _ensure_token(self) -> str:
        if not self._access_token or time.monotonic() >= self._token_expires_at:
            self._fetch_token()
        return self._access_token  # type: ignore[return-value]

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._ensure_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _do_request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]],
        json_payload: Optional[Dict[str, Any]],
    ) -> requests.Response:
        try:
            return requests.request(
                method=method,
                url=url,
                headers=self._headers(),
                params=params,
                json=json_payload,
                timeout=60,
            )
        except requests.RequestException as exc:
            raise GreenlakeError(f"Request failed ({method} {url}): {exc}") from exc

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = self._url(path)
        response = self._do_request(method, url, params, json_payload)
        # On 401 force a token refresh and retry once
        if response.status_code == 401:
            self._access_token = None
            response = self._do_request(method, url, params, json_payload)
        if not response.ok:
            raise GreenlakeError(
                f"GreenLake API {response.status_code} {response.reason} "
                f"from {method} {path}: {response.text[:400]}"
            )
        if response.text:
            try:
                return response.json()
            except json.JSONDecodeError:
                return {"raw": response.text}
        return {}

    def _paginate(
        self,
        path: str,
        base_params: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch all pages from a GreenLake list endpoint."""
        params = dict(base_params or {})
        params.setdefault("limit", self._PAGE_SIZE)
        offset = 0
        results: List[Dict[str, Any]] = []

        while True:
            params["offset"] = offset
            data = self._request("GET", path, params=params)

            if isinstance(data, list):
                results.extend(data)
                break

            items: List[Any] = data.get("items") or data.get("data") or []
            results.extend(item for item in items if isinstance(item, dict))

            total: int = data.get("total") or data.get("count") or 0
            offset += len(items)
            if not items or offset >= total:
                break

        return results

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    def list_tags(self, filter_key: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if filter_key:
            params["filter"] = f"key eq '{filter_key}'"
        return self._paginate(self.tags_endpoint, params)

    def delete_tag(self, tag_id: str) -> Dict[str, Any]:
        return self._request("DELETE", f"{self.tags_endpoint.rstrip('/')}/{tag_id}")

    # ------------------------------------------------------------------
    # Resources (inventory)
    # ------------------------------------------------------------------

    def list_access_points(self, resource_type: str = "ACCESS_POINT") -> List[Dict[str, Any]]:
        return self._paginate(self.resources_endpoint, {"resourceType": resource_type})

    # ------------------------------------------------------------------
    # Tag assignments
    # ------------------------------------------------------------------

    def list_tagged_resources(self, resource_type: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if resource_type:
            params["filter"] = f"resourceType eq '{resource_type}'"
        return self._paginate(self.tagged_resources_endpoint, params)

    def assign_tag(
        self,
        resource: Dict[str, Any],
        tag_id: str,
        resource_type: str,
        tag_value: str,
    ) -> Dict[str, Any]:
        # resourceUri is the canonical identifier per the GreenLake API docs.
        # resourceId is kept as a fallback for APIs that accept it.
        resource_uri = self._extract_resource_uri(resource)
        if not resource_uri:
            raise GreenlakeError("Resource is missing resourceUri — cannot assign tag")

        payload: Dict[str, Any] = {
            "resourceUri": resource_uri,
            "resourceType": resource_type,
            "tags": [
                {
                    "id": tag_id,
                    "key": self.tag_key,
                    "value": tag_value,
                }
            ],
        }

        resource_id = self._extract_resource_id(resource)
        if resource_id:
            payload["resourceId"] = resource_id

        return self._request("POST", self.assignment_endpoint, json_payload=payload)

    def unassign_tag(
        self,
        resource: Dict[str, Any],
        tag_id: str,
        resource_type: str,
    ) -> Dict[str, Any]:
        assignment_id = self._extract_assignment_id(resource)
        if assignment_id:
            return self._request(
                "DELETE",
                f"{self.assignment_endpoint.rstrip('/')}/{assignment_id}",
            )

        resource_uri = self._extract_resource_uri(resource)
        if resource_uri:
            return self._request(
                "DELETE",
                self.assignment_endpoint,
                params={"resourceUri": resource_uri, "tagId": tag_id},
            )

        raise GreenlakeError("Cannot unassign tag: resource has neither id nor resourceUri")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup_tags(
        self,
        current_site_names: Set[str],
        resource_type: str,
        cleanup_unassign: bool = True,
        delete_orphan_tags: bool = True,
    ) -> Dict[str, Any]:
        tagged_resources = self.list_tagged_resources(resource_type=resource_type)
        removed_assignments: List[Dict[str, Any]] = []

        for resource in tagged_resources:
            if resource.get("resourceType") != resource_type:
                continue
            resource_uri = self._extract_resource_uri(resource)
            for tag in self._extract_tags(resource):
                tag_name = self._extract_tag_value(tag)
                tag_id = self._extract_tag_id(tag)
                if not tag_id or not tag_name:
                    continue
                if tag_name not in current_site_names and cleanup_unassign:
                    try:
                        self.unassign_tag(resource, tag_id, resource_type)
                        removed_assignments.append(
                            {"resource_uri": resource_uri, "tag_id": tag_id, "tag_name": tag_name}
                        )
                    except GreenlakeError:
                        continue

        deleted_tags: List[Dict[str, Any]] = []
        if delete_orphan_tags:
            all_tags = self.list_tags(filter_key=self.tag_key)
            assigned_tag_ids = {
                str(self._extract_tag_id(tag))
                for resource in tagged_resources
                for tag in self._extract_tags(resource)
                if self._extract_tag_id(tag)
            }
            for tag in all_tags:
                tag_name = self._extract_tag_value(tag)
                tag_id = self._extract_tag_id(tag)
                if not tag_id or not tag_name:
                    continue
                if tag_name not in current_site_names and str(tag_id) not in assigned_tag_ids:
                    try:
                        self.delete_tag(str(tag_id))
                        deleted_tags.append({"tag_id": tag_id, "tag_name": tag_name})
                    except GreenlakeError:
                        continue

        return {
            "removed_assignments": removed_assignments,
            "deleted_tags": deleted_tags,
        }

    # ------------------------------------------------------------------
    # Field extractors — ordered by what the GreenLake API docs specify
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_resource_uri(resource: Dict[str, Any]) -> Optional[str]:
        return resource.get("resourceUri") or resource.get("resource_uri")

    @staticmethod
    def _extract_resource_id(resource: Dict[str, Any]) -> Optional[str]:
        val = (
            resource.get("id")
            or resource.get("resourceId")
            or resource.get("resource_id")
            or resource.get("uuid")
            or ""
        )
        return str(val).strip() or None

    @staticmethod
    def _extract_assignment_id(resource: Dict[str, Any]) -> Optional[str]:
        # The tag-resources response uses "id" as the assignment record id
        return resource.get("id") or resource.get("assignmentId") or resource.get("tagResourceId")

    @staticmethod
    def _extract_tags(resource: Dict[str, Any]) -> List[Dict[str, Any]]:
        return resource.get("tags") or resource.get("tag") or []

    @staticmethod
    def _extract_tag_value(tag: Dict[str, Any]) -> Optional[str]:
        # docs: tag objects have "key" and "value"; value holds the site name
        return tag.get("value") or tag.get("name") or tag.get("tagName")

    @staticmethod
    def _extract_tag_id(tag: Dict[str, Any]) -> Optional[str]:
        val = tag.get("id") or tag.get("tagId") or tag.get("uuid") or tag.get("tag_id") or ""
        return str(val).strip() or None
