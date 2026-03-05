#!/usr/bin/env python3
"""Generate a Spain-only (ES) radio catalog from the Radio Browser API.

Outputs:
  - catalog/es/stations.json
  - catalog/es/sections.json
  - catalog/es/manifest.json

Design goals:
  - Only countrycode=ES
  - Prefer working stations (hidebroken=true)
  - Order by popularity (clickcount/votes)
  - Deduplicate by url_resolved + name
  - Keep the schema stable for the Android app

Run locally:
  python tools/generate_es_catalog.py

Runs on GitHub Actions daily.
"""

from __future__ import annotations

import json
import os
import random
import re
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

USER_AGENT = os.getenv("RADIOSTATION_UA", "RadioStationCatalogBot/1.0 (+https://github.com/Mikelo32es)")
TIMEOUT = 25

# How many stations to fetch (before dedupe/filter)
FETCH_LIMIT = int(os.getenv("ES_FETCH_LIMIT", "2500"))
# How many stations to keep after cleaning
KEEP_LIMIT = int(os.getenv("ES_KEEP_LIMIT", "800"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _dns_discover_servers() -> List[str]:
    """Discover servers via DNS lookup of all.api.radio-browser.info.

    Returns base URLs like https://de1.api.radio-browser.info
    """
    servers: List[str] = []
    try:
        infos = socket.getaddrinfo("all.api.radio-browser.info", 443)
        ips = sorted({i[4][0] for i in infos})
        # We can't reverse-resolve names reliably without extra deps.
        # Instead, fall back to the /json/servers endpoint on a known host.
        # Still, the DNS step is kept here because it's the recommended flow.
        _ = ips  # kept for debugging if needed
    except Exception:
        pass

    # Known public mirrors (docs recommend not hardcoding, but we keep a short fallback list)
    servers = [
        "https://de1.api.radio-browser.info",
        "https://nl1.api.radio-browser.info",
        "https://at1.api.radio-browser.info",
    ]
    random.shuffle(servers)
    return servers


def _request_json(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    r = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _pick_working_server() -> str:
    for base in _dns_discover_servers():
        try:
            _request_json(f"{base}/json/stats")
            return base
        except Exception:
            continue
    raise RuntimeError("No Radio Browser server reachable")


def _fetch_es_stations(base: str) -> List[Dict[str, Any]]:
    """Use advanced search with countrycode=ES.

    Note: the API docs say country field is deprecated; we use countrycode.
    """
    params = {
        "countrycode": "ES",
        "hidebroken": "true",
        "order": "clickcount",
        "reverse": "true",
        "limit": str(FETCH_LIMIT),
        "offset": "0",
    }
    return _request_json(f"{base}/json/stations/search", params=params)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _is_probably_bad(st: Dict[str, Any]) -> bool:
    url = (st.get("url_resolved") or "").strip()
    name = (st.get("name") or "").strip()
    if not url or not name:
        return True
    if url.startswith("http://") is False and url.startswith("https://") is False:
        return True
    # filter obvious non-audio / placeholders
    if any(x in url.lower() for x in [".m3u8?", "example.com", "localhost", "127.0.0.1"]):
        return True
    return False


def _score(st: Dict[str, Any]) -> Tuple[int, int, int]:
    # Primary: clickcount, then votes, then bitrate
    click = int(st.get("clickcount") or 0)
    votes = int(st.get("votes") or 0)
    bitrate = int(st.get("bitrate") or 0)
    return (click, votes, bitrate)


def _dedupe(stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best_by_key: Dict[str, Dict[str, Any]] = {}
    for st in stations:
        if _is_probably_bad(st):
            continue
        key = _norm(st.get("url_resolved") or "")
        # secondary defense against duplicates with different resolved urls
        name = _norm(st.get("name") or "")
        key2 = f"{name}::{key}"
        pick_key = key2 if name else key

        cur = best_by_key.get(pick_key)
        if cur is None or _score(st) > _score(cur):
            best_by_key[pick_key] = st

    cleaned = list(best_by_key.values())
    cleaned.sort(key=_score, reverse=True)
    return cleaned[:KEEP_LIMIT]


GENRE_RULES: List[Tuple[str, List[str]]] = [
    ("Noticias", ["news", "noticias", "información", "informacion", "actualidad"]),
    ("Deporte", ["sport", "deporte", "fútbol", "futbol", "marca"]),
    ("Música", ["music", "hits", "pop", "rock", "dance", "electronic", "indie", "latina", "reggaeton", "flamenco", "rap", "hip hop"]),
    ("Clásica", ["classic", "clásica", "clasica", "opera", "symphony", "baroque", "barroco"]),
    ("Talk", ["talk", "podcast", "tertulia", "debate"]),
    ("Religión", ["religion", "religión", "misa", "gospel", "catolica", "católica"]),
]


def _pick_category(st: Dict[str, Any]) -> str:
    name = _norm(st.get("name") or "")
    tags = _norm(st.get("tags") or "")
    blob = f"{name} {tags}"

    for cat, needles in GENRE_RULES:
        if any(n in blob for n in needles):
            return cat

    # Regional / local fallback
    state = (st.get("state") or "").strip()
    if state:
        return "Regional"

    return "General"


def _to_catalog_station(st: Dict[str, Any]) -> Dict[str, Any]:
    name = (st.get("name") or "").strip()
    tags = (st.get("tags") or "").strip()
    state = (st.get("state") or "").strip()
    codec = (st.get("codec") or "").strip()

    subtitle_parts: List[str] = []
    if state:
        subtitle_parts.append(state)
    if tags:
        # take first 2 tags as human hint
        tag_list = [t.strip() for t in tags.split(",") if t.strip()][:2]
        if tag_list:
            subtitle_parts.append(" · ".join(tag_list))

    subtitle = " • ".join([p for p in subtitle_parts if p])

    return {
        "id": st.get("stationuuid"),
        "name": name,
        "subtitle": subtitle,
        "streamUrl": (st.get("url_resolved") or "").strip(),
        "homepage": (st.get("homepage") or "").strip(),
        "logoUrl": (st.get("favicon") or "").strip(),
        "countryCode": "ES",
        "state": state,
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
        "codec": codec,
        "bitrate": int(st.get("bitrate") or 0),
        "clickcount": int(st.get("clickcount") or 0),
        "votes": int(st.get("votes") or 0),
        "category": _pick_category(st),
        "lastCheckOk": bool(int(st.get("lastcheckok") or 0)),
    }


def _build_sections(stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Precompute per-category lists
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for s in stations:
        by_cat.setdefault(s["category"], []).append(s)

    def top(cat: str, limit: int = 30) -> List[str]:
        items = by_cat.get(cat, [])
        # already popularity-sorted in stations.json build, but keep deterministic
        items = sorted(items, key=lambda x: (x.get("clickcount", 0), x.get("votes", 0), x.get("bitrate", 0)), reverse=True)
        return [x["id"] for x in items[:limit]]

    # A "rompedor" home: simple but punchy sections
    sections = [
        {"id": "top_es", "title": "Top España", "subtitle": "Lo más escuchado", "stationIds": [s["id"] for s in stations[:40]]},
        {"id": "news", "title": "Noticias", "subtitle": "Actualidad y tertulia", "stationIds": top("Noticias", 40)},
        {"id": "sports", "title": "Deporte", "subtitle": "Partidos y programas", "stationIds": top("Deporte", 40)},
        {"id": "music", "title": "Música", "subtitle": "Hits, rock, dance…", "stationIds": top("Música", 60)},
        {"id": "regional", "title": "Regional", "subtitle": "Autonómicas y locales", "stationIds": top("Regional", 80)},
    ]

    # Remove empty sections
    return [s for s in sections if s["stationIds"]]


def main() -> int:
    out_dir = os.path.join("catalog", "es")
    os.makedirs(out_dir, exist_ok=True)

    base = _pick_working_server()
    raw = _fetch_es_stations(base)

    cleaned_raw = _dedupe(raw)

    catalog_stations = [_to_catalog_station(s) for s in cleaned_raw]

    # Stable sort: category then popularity then name
    def sort_key(s: Dict[str, Any]):
        return (
            s.get("category", ""),
            -(s.get("clickcount", 0)),
            -(s.get("votes", 0)),
            -(s.get("bitrate", 0)),
            s.get("name", ""),
        )

    catalog_stations.sort(key=sort_key)

    sections = _build_sections(catalog_stations)

    manifest = {
        "schemaVersion": 1,
        "generatedAt": _now_iso(),
        "source": "Radio Browser API",
        "server": base,
        "countryCode": "ES",
        "counts": {
            "fetched": len(raw),
            "kept": len(catalog_stations),
            "sections": len(sections),
        },
    }

    with open(os.path.join(out_dir, "stations.json"), "w", encoding="utf-8") as f:
        json.dump({"schemaVersion": 1, "generatedAt": manifest["generatedAt"], "stations": catalog_stations}, f, ensure_ascii=False, indent=2)

    with open(os.path.join(out_dir, "sections.json"), "w", encoding="utf-8") as f:
        json.dump({"schemaVersion": 1, "generatedAt": manifest["generatedAt"], "sections": sections}, f, ensure_ascii=False, indent=2)

    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"OK: generated {len(catalog_stations)} ES stations from {base}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise
