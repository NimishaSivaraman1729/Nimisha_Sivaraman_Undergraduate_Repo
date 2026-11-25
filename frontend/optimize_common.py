"""Shared pieces of optimize_multi.py (and deliver.py): the database of solved
points, the design family a fit may use, the shared-slope fit, the geometry
(conductor) check run before every solve, and the defaults they share.

Every HFSS solve is an ordinary point in the main database (data/), so later
optimisations and the frontend's kappa fit reuse it.
"""

from __future__ import annotations

import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
P_MIN = 0.9                                          # below: hybridised qubit
DATA_DIR = os.path.join(HERE, "data")                # the one database
HYBRID_GHZ = 0.3                                     # warn if f01, f_r this close
DEFAULT_BASE = dict(ground_margin="500um")           # the base of every sweep so far
LOG_DIR = os.path.join(HERE, "output", "optimize")
PRIOR_PARTS = dict(inductor="meander")               # legacy meander: lends its slope only
# Default bounds per knob (um) = the range each was swept over, so the fit
# interpolates (cap_length: caplen_fixed; the rest: star5).
KNOB_BOUNDS = {"cap_length": (151.25, 453.75), "ind_length": (9650.0, 28950.0),
               "t_length": (17.0, 51.0), "cap_finger_width": (1.25, 3.75),
               "cap_finger_gap": (1.9, 5.7)}



# ---------------------------------------------------------------------------
# the database -> points of one design family
# ---------------------------------------------------------------------------
def _same(a, b):
    from store import to_number
    na, nb = to_number(a), to_number(b)
    if na is not None and nb is not None:
        return abs(float(na) - float(nb)) <= 1e-6 * max(1.0, abs(float(nb)))
    return str(a) == str(b)


def _key(d):
    return json.dumps(d or {}, sort_keys=True, default=str)


def load_points(data_dirs=(DATA_DIR,)):
    """Every stored record as a flat DataFrame, plus `_parts` and `_setup`
    keys (JSON) so families and mesh groups can be told apart."""
    import pandas as pd
    import store as st
    rows = []
    for d in data_dirs:
        if not os.path.isdir(d):
            continue
        for r in st.iter_runs(d):
            row = st._flatten(r)
            row["_parts"] = _key(r.get("parts"))
            row["_setup"] = _key(r.get("settings"))
            rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return (df.sort_values("timestamp").drop_duplicates("point_id", keep="last")
              .reset_index(drop=True))


def family(df, fom, knob, lj_nH, cj_fF, base=None, parts=None, verbose=True):
    """The points that differ from the base design only in `knob` (a geometry
    knob, or Lj_nH / Cj_fF: then that junction value is the one left free)."""
    from assembly import all_knobs
    from parts.catalog import full_choice
    from simulate import JJ_MESH, JJ_MODEL
    if df.empty or fom not in df or knob not in df:
        return df.iloc[0:0] if not df.empty else df
    choice = full_choice(parts)
    nominal = dict(all_knobs(choice), **(base or {}))
    keep = (df["_parts"] == _key(choice)) & df[fom].notna()
    for col, want in (("Lj_nH", lj_nH), ("Cj_fF", cj_fF)):
        if col != knob:
            keep &= np.isclose(df[col].astype(float), want) if col in df else False
    if "soi" in df:
        keep &= df["soi"].isna()
    # e.g. recreate_old_si_single (one RLC sheet) has a spurious mode: never fit it
    for col, want in (("jj_model", JJ_MODEL), ("jj_mesh", JJ_MESH)):
        if col in df:
            keep &= df[col].apply(lambda val, w=want: _same(val, w))
    for k, v in nominal.items():
        if k != knob and k in df:
            keep &= df[k].apply(lambda val, v=v: _same(val, v))
    out = df[keep].copy()
    hyb = out["p_q_total"].astype(float) < P_MIN if "p_q_total" in out else False
    if verbose and np.any(hyb):
        print(f"  ignoring {int(np.sum(hyb))} hybridised point(s) (p_q < {P_MIN})")
    out = out[~hyb] if np.any(hyb) else out
    out["x"] = out[knob].astype(float)
    return out.sort_values("x").reset_index(drop=True)


def fit_shared_slope(groups):
    """y = y0_g + k x: one slope, one offset per group. groups: [(x, y)].
    Returns (k, [y0_g])."""
    groups = [(np.asarray(x, float), np.asarray(y, float)) for x, y in groups if len(x)]
    n = sum(len(x) for x, _ in groups)
    A, b, i = np.zeros((n, 1 + len(groups))), np.zeros(n), 0
    for g, (x, y) in enumerate(groups):
        A[i:i + len(x), 0], A[i:i + len(x), 1 + g], b[i:i + len(x)] = x, 1.0, y
        i += len(x)
    sol = np.linalg.lstsq(A, b, rcond=None)[0]
    return float(sol[0]), [float(v) for v in sol[1:]]


# The conductor check exports a GDS (gdspy). Where this env lacks gdspy it runs
# in GEOMETRY_ENV instead, through `conda run` (site_config.py: per machine).
from site_config import GEOMETRY_ENV, geometry_python  # noqa: E402


def _check_geometry(knobs, parts):
    try:
        import gdspy  # noqa: F401
    except ImportError:
        return _check_geometry_in_env(knobs, parts)
    from sweep import check_geometry
    try:
        return check_geometry(knobs, parts)[0]
    except Exception as err:                            # noqa: BLE001
        print(f"    geometry build failed: {type(err).__name__}: {err}")
        return 0


def _check_geometry_in_env(knobs, parts, env=GEOMETRY_ENV):
    import subprocess
    if not env:
        print("    gdspy is not importable here and site_config.GEOMETRY_ENV is empty: "
              "install gdspy or set GEOMETRY_ENV (local_settings.json, see site_config.py)")
        return 0
    args = os.path.join(HERE, "output", "_geometry_check.json")   # stays under this folder
    os.makedirs(os.path.dirname(args), exist_ok=True)
    with open(args, "w", encoding="utf-8") as fh:
        json.dump(dict(knobs=knobs, parts=parts), fh)
    code = ("import json, sys; from sweep import check_geometry; "
            "a = json.load(open(sys.argv[1])); "
            "print('CONDUCTORS', check_geometry(a['knobs'], a['parts'])[0])")
    try:
        out = subprocess.run(geometry_python() + ["-B", "-c", code, args],
                             cwd=HERE, capture_output=True, text=True, timeout=900)
        hits = [ln for ln in out.stdout.splitlines() if ln.startswith("CONDUCTORS")]
        if hits:
            return int(hits[-1].split()[1])
        print(f"    geometry check in {env} gave no result:\n{out.stderr[-1500:]}")
    except Exception as err:                            # noqa: BLE001
        print(f"    geometry check in {env} failed: {type(err).__name__}: {err}")
    finally:
        if os.path.exists(args):
            os.remove(args)
    return 0
