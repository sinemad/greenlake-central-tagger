from unittest.mock import patch

import pytest

from app.services.greenlake_tags import GreenlakeError, GreenlakeTagClient


@pytest.fixture
def client():
    c = GreenlakeTagClient("https://gl.example.com/", "id", "secret")
    c._access_token = "tok"
    c._token_expires_at = float("inf")
    return c


def test_endpoint_defaults(client):
    assert client.base_url == "https://gl.example.com"
    assert client.tags_endpoint == "/tags/v1/tags"


def test_tenant_prefix_applied_with_format():
    c = GreenlakeTagClient("https://gl", "i", "s", tenant_id="t1", tenant_path_prefix="/tenants/{tenant_id}/")
    assert c.tags_endpoint == "/tenants/t1/tags/v1/tags"
    assert c.resources_endpoint == "/tenants/t1/inventory/v1/resources"


def test_tenant_id_formats_endpoint_placeholder():
    c = GreenlakeTagClient("https://gl", "i", "s", tenant_id="t1", tags_endpoint="/t/{tenant_id}/tags")
    assert c.tags_endpoint == "/t/t1/tags"


def test_prefix_without_tenant_id_used_verbatim():
    c = GreenlakeTagClient("https://gl", "i", "s", tenant_path_prefix="/custom")
    assert c.tags_endpoint == "/custom/tags/v1/tags"


def test_paginate_list_response(client):
    with patch.object(client, "_request", return_value=[{"a": 1}]):
        assert client._paginate("/p") == [{"a": 1}]


def test_paginate_dict_pages(client):
    pages = [{"items": [{"i": 1}, {"i": 2}], "total": 3}, {"items": [{"i": 3}], "total": 3}]
    with patch.object(client, "_request", side_effect=pages) as req:
        assert len(client._paginate("/p")) == 3
    assert req.call_args_list[1].kwargs["params"]["offset"] == 2


def test_list_tags_filter(client):
    with patch.object(client, "_paginate", return_value=[]) as pg:
        client.list_tags("Key")
    assert pg.call_args.args[1] == {"filter": "key eq 'Key'"}


def test_assign_tag_payload(client):
    with patch.object(client, "_request", return_value={}) as req:
        client.assign_tag({"resourceUri": "/u/1", "id": "r1"}, "tag1", "ACCESS_POINT", "HQ")
    payload = req.call_args.kwargs["json_payload"]
    assert payload == {
        "resourceUri": "/u/1",
        "resourceType": "ACCESS_POINT",
        "tags": [{"id": "tag1", "key": "ArubaCentralSite", "value": "HQ"}],
        "resourceId": "r1",
    }


def test_assign_tag_requires_uri(client):
    with pytest.raises(GreenlakeError, match="resourceUri"):
        client.assign_tag({"id": "r1"}, "t", "ACCESS_POINT", "HQ")


def test_unassign_prefers_assignment_id(client):
    with patch.object(client, "_request", return_value={}) as req:
        client.unassign_tag({"id": "a1", "resourceUri": "/u"}, "t", "ACCESS_POINT")
    assert req.call_args.args == ("DELETE", "/tags/v1/tag-resources/a1")


def test_unassign_falls_back_to_uri(client):
    with patch.object(client, "_request", return_value={}) as req:
        client.unassign_tag({"resourceUri": "/u"}, "t1", "ACCESS_POINT")
    assert req.call_args.kwargs["params"] == {"resourceUri": "/u", "tagId": "t1"}


def test_unassign_without_identifiers_raises(client):
    with pytest.raises(GreenlakeError):
        client.unassign_tag({}, "t", "ACCESS_POINT")


def test_cleanup_removes_stale_assignments_and_orphan_tags(client):
    tagged = [{
        "id": "a1", "resourceType": "ACCESS_POINT", "resourceUri": "/u/1",
        "tags": [{"id": "t-old", "value": "Old"}, {"id": "t-keep", "value": "HQ"}],
    }]
    all_tags = [
        {"id": "t-old", "value": "Old"},
        {"id": "t-keep", "value": "HQ"},
        {"id": "t-orphan", "value": "Gone"},
    ]
    with patch.object(client, "list_tagged_resources", return_value=tagged), \
         patch.object(client, "list_tags", return_value=all_tags), \
         patch.object(client, "unassign_tag") as un, \
         patch.object(client, "delete_tag") as dl:
        out = client.cleanup_tags({"HQ"}, "ACCESS_POINT")
    un.assert_called_once()
    assert [r["tag_name"] for r in out["removed_assignments"]] == ["Old"]
    # t-old is still in the pre-cleanup assignment snapshot, so only the truly unassigned tag is deleted
    assert [t["tag_id"] for t in out["deleted_tags"]] == ["t-orphan"]
    dl.assert_called_once_with("t-orphan")


def test_cleanup_respects_flags_and_swallows_errors(client):
    tagged = [{"resourceType": "ACCESS_POINT", "resourceUri": "/u", "tags": [{"id": "t", "value": "Old"}]}]
    with patch.object(client, "list_tagged_resources", return_value=tagged), \
         patch.object(client, "unassign_tag", side_effect=GreenlakeError("x")), \
         patch.object(client, "list_tags") as lt:
        out = client.cleanup_tags(set(), "ACCESS_POINT", delete_orphan_tags=False)
    assert out == {"removed_assignments": [], "deleted_tags": []}
    lt.assert_not_called()
