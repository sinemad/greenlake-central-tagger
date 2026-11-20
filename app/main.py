import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.services.aruba_central import ArubaCentralClient, ArubaCentralError
from app.services.greenlake_devices import GreenlakeDeviceClient, GreenlakeDeviceError

logger = logging.getLogger("uvicorn.error")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

GREENLAKE_API_URL = "https://global.api.greenlake.hpe.com"

app = FastAPI(title="GreenLake Central Tagger", version="0.1.0")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Log details server-side only; never return exception text or tracebacks to clients.
    logger.error(
        "Unhandled error on %s %s", request.method, request.url.path, exc_info=exc
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ArubaCentralConfig(BaseModel):
    base_url: str = Field(..., title="Aruba Central API Gateway URL")
    access_token: str = Field(..., title="Aruba Central access token")


class GreenlakeConfig(BaseModel):
    api_url: str = Field(GREENLAKE_API_URL, title="GreenLake API base URL")
    client_id: str = Field(..., title="GreenLake API client ID")
    client_secret: str = Field(..., title="GreenLake API client secret")
    tag_key: str = Field("ArubaCentralSite", title="Tag key applied to matched devices")
    tag_groups: bool = Field(True, title="Also tag devices with their Central group")
    group_tag_key: str = Field("ArubaCentralGroup", title="Tag key for the Central group")


class SitesRequest(BaseModel):
    aruba: ArubaCentralConfig


class SyncRequest(BaseModel):
    aruba: ArubaCentralConfig
    greenlake: GreenlakeConfig
    selected_sites: Optional[List[str]] = Field(
        None, title="Site names to sync; all sites if omitted"
    )


@app.get("/")
async def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/sites")
async def get_sites(body: SitesRequest):
    try:
        client = ArubaCentralClient(
            base_url=body.aruba.base_url,
            access_token=body.aruba.access_token,
        )
        site_names = client.get_site_names()
    except ArubaCentralError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"sites": site_names}


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "GreenLake Central Tagger", "version": "0.1.0"}


@app.post("/api/sync")
async def sync_tags(body: SyncRequest):
    selected: Optional[Set[str]] = (
        set(body.selected_sites) if body.selected_sites is not None else None
    )

    if body.greenlake.tag_groups and body.greenlake.group_tag_key == body.greenlake.tag_key:
        raise HTTPException(
            status_code=400,
            detail="Site tag key and group tag key must be different.",
        )

    # 1. Fetch APs with site assignments from Aruba Central
    try:
        aruba = ArubaCentralClient(
            base_url=body.aruba.base_url,
            access_token=body.aruba.access_token,
        )
        all_aps = aruba.get_aps_with_sites()
    except ArubaCentralError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Filter to selected sites if specified
    if selected is not None:
        all_aps = [ap for ap in all_aps if ap["site_name"] in selected]

    if not all_aps:
        return {
            "site_count": 0,
            "ap_count": 0,
            "matched": [],
            "unmatched": [],
            "patch_results": [],
        }

    # 2. Fetch all GreenLake devices and build serial/mac → device_id lookup
    try:
        gl = GreenlakeDeviceClient(
            api_url=body.greenlake.api_url,
            client_id=body.greenlake.client_id,
            client_secret=body.greenlake.client_secret,
            tag_key=body.greenlake.tag_key,
        )
        devices = gl.list_devices()
        lookup = gl.build_device_lookup(devices)
    except GreenlakeDeviceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # 3. Match each AP to a GreenLake device
    # (site, group-or-None) -> device ids; devices in the same pair share one PATCH payload
    tag_targets: Dict[Tuple[str, Optional[str]], List[str]] = defaultdict(list)
    matched: List[Dict[str, Any]] = []
    unmatched: List[Dict[str, Any]] = []

    for ap in all_aps:
        device_id = lookup.get(ap["serial"]) or lookup.get(ap["mac"])
        if device_id:
            group = (ap.get("group_name") or None) if body.greenlake.tag_groups else None
            tag_targets[(ap["site_name"], group)].append(device_id)
            matched.append({
                "ap_name": ap["ap_name"],
                "serial": ap["serial"],
                "site": ap["site_name"],
                "group": group,
                "greenlake_device_id": device_id,
            })
        else:
            unmatched.append({
                "ap_name": ap["ap_name"],
                "serial": ap["serial"],
                "mac": ap["mac"],
                "site": ap["site_name"],
                "reason": "no matching GreenLake device found by serial or MAC",
            })

    # 4. PATCH tags onto matched devices, batched by site
    patch_results: List[Dict[str, Any]] = []
    try:
        for (site_name, group), device_ids in tag_targets.items():
            tags = {body.greenlake.tag_key: site_name}
            if group:
                tags[body.greenlake.group_tag_key] = group
            results = gl.patch_device_tags(device_ids, tags)
            patch_results.append({
                "site": site_name,
                "group": group,
                "device_count": len(device_ids),
                "batches": results,
            })
    except GreenlakeDeviceError as exc:
        raise HTTPException(status_code=500, detail=f"Tag patch failed: {exc}")

    sites_synced = sorted({site for site, _ in tag_targets})

    return {
        "site_count": len(sites_synced),
        "sites": sites_synced,
        "ap_count": len(all_aps),
        "matched": matched,
        "unmatched": unmatched,
        "patch_results": patch_results,
    }
