# GreenLake Central Tagger

A Python FastAPI web app that syncs Aruba Central site names to HPE GreenLake tags and applies those tags to GreenLake access points.

## Features

- Authenticate to Aruba Central using `pycentral`
- Fetch Aruba Central site names
- Create matching HPE GreenLake tags using a tenant-aware endpoint mapping
- Apply tags to GreenLake access points by matching AP metadata
- Cleanup orphan site tags and outdated tag assignments
- Browser-based UI for runtime credentials and advanced endpoint override

## Requirements

- Python 3.14+
- Docker
- Aruba Central credentials including `base_url`, `username`, `password`, `client_id`, `client_secret`, and `customer_id`
- HPE GreenLake API URL and bearer token

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

The UI accepts Aruba Central credentials and GreenLake connection settings at runtime.

### Endpoint overrides

If your tenant requires a non-default path, use the advanced endpoint fields in the UI and optionally set a `tenant_id` plus `tenant_path_prefix` such as `/tenants/{tenant_id}`.

- Tags endpoint: `/tags/v1/tags`
- Resources endpoint: `/inventory/v1/resources`
- Tagged resources endpoint: `/tags/v1/tag-resources`
- Tag assignment endpoint: `/tags/v1/tag-resources`

### Cleanup

Enable cleanup to remove outdated tag assignments and delete orphan site tags that are no longer present in Aruba Central.

## Notes

The default tag key is `ArubaCentralSite`, and each Aruba site name is stored as the tag value. If your GreenLake tenant uses alternate tag or assignment APIs, update the endpoint overrides accordingly.
