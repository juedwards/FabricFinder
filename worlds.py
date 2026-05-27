"""World name <-> product-ID lookup, backed by the Master Content List.xlsx.

content_sessions_monthly.world_product_id stores the content list's
EducationProductID (a GUID). This module lets the bot resolve human world
names to those IDs (to filter queries) and map IDs back to names (to label
results).
"""
import os

import openpyxl

XLSX = os.path.join(
    os.path.dirname(__file__), "memory", "xls_data", "Master Content List.xlsx"
)
SHEET = "ContentList"

_worlds = None          # list[dict]: name, product_id, seed_in_game, seed_adjusted
_by_id = None           # dict[str -> name]


def _load():
    global _worlds, _by_id
    if _worlds is not None:
        return _worlds
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    ws = wb[SHEET]
    rows = list(ws.iter_rows(min_row=1, values_only=True))
    header = rows[0]
    ci_name = header.index("OfficialName")
    ci_pid = header.index("EducationProductID")
    ci_sig = header.index("WorldSeedInGame")
    ci_sadj = header.index("WorldSeedAdjusted")

    out, by_id = [], {}
    for r in rows[1:]:
        name = r[ci_name]
        pid = r[ci_pid]
        if not (name or pid):
            continue
        entry = {
            "name": name,
            "product_id": pid,
            "seed_in_game": r[ci_sig],
            "seed_adjusted": r[ci_sadj],
        }
        out.append(entry)
        if pid:
            by_id[str(pid).lower()] = name
    _worlds, _by_id = out, by_id
    return out


def search_worlds(query, limit=25):
    """Substring (case-insensitive) search on world name.

    Returns matches ordered with exact/prefix hits first.
    """
    q = (query or "").strip().lower()
    if not q:
        return []
    hits = [w for w in _load() if q in (w["name"] or "").lower()]
    hits.sort(
        key=lambda w: (
            (w["name"] or "").lower() != q,                  # exact first
            not (w["name"] or "").lower().startswith(q),     # then prefix
            len(w["name"] or ""),
        )
    )
    return hits[:limit]


def names_for_ids(ids):
    """Map a list of product IDs to world names. Returns {id: name|None}."""
    _load()
    return {pid: _by_id.get(str(pid).lower()) for pid in ids}


def count():
    return len(_load())


if __name__ == "__main__":
    print(f"{count()} worlds loaded")
    for w in search_worlds("tutorial", limit=5):
        print(f"  {w['name']:40} {w['product_id']}")
