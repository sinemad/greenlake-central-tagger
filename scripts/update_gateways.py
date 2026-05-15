#!/usr/bin/env python3
"""
Fetches Aruba Central API gateway tables from the developer docs and rewrites
the <optgroup> sections in app/static/index.html.

Usage:
    python scripts/update_gateways.py [interval_seconds]

If interval_seconds is given the script loops indefinitely, sleeping that many
seconds between runs.  Omit it to run once and exit.
"""

import logging
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

CLASSIC_URL = (
    "https://developer.arubanetworks.com/central/docs/api-oauth-access-token"
)
NEW_CENTRAL_URL = (
    "https://developer.arubanetworks.com/new-central/docs/getting-started-with-rest-apis"
)
GREENLAKE_DOCS_URL = "https://developer.hpe.com/greenlake/hpe-greenlake-platform/home/"

INDEX_HTML = Path(__file__).resolve().parent.parent / "app" / "static" / "index.html"
MAIN_PY = Path(__file__).resolve().parent.parent / "app" / "main.py"

SKIP_LABELS = {"internal"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; GreenLake-Central-Tagger/1.0; gateway-updater)"
    )
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _col_index(headers: list[str], *keywords: str) -> int | None:
    for i, h in enumerate(headers):
        if any(kw in h for kw in keywords):
            return i
    return None


def _normalise_url(raw: str) -> str:
    raw = raw.strip()
    if raw and not raw.startswith("http"):
        raw = "https://" + raw
    return raw


def _parse_gateway_table(
    soup: BeautifulSoup,
    label_keywords: tuple[str, ...],
    url_keywords: tuple[str, ...],
    skip_labels: set[str] = SKIP_LABELS,
) -> list[tuple[str, str]]:
    """Return [(region_label, url), ...] from the first matching table."""
    for table in soup.find_all("table"):
        raw_headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not raw_headers:
            continue

        label_col = _col_index(raw_headers, *label_keywords)
        url_col = _col_index(raw_headers, *url_keywords)

        if label_col is None or url_col is None:
            continue

        results = []
        for row in table.find_all("tr")[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) <= max(label_col, url_col):
                continue
            label = cells[label_col].get_text(strip=True)
            url = _normalise_url(cells[url_col].get_text(strip=True))
            if label.lower() in skip_labels:
                continue
            if label and url.startswith("http"):
                results.append((label, url))

        if results:
            return results

    return []


def fetch_classic_gateways() -> list[tuple[str, str]]:
    log.info("Fetching Classic Central gateway page…")
    soup = fetch_soup(CLASSIC_URL)
    gateways = _parse_gateway_table(
        soup,
        label_keywords=("region", "cluster"),
        url_keywords=("url", "domain", "gateway"),
    )
    log.info("  Classic Central: %d gateways found", len(gateways))
    return gateways


def fetch_new_central_gateways() -> list[tuple[str, str]]:
    log.info("Fetching New Central gateway page…")
    soup = fetch_soup(NEW_CENTRAL_URL)
    gateways = _parse_gateway_table(
        soup,
        label_keywords=("cluster", "region"),
        url_keywords=("base url", "url", "base"),
    )
    log.info("  New Central: %d gateways found", len(gateways))
    return gateways


# ---------------------------------------------------------------------------
# HTML rewriting
# ---------------------------------------------------------------------------

_INDENT_OPTGROUP = "          "   # 10 spaces — matches current index.html
_INDENT_OPTION = "            "   # 12 spaces


def _build_optgroup(label: str, gateways: list[tuple[str, str]]) -> str:
    lines = [f'{_INDENT_OPTGROUP}<optgroup label="{label}">']
    for region, url in gateways:
        lines.append(f'{_INDENT_OPTION}<option value="{url}">{region}</option>')
    lines.append(f"{_INDENT_OPTGROUP}</optgroup>")
    return "\n".join(lines)


