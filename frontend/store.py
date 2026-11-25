"""How a sweep point is written to disk, fingerprinted, and read back.

Adapted from the hfss-transmon-sweep-tutorial's sweep_store.py (same storage
contract: one JSON per completed point, identity by content hash, everything
else derived). Changes: lengths are normalised to um before hashing (so
'19.3mm' and '19300um' are the same point), the default scope is the mergemon
assembly, and records carry the mergemon FoMs.

    import store as st
    pid = st.point_fingerprint(scope, knobs, component_params, settings)
    if st.find_existing_run("data", pid) is None:
        ...solve...
        st.save_run(record, "data")
    df = st.load_table("data")
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import platform
import socket
import sys
import time

SCOPE = {"design": "mergemon_automation.assembly", "builder": "build_design"}

# Settings that change runtime, never results: recorded, not hashed.
NON_IDENTIFYING_SETTINGS = {"n_cores", "n_tasks", "use_batch", "verbose"}

_LENGTH_TO_UM = {"nm": 1e-3, "um": 1.0, "mm": 1e3}
_OTHER_UNITS = ("nH", "pH", "fF", "pF", "GHz", "MHz", "ohm", "Ohm")


def to_number(v):
    """'302.5um' -> 302.5, '19.3mm' -> 19300.0 (lengths in um); other unit
    strings -> their number; plain numbers unchanged; else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    s = v.strip()
    for unit, k in _LENGTH_TO_UM.items():
        if s.endswith(unit):
            try:
                return float(s[: -len(unit)]) * k
            except ValueError:
                return None
    for unit in _OTHER_UNITS:
        if s.endswith(unit):
            try:
                return float(s[: -len(unit)])
            except ValueError:
                return None
    try:
        return float(s)
    except ValueError:
        return None


def _norm(v):
    """Normalise for hashing: numbers rounded, lengths in um, dicts sorted."""
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in sorted(v.items())}
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    n = to_number(v)
    if n is not None:
        return round(n, 6)
    return v


