from unittest.mock import MagicMock

import pytest


def make_response(json_data=None, status=200, text=None, reason="OK"):
    """Build a minimal stand-in for requests.Response."""
    import json as _json

    resp = MagicMock()
    resp.status_code = status
    resp.ok = 200 <= status < 300
    resp.reason = reason
    resp.text = text if text is not None else (_json.dumps(json_data) if json_data is not None else "")
    resp.json.side_effect = lambda: _json.loads(resp.text)
    return resp


@pytest.fixture
def resp():
    return make_response