def _replace_optgroup(html: str, label: str, gateways: list[tuple[str, str]]) -> str:
    new_block = _build_optgroup(label, gateways)
    pattern = rf'[ \t]*<optgroup label="{re.escape(label)}">.*?</optgroup>'
    return re.sub(pattern, new_block, html, flags=re.DOTALL)


def update_index_html(
    classic: list[tuple[str, str]],
    new_central: list[tuple[str, str]],
) -> bool:
    """Rewrite optgroup blocks in index.html. Returns True if file changed."""
    original = INDEX_HTML.read_text(encoding="utf-8")
    updated = original

    if classic:
        updated = _replace_optgroup(updated, "Classic Central", classic)
    if new_central:
        updated = _replace_optgroup(updated, "New Central", new_central)

    if updated == original:
        log.info("No changes detected in gateway lists.")
        return False

    INDEX_HTML.write_text(updated, encoding="utf-8")
    log.info("index.html updated.")
    return True


# ---------------------------------------------------------------------------
# GreenLake API URL
# ---------------------------------------------------------------------------

_GL_URL_RE = re.compile(r'https://[a-z0-9.-]+\.api\.greenlake\.hpe\.com\b')


def fetch_greenlake_api_url() -> str | None:
    """Return the GreenLake API gateway URL found on the HPE developer page."""
    log.info("Fetching GreenLake API gateway URL from %s…", GREENLAKE_DOCS_URL)
    soup = fetch_soup(GREENLAKE_DOCS_URL)
    text = soup.get_text(" ")
    match = _GL_URL_RE.search(text)
    if match:
        url = match.group(0).rstrip("/")
        log.info("  GreenLake API URL: %s", url)
        return url
    log.warning("  Could not find a GreenLake API URL on the docs page.")
    return None


def update_greenlake_url(new_url: str) -> bool:
    """Patch GREENLAKE_API_URL in main.py and the display link in index.html.
    Returns True if either file changed."""
    changed = False

    # --- main.py ---
    main_src = MAIN_PY.read_text(encoding="utf-8")
    new_main = re.sub(
        r'(GREENLAKE_API_URL\s*=\s*")[^"]+(")',
        rf'\g<1>{new_url}\g<2>',
        main_src,
    )
    if new_main != main_src:
        MAIN_PY.write_text(new_main, encoding="utf-8")
        log.info("main.py: GREENLAKE_API_URL updated to %s", new_url)
        changed = True
    else:
        log.info("main.py: GREENLAKE_API_URL unchanged.")

    # --- index.html (the display link) ---
    html_src = INDEX_HTML.read_text(encoding="utf-8")
    new_html = re.sub(
        r'(<a id="gl-api-url-display"[^>]*href=")[^"]+("[^>]*>)[^<]*(</a>)',
        rf'\g<1>{new_url}\g<2>{new_url}\g<3>',
        html_src,
    )
    if new_html != html_src:
        INDEX_HTML.write_text(new_html, encoding="utf-8")
        log.info("index.html: GreenLake display URL updated to %s", new_url)
        changed = True
    else:
        log.info("index.html: GreenLake display URL unchanged.")

    return changed


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_once() -> None:
    classic, new_central = [], []

    try:
        classic = fetch_classic_gateways()
    except Exception as exc:
        log.error("Failed to fetch Classic Central gateways: %s", exc)

    try:
        new_central = fetch_new_central_gateways()
    except Exception as exc:
        log.error("Failed to fetch New Central gateways: %s", exc)

    if not classic and not new_central:
        log.warning("No gateways found on either page — skipping update to avoid data loss.")
    else:
        update_index_html(classic, new_central)

    try:
        gl_url = fetch_greenlake_api_url()
        if gl_url:
            update_greenlake_url(gl_url)
    except Exception as exc:
        log.error("Failed to fetch/update GreenLake API URL: %s", exc)


def main() -> None:
    if len(sys.argv) > 1:
        interval = int(sys.argv[1])
        log.info("Running in loop mode, interval=%ds.", interval)
        while True:
            run_once()
            log.info("Next check in %ds.", interval)
            time.sleep(interval)
    else:
        run_once()


if __name__ == "__main__":
    main()
