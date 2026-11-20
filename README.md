# GreenLake Central Tagger

A Python FastAPI web app that reads site assignments for access points from Aruba Central and writes them as tags on the matching HPE GreenLake devices.

## Features

- Fetch Aruba Central site names (via `pycentral`) and per-AP site assignments (Classic Central and New Central endpoints, tried in order)
- Match each AP to a GreenLake device by serial number or MAC address
- Patch a site tag (default key `ArubaCentralSite`, value = site name) onto matched devices in batches of 25
- Also patch the AP's Central group as a second tag (default key `ArubaCentralGroup`), toggleable in the UI. APs with no group reported get the site tag only.
- Optionally restrict the sync to selected sites
- Report matched and unmatched APs
- Browser-based UI; credentials are entered at runtime and not stored server-side
- A `gateway-updater` sidecar that refreshes the Aruba gateway list in the UI from the developer docs

## Requirements

- Docker (the image uses Python 3.14)
- Aruba Central: API gateway URL and an access token
- HPE GreenLake: API client ID and client secret (OAuth client credentials). The API URL defaults to `https://global.api.greenlake.hpe.com`.

## Running locally with Docker

```bash
docker build -t greenlake-central-tagger .
docker run -p 8000:8000 greenlake-central-tagger
```

Then visit `http://localhost:8000`.

A health check is available at `http://localhost:8000/health`.

## Running with Docker Compose

```bash
docker compose up --build
```

Then visit `http://localhost:8000`.

A health check is available at `http://localhost:8000/health`.

## Configuration

The UI takes these values at runtime:

- Aruba Central API gateway URL and access token
- GreenLake client ID and client secret, plus optional API URL, site tag key, group tag key, and a checkbox to tag groups

Set `WEB_PORT` (see `.env.example`) to change the published host port. The container always listens on 8000.

### GreenLake endpoints used

- List devices: `GET /devices/v1/devices`
- Apply tags: `PATCH /devices/v2beta1/devices` (merge-patch, 25 devices per request)

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

All HTTP is mocked, so no credentials or network access are needed.

## Notes

The default tag keys are `ArubaCentralSite` (value = site name) and `ArubaCentralGroup` (value = Central group name). The group comes from the `group_name` field of the AP listing; if an endpoint doesn't report it, only the site tag is applied.

`app/services/greenlake_tags.py` (tag create/assign/delete, tenant endpoint overrides, and orphan-tag cleanup) is implemented and unit-tested but is **not** wired into the API or UI yet. The live sync path uses the device PATCH API only, so cleanup of outdated tags is not currently available from the app.
