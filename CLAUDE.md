# CLAUDE.md

## Rules of engagement

- **Never run git commits or stage files** (`git add`, `git commit`, `git stash`, etc.). The owner handles all git write operations manually. Read-only git (`status`, `diff`, `log`) is fine.
- Never commit or print secrets. `.env` and `.env.*` are gitignored; only `.env.example` is tracked.

## Project

GreenLake Central Tagger: a FastAPI web app that reads per-AP site assignments from Aruba Central, matches each AP to a GreenLake device by serial or MAC, and PATCHes a site tag (default key `ArubaCentralSite`, value = site name) and a group tag (default key `ArubaCentralGroup`, value = Central group name, optional) onto the device via the GreenLake devices API. The group comes from `group_name` in the AP listing, so no extra pycentral calls are needed.

Credentials are entered in the browser UI at runtime. Nothing is stored server-side. Aruba Central is accessed through `pycentral` (`ArubaCentralBase.command()`) using a bearer access token. GreenLake auth is OAuth client credentials via plain `requests`.

`greenlake_tags.py` (tag objects, tenant endpoint overrides, cleanup) is **not wired into the app**. The live sync uses `greenlake_devices.py` only.

## Tech stack

- Python 3.14 (Docker `python:3.14-slim`), FastAPI, uvicorn, requests, python-multipart, beautifulsoup4, pycentral (see `requirements.txt`)
- Frontend: plain static HTML/JS, no build step (`app/static/index.html`, `app/static/app.js`)
- Docker and Docker Compose for running and dev

## Layout

- `app/main.py`: FastAPI app. Routes: `GET /`, `GET /health`, `POST /api/sites`, `POST /api/sync`. Pydantic request models and a global exception handler.
- `app/services/aruba_central.py`: `ArubaCentralClient`, `ArubaCentralError` (built on pycentral; sites and APs, with Classic/New Central endpoint fallback)
- `app/services/greenlake_tags.py`: `GreenlakeTagClient`, `GreenlakeError` (tag list/assign/unassign/delete and cleanup, tenant-aware endpoints). Unit-tested but unused by `main.py`.
- `app/services/greenlake_devices.py`: `GreenlakeDeviceClient`, `GreenlakeDeviceError` (paginated device list, serial/MAC lookup, tag PATCH in batches of 25). This is the live sync path.
- `tests/`: pytest suite with all HTTP mocked (`pytest.ini`, `requirements-dev.txt`)
- `scripts/update_gateways.py`: scrapes Aruba and HPE developer docs and rewrites the `<optgroup>` gateway lists in `app/static/index.html`, and also rewrites the `GREENLAKE_API_URL` constant in `app/main.py`. Run once, or loop with an interval argument.

## Running

```bash
docker compose up --build        # web UI on http://localhost:${WEB_PORT:-8000}
curl localhost:8000/health

pip install -r requirements.txt -r requirements-dev.txt
pytest                           # run tests
```

- `WEB_PORT` (see `.env.example`) sets the host port. The container always listens on 8000.
- `docker-compose.override.yml` is gitignored and local-only. It mounts `./app` and runs uvicorn with `--reload`.
- Compose also runs a `gateway-updater` sidecar that runs `update_gateways.py` every 86400s. It writes to the mounted `app/static`. Its edit to `app/main.py` (the `GREENLAKE_API_URL` constant) only changes the sidecar's own container copy, because `main.py` isn't mounted there. The web container never sees it.

## Conventions

- Live GreenLake endpoints: `GET /devices/v1/devices` and `PATCH /devices/v2beta1/devices` (merge-patch). `GreenlakeTagClient` defaults: tags `/tags/v1/tags`, resources `/inventory/v1/resources`, tag-resources `/tags/v1/tag-resources`, with optional `tenant_id` and `tenant_path_prefix` overrides. Keep those overrides working if you touch it.
- pycentral calls `exit()`/`sys.exit()` on bad input and on connection failures. `ArubaCentralClient` catches `SystemExit` and re-raises `ArubaCentralError`. Keep that wrapper around every pycentral call, or a bad request can kill the server worker.
- Each service module defines its own exception type. Raise it from the service and let `main.py` map it to an HTTP response.
- Normalize MACs and serials before comparing them (helpers exist in the service modules).
- Don't hand-edit the generated `<optgroup>` blocks in `index.html`. Change `update_gateways.py` instead.

## Known gaps

- Group names are read from the AP listing. Classic Central (`/monitoring/v2/aps`) reports `group_name`. Unverified for the New Central endpoint. If it's missing there, only the site tag is applied. `pycentral`'s `configuration.get_devices_group` is the per-device fallback if that's needed.
- Cleanup of outdated tags/assignments is implemented in `GreenlakeTagClient` but not exposed by the API or UI.
- No CI config exists.

## Active objectives

<!-- Fill these in. Example format: -->
- [ ] TODO: owner to list objectives here