def point_fingerprint(scope: dict, knobs: dict, component_params: dict,
                      settings: dict) -> str:
    """16 hex chars over (scope, full knob vector incl. part choice, Lj/Cj/SOI,
    solver settings minus NON_IDENTIFYING_SETTINGS). Not hashed: host, paths,
    timestamps -- the same point solved elsewhere is the same point."""
    payload = {
        "scope": _norm(scope or {}),
        "knobs": _norm(knobs or {}),
        "component_params": _norm(component_params or {}),
        "settings": _norm({k: v for k, v in (settings or {}).items()
                           if k not in NON_IDENTIFYING_SETTINGS}),
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def make_run_name(prefix: str = "mergemon", label: str | None = None) -> str:
    """`{prefix}_{YYYYmmdd-HHMMSS}[_{label}]` -- names a run, never a point."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{prefix}_{stamp}_{label}" if label else f"{prefix}_{stamp}"


def design_tag(label: str | None = None, sweep: dict | None = None) -> str | None:
    """Readable part of a design name: `<label>_<knob>_<value>` for a sweep
    point, `<label>_base` for its base, `<label>` for a one-off run. AEDT
    names take no '.', so 151.25um -> 151p25um, 16.0 (Lj_nH) -> 16nH."""
    if not label:
        return None
    if sweep is None:
        return label
    knob, value = sweep.get("knob"), sweep.get("value")
    if not knob:
        return f"{label}_base"
    if knob in ("Lj_nH", "Cj_fF"):                       # -> Lj_16nH
        knob, unit = knob.split("_")
        value = f"{float(value):g}{unit}"
    value = str(value).replace(".", "p").replace("-", "m")
    return f"{label}_{knob}_{value}"


def design_name(point_id: str, tag: str | None = None) -> str:
    """AEDT design name: `<tag>_` + first 8 hex of the point_id, or `pt_` +
    the hex without a tag. The tag is for reading only; the hash keeps the
    name unique (tutorial gotcha 21: a name built from the swept knob alone
    collides -- e.g. cap_length=151.25um exists with two meander layouts)."""
    tag = "".join(c if c.isalnum() or c == "_" else "_" for c in tag) if tag else "pt"
    return f"{tag}_{point_id[:8]}"


def record_design_name(record: dict) -> str:
    """The readable design name for a stored record (label from its run_name)."""
    parts = str(record.get("run_name", "")).split("_", 2)
    label = parts[2] if len(parts) == 3 else None
    return design_name(record["point_id"], design_tag(label, record.get("sweep")))


def run_context(**extra) -> dict:
    """Where and with what a point was solved. Recorded, never hashed; every
    field guarded so provenance can never fail a run."""
    ctx = {"host": socket.gethostname(), "platform": platform.platform(),
           "python": sys.version.split()[0], "cwd": os.getcwd()}
    for name in ("qiskit_metal", "pyEPR", "numpy", "pandas"):
        try:
            ctx[f"{name}_version"] = getattr(__import__(name), "__version__", "unknown")
        except Exception as err:                       # noqa: BLE001
            ctx[f"{name}_version"] = f"<unreadable: {type(err).__name__}>"
    ctx.update(extra)
    return ctx


def save_run(record: dict, data_dir: str = "data") -> str:
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, f"{record['run_name']}_data.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, default=str)
    return path


def iter_runs(data_dir: str = "data"):
    """Every readable record; a corrupt file is skipped, not raised."""
    for path in sorted(glob.glob(os.path.join(data_dir, "*_data.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                yield json.load(fh)
        except Exception:                              # noqa: BLE001
            continue


def find_existing_run(data_dir: str, point_id: str):
    """Latest record with this point_id (matched on contents), or None."""
    hits = [r for r in iter_runs(data_dir) if r.get("point_id") == point_id]
    return max(hits, key=lambda r: str(r.get("timestamp") or "")) if hits else None


def _flatten(record: dict) -> dict:
    """One record -> one flat row: knobs (as numbers, lengths in um), the
    sweep tag, quality, and every FoM."""
    row = {k: record.get(k) for k in ("run_name", "point_id", "timestamp")}
    for k, v in (record.get("knobs") or {}).items():
        n = to_number(v)
        row[k] = n if n is not None else v
    for k, v in (record.get("component_params") or {}).items():
        row[k] = v
    sweep = record.get("sweep") or {}
    row["sweep_knob"] = sweep.get("knob")
    for k, v in (record.get("quality") or {}).items():
        row[k] = json.dumps(v) if isinstance(v, (dict, list)) else v
    for k, v in (record.get("fom") or {}).items():
        row[k] = v
    return row


def load_table(*data_dirs: str, dedupe: bool = True):
    """All points in these directories as one flat DataFrame (no solver)."""
    import pandas as pd
    rows = [_flatten(r) for d in (data_dirs or ("data",)) for r in iter_runs(d)]
    df = pd.DataFrame(rows)
    if df.empty or not dedupe or "point_id" not in df:
        return df
    df = df.sort_values("timestamp").drop_duplicates("point_id", keep="last")
    return df.reset_index(drop=True)


def write_csv(df, path: str = os.path.join("output", "sweep_database.csv")) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    df.to_csv(path, index=False)
    return path


def whatis(name_or_id: str, data_dir: str = "data"):
    """Resolve a design name (`<tag>_xxxxxxxx`, `pt_xxxxxxxx`, with or without
    the `_hfss` AEDT adds) or a point_id prefix back to its record(s)."""
    key = str(name_or_id).strip().removesuffix("_hfss").rsplit("_", 1)[-1]
    hits = [r for r in iter_runs(data_dir) if str(r.get("point_id", "")).startswith(key)]
    for r in hits:
        print(f"{r.get('run_context', {}).get('design_name')}  {r.get('run_name')}  "
              f"sweep={r.get('sweep')}")
        print(f"  fom     {r.get('fom')}")
        print(f"  quality {r.get('quality')}")
    return hits
