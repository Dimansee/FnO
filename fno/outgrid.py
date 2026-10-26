"""Every-combination outcomes (research/outcome_grid.py -> fno/outcomes_grid.json.gz), served to the Outcomes tab.

The file holds ~32,000 rows: [factor option indexes..., then per run (Rs 2L full, Rs 2L 2026, Rs 20k full, Rs 20k 2026):
final, worst fall %, win %, profit factor, trades]. It is loaded once into memory and filtered / sorted here, so the page
only ever receives the rows it shows.
"""
from __future__ import annotations

import gzip
import json
import os

PATH = os.path.join(os.path.dirname(__file__), "outcomes_grid.json.gz")
_g: dict = {"mtime": None, "data": None}
NM = 5   # metrics per run


def data() -> dict | None:
    if not os.path.exists(PATH):
        return None
    mt = os.path.getmtime(PATH)
    if _g["mtime"] != mt:
        with gzip.open(PATH, "rt") as f:
            _g["data"] = json.load(f)
        _g["mtime"] = mt
    return _g["data"]


def meta() -> dict:
    d = data()
    if not d:
        return {"ok": False, "error": "The every-combination study has not been run yet."}
    return {"ok": True, "generated": d["generated"], "periods": d["periods"], "runs": d["runs"], "metrics": d["metrics"],
            "factors": [{"name": f["name"], "options": f["options"]} for f in d["factors"]], "count": len(d["rows"]),
            "baseline": _row(d, d["baseline"]), "notes": d["notes"]}


def _row(d, r) -> dict:
    nf = len(d["factors"])
    runs = {}
    for i, k in enumerate(d["runs"]):
        v = r[nf + i * NM: nf + (i + 1) * NM]
        runs[k] = dict(zip(d["metrics"], v))
    labels = [d["factors"][j]["options"][r[j]] for j in range(nf)]
    changed = [f"{d['factors'][j]['name']}: {labels[j]}" for j in range(nf) if r[j]]
    return {"idx": r[:nf], "labels": labels, "changes": changed, "runs": runs}


def params(idx: list[int]) -> dict:
    """Backtest / Pine parameters for one combination (today's Autopilot + the chosen options)."""
    from . import portfolio_bt as PB
    d = data()
    if not d:
        raise ValueError("No combination data.")
    p = {**PB.BASELINE, "indices": ["NIFTY", "BANKNIFTY", "SENSEX"]}
    for f, i in zip(d["factors"], idx):
        if not 0 <= int(i) < len(f["options"]):
            raise ValueError("Unknown option.")
        p.update(f["changes"][int(i)])
    return p


def query(filters: dict | None = None, run: str = "200000_full", sort: str = "final", desc: bool = True, limit: int = 100,
          max_dd: float | None = None, both_better: bool = False, offset: int = 0) -> dict:
    d = data()
    if not d:
        return {"ok": False, "error": "The every-combination study has not been run yet.", "rows": []}
    nf = len(d["factors"])
    if run not in d["runs"]:
        raise ValueError("Unknown run.")
    ri = d["runs"].index(run)
    mi = d["metrics"].index(sort) if sort in d["metrics"] else 0
    col = nf + ri * NM + mi
    fdd = nf + ri * NM + 1
    other = run.replace("_full", "_y2026") if run.endswith("_full") else run.replace("_y2026", "_full")
    oi = d["runs"].index(other)
    base = d["baseline"]
    want = {int(k): int(v) for k, v in (filters or {}).items() if v is not None and str(v) != ""}
    rows = []
    for r in d["rows"]:
        if any(r[k] != v for k, v in want.items()):
            continue
        if max_dd is not None and (r[fdd] or 0) < -abs(max_dd):
            continue
        if both_better and not (r[nf + ri * NM] > base[nf + ri * NM] and r[nf + oi * NM] > base[nf + oi * NM]):
            continue
        rows.append(r)
    rows.sort(key=lambda r: (r[col] is None, (r[col] or 0) * (-1 if desc else 1)))
    return {"ok": True, "total": len(rows), "rows": [_row(d, r) for r in rows[offset:offset + max(1, min(int(limit), 500))]],
            "baseline": _row(d, base)}
