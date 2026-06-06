"""
JSON-file persistence for the dashboard.

  data/dashboard/history/{city_slug}.json — most recent query result per city
  data/dashboard/favorites.json           — {properties: [zpid], cities: [slug]}

Writes are atomic (write to temp + rename) so concurrent reads never see torn JSON.
"""
import json
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = ROOT / "data" / "dashboard" / "history"
FAVORITES_PATH = ROOT / "data" / "dashboard" / "favorites.json"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

_LOCK = threading.Lock()  # serialize writes


def slugify_city(city: str) -> str:
    """'San Francisco, CA' -> 'san-francisco-ca'"""
    s = city.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


# ---- atomic write ----

def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as tf:
        json.dump(data, tf, indent=2, default=str)
        tmp_path = Path(tf.name)
    tmp_path.replace(path)


# ---- history (per-city, replace on re-query) ----

def save_history(city: str, payload: dict) -> str:
    slug = slugify_city(city)
    with _LOCK:
        _atomic_write_json(HISTORY_DIR / f"{slug}.json", payload)
    return slug


def load_history(city_slug: str) -> Optional[dict]:
    p = HISTORY_DIR / f"{city_slug}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def list_history() -> List[dict]:
    """Returns [{slug, city, count, queried_at}] sorted newest first."""
    out = []
    for p in HISTORY_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text())
            out.append({
                "slug": p.stem,
                "city": data.get("city", p.stem),
                "count": len(data.get("results", [])),
                "queried_at": data.get("queried_at"),
            })
        except Exception:
            continue
    out.sort(key=lambda x: x.get("queried_at") or "", reverse=True)
    return out


def delete_history(city_slug: str) -> bool:
    p = HISTORY_DIR / f"{city_slug}.json"
    if p.exists():
        p.unlink()
        return True
    return False


# ---- favorites ----

@dataclass
class Favorites:
    properties: List[str]
    cities: List[str]


def _normalize_property(item) -> Dict[str, str]:
    """Accept either old-format str(zpid) or new-format {zpid, city_slug}."""
    if isinstance(item, str):
        return {"zpid": item, "city_slug": None}
    return {"zpid": str(item.get("zpid")), "city_slug": item.get("city_slug")}


def _read_favorites() -> Favorites:
    if not FAVORITES_PATH.exists():
        return Favorites(properties=[], cities=[])
    try:
        d = json.loads(FAVORITES_PATH.read_text())
        props = [_normalize_property(p) for p in d.get("properties", []) if p]
        return Favorites(
            properties=props,
            cities=list(d.get("cities", [])),
        )
    except Exception:
        return Favorites(properties=[], cities=[])


def _write_favorites(f: Favorites) -> None:
    with _LOCK:
        _atomic_write_json(FAVORITES_PATH, {
            "properties": f.properties,
            "cities": f.cities,
        })


def get_favorites() -> Dict[str, list]:
    f = _read_favorites()
    return {"properties": f.properties, "cities": f.cities}


def favorite_property(zpid: str, on: bool, city_slug: Optional[str] = None) -> Dict[str, list]:
    f = _read_favorites()
    zpid = str(zpid)
    existing_idx = next(
        (i for i, p in enumerate(f.properties) if p.get("zpid") == zpid), None
    )
    if on:
        new_entry = {"zpid": zpid, "city_slug": city_slug}
        if existing_idx is None:
            f.properties.append(new_entry)
        elif city_slug:
            # Update city_slug if newly known
            f.properties[existing_idx] = new_entry
    else:
        if existing_idx is not None:
            f.properties.pop(existing_idx)
    _write_favorites(f)
    return get_favorites()


def favorite_city(slug: str, on: bool) -> Dict[str, list]:
    f = _read_favorites()
    if on and slug not in f.cities:
        f.cities.append(slug)
    elif not on and slug in f.cities:
        f.cities.remove(slug)
    _write_favorites(f)
    return get_favorites()
