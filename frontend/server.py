"""Mergemon design front end: a local web app over the automation.

    frontend\\run_frontend.bat            # picks a Python (launch.py); opens http://localhost:8765
    python -B frontend\\server.py         # or directly, with any Python that has numpy + shapely

Runs on any computer: without qiskit-metal / pyEPR / AEDT the design screen,
checks and results still work, and the jobs that need them are refused with a
message saying what is missing (capabilities.py, /api/capabilities).

Tabs: Substrate -> Design (qubit, readout resonator, coupling capacitor) ->
Figures of merit (L_J, C_J, f_r, chi) -> Results; the design screen beside them
draws the parts as they are picked. "Optimize" runs optimize_multi.py (which
delivers the request folder on convergence: fab GDS, own .aedt, summary.png).

Every option is listed; the ones without a simulation behind them yet are
marked "planned" and block Optimize. Only standard library on the server side
(no web framework in the env), plain HTML/JS on the page. Binds to localhost.
"""

from __future__ import annotations

import glob
import json
import mimetypes
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE                     # code, data/, output/ and delivery folders all live here
STATIC = os.path.join(HERE, "static")
sys.path.insert(0, HERE)

import capabilities as caps  # noqa: E402
from store import to_number  # noqa: E402

try:                                  # numpy + shapely: the design screen (parts/geometry.py)
    from parts import geometry as g  # noqa: E402
    from parts.catalog import DEFAULT_PARTS, default_knobs  # noqa: E402
    from shapely.ops import unary_union  # noqa: E402
    GEOMETRY_ERROR = None
except ImportError as _err:
    GEOMETRY_ERROR = (f"design screen off: {_err} (pip install numpy shapely, or start with "
                      "run_frontend.bat, which picks a Python that has them)")
    DEFAULT_PARTS = {}

PORT = 8765
LOG_DIR = os.path.join(ROOT, "output", "optimize")
BASE = dict(ground_margin="500um")          # optimize_common.DEFAULT_BASE: every sweep's base
SIMULATED_THICKNESS_UM = 750.0              # DesignPlanar's size_z: what HFSS renders

# ---------------------------------------------------------------------------
# What can be picked, and what each choice means for the simulation. status:
# "simulated" (runs today), "planned" (listed, not built), "legacy" (data only).
# parts: the catalog roles a choice sets (only for simulated choices).
# ---------------------------------------------------------------------------
OPTIONS = {
    "substrate": {
        "soi": [
            dict(id="bulk", label="Bulk silicon (not SOI)", status="simulated",
                 note="high-resistivity Si, eps_r 11.9 (HFSS 'silicon')"),
            dict(id="soi", label="SOI", status="planned",
                 note="device layer / BOX / handle stack; simulate.run_point(soi=...) is a stub"),
        ],
        "thickness_um": dict(default=750.0, simulated=[SIMULATED_THICKNESS_UM]),
    },
    "qubit": [
        dict(id="squid", label="SQUID", status="simulated",
             note="DC SQUID, two merged-element junctions in parallel (the shape simulated so far)",
             parts=dict(squid="dc_squid")),
        dict(id="single", label="Single junction + pads", status="planned",
             note="pad geometry selection: not built yet"),
    ],
    "resonator": [
        dict(id="lumped", label="Lumped LC", status="simulated",
             note="interdigitated capacitor + meander inductor in the qubit pocket",
             parts=dict(res_cap="three_finger", inductor="meander_fixed", column="lanes")),
        dict(id="lambda4", label="\u03bb/4 CPW", status="planned", note="not built yet"),
        dict(id="lambda2", label="\u03bb/2 CPW", status="planned", note="not built yet"),
    ],
    "res_cap": [
        dict(id="three_finger", label="Three-finger interdigitated", status="simulated",
             note="cap_length sets C_r"),
        dict(id="other_cap", label="Other C geometry", status="planned", note="later"),
    ],
    "res_ind": [
        dict(id="meander_fixed", label="Fixed-span meander", status="simulated",
             note="ind_length sets L_r; span fixed, so cap_length leaves it alone"),
        dict(id="meander", label="Legacy meander", status="legacy",
             note="runs follow the pocket; star5 data only, no new simulations"),
        dict(id="other_ind", label="Other L geometry", status="planned", note="later"),
    ],
    "coupler": [
        dict(id="t", label="T", status="simulated",
             note="T on the SQUID island; t_length sets C_g, so g and chi",
             parts=dict(coupler="t")),
        dict(id="pads", label="Pads", status="planned",
             note="readout-pad style, as in hfss-transmon-sweep-tutorial: not built yet"),
    ],
    "feedline": [
        dict(id="none", label="None", status="simulated",
             note="no readout line: f_r, f01, chi, g only (real eigenproblem)"),
        dict(id="met181", label="Clipped feedline", status="simulated",
             note="from MET_ver_1_81.gds (left device): CPW 35.3/3.7 um + coupling finger "
                  "beside the column, cut at its first bend, 50 ohm end -> kappa, Q_r. Its "
                  "kappa is ~3x the whole line's: use the whole feedline for kappa",
             parts=dict(feedline="met181")),
        dict(id="met181_full", label="Whole feedline", status="simulated",
             note="the same line followed out to its wirebond launchpad as in the GDS (two "
                  "bends, taper, pad), 50 ohm behind the pad -> kappa, Q_r; ~4.2 x 2.7 mm",
             parts=dict(feedline="met181_full")),
    ],
    "flux_line": [
        dict(id="none", label="None", status="simulated", note="no flux line"),
        dict(id="met181", label="Flux line", status="simulated",
             note="from MET_ver_1_81.gds (left device): CPW tapering onto the SQUID, "
                  "shorted above the loop, 50 ohm end -> qubit Q (Purcell T1)",
             parts=dict(flux_line="met181")),
    ],
}

ROLE_OF_CHOICE = {"qubit": ("squid",), "resonator": ("res_cap", "inductor", "column"),
                  "coupler": ("coupler",)}
LINE_GROUPS = ("feedline", "flux_line")      # optional parts: "none" or a variant


def _status(group, cid):
    return next((o["status"] for o in OPTIONS[group] if o["id"] == cid), None)


def check_state(s):
    """[(field, message)] of what keeps this state from running today."""
    issues = []
    sub = s.get("substrate", {})
    if sub.get("soi") not in ("bulk", "soi"):
        issues.append(("substrate", "pick a wafer (bulk silicon or SOI)"))
    elif sub.get("soi") == "soi":
        issues.append(("substrate", "SOI is planned: simulations are bulk silicon"))
    if abs(float(sub.get("thickness_um") or 0) - SIMULATED_THICKNESS_UM) > 1e-6:
        issues.append(("substrate", f"substrate thickness: only {SIMULATED_THICKNESS_UM:g} um "
                                    "is simulated (the thickness knob is planned)"))
    d = s.get("design", {})
    for group in ("qubit", "resonator", "coupler"):
        cid = d.get(group)
        if not cid:
            issues.append(("design", f"pick a {group}"))
        elif _status(group, cid) != "simulated":
            issues.append(("design", f"{group}: '{cid}' is planned"))
    if d.get("resonator") == "lumped":
        for group in ("res_cap", "res_ind"):
            st = _status(group, d.get(group))
            if st != "simulated":
                issues.append(("design", f"{group}: '{d.get(group)}' is {st or 'not picked'}"))
    for group in LINE_GROUPS:
        cid = d.get(group) or "none"
        if _status(group, cid) != "simulated":
            issues.append(("design", f"{group}: '{cid}' is not available"))
    f = s.get("fom", {})
    for k, lab in (("lj_total_nH", "L_J"), ("cj_total_fF", "C_J"), ("fr_GHz", "f_r"),
                   ("chi_MHz", "chi")):
        try:
            if float(f.get(k)) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            issues.append(("fom", f"{lab} must be a positive number"))
    k = kappa_target(s)
    if k and k.get("error"):
        issues.append(("fom", k["error"]))
    return issues


# ---------------------------------------------------------------------------
# kappa target -> feedline finger length, from the solved whole-feedline points
# ---------------------------------------------------------------------------
KAPPA_FEEDLINE = "met181_full"
KAPPA_MIN_FINGER_UM = 134.5             # the finger must still span the feedline


def kappa_model():
    """kappa = a (L - L0)^2 (kappa ~ C_kappa^2, C_kappa ~ finger length facing the
    column), least squares on sqrt(kappa) over every solved met181_full point.
    None until 3 finger lengths are solved."""
    import numpy as np
    import store as st
    latest = {}
    for r in st.iter_runs(os.path.join(ROOT, "data")):
        k, L = (r.get("fom") or {}).get("kappa_MHz"), (r.get("knobs") or {}).get("feed_pad_length")
        if (r.get("parts") or {}).get("feedline") == KAPPA_FEEDLINE and k and L:
            latest[r["point_id"]] = (to_number(L), float(k))
    pts = sorted(latest.values())
    if len({L for L, _ in pts}) < 3:
        return None
    L, k = np.array(pts).T
    s, c = np.polyfit(L, np.sqrt(k), 1)
    fit = (L + c / s) ** 2 * s * s
    return dict(a=float(s * s), L0=float(-c / s), points=pts,
                max_resid_pct=float(np.max(np.abs(k - fit) / fit) * 100),
                L_range=(float(L.min()), float(L.max())),
                k_range=(float(fit.min()), float(fit.max())))


def kappa_target(s):
    """None without a kappa target; else dict(finger_um, note) or dict(error)."""
    raw = (s.get("fom") or {}).get("kappa_MHz")
    if raw in (None, ""):
        return None
    try:
        k = float(raw)
        if k <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return dict(error="kappa must be a positive number (or empty: no kappa target)")
    if (s.get("design") or {}).get("feedline") != KAPPA_FEEDLINE:
        return dict(error="a kappa target needs the whole feedline (Design tab: Readout "
                          "feedline -> Whole feedline)")
    if GEOMETRY_ERROR:
        return dict(error=GEOMETRY_ERROR)
    m = kappa_model()
    if m is None:
        return dict(error="no kappa data yet: solve a feed_pad_length sweep on the whole feedline first (Sweep tab)")
    lo, hi = m["k_range"]
    if not lo <= k <= hi:
        return dict(error=f"kappa {k:g} MHz is outside the simulated range "
                          f"{lo:.2f}-{hi:.2f} MHz (finger {m['L_range'][0]:g}-"
                          f"{m['L_range'][1]:g} um)")
    L = m["L0"] + (k / m["a"]) ** 0.5
    return dict(finger_um=round(max(L, KAPPA_MIN_FINGER_UM), 1),
                note=f"from {len(m['points'])} solved whole-feedline points, fit "
                     f"kappa = a (L - {m['L0']:.0f} um)^2. Not 100% accurate: the points sit "
                     f"up to {m['max_resid_pct']:.0f}% off the fit (10-pass solves, the "
                     f"longest fingers least trustworthy). The finger length is set from "
                     f"this; the optimizer still targets only f_r and chi.")


# ---------------------------------------------------------------------------
# design screen: the chosen parts, drawn from parts/geometry.py (pure shapely)
# ---------------------------------------------------------------------------
def _mm(v):
    x = to_number(v)                       # lengths -> um
    return x / 1e3 if x is not None else v


def _parts_for(design):
    parts = dict(DEFAULT_PARTS)
    if design.get("res_ind") in ("meander", "meander_fixed"):
        parts["inductor"] = design["res_ind"]
    for group in LINE_GROUPS:
        if (design.get(group) or "none") != "none":
            parts[group] = design[group]
    return parts


def _part_args(design):
    """--part role=variant for the choices that differ from DEFAULT_PARTS."""
    extra = {k: v for k, v in _parts_for(design).items() if DEFAULT_PARTS.get(k) != v}
    return ["--part", *[f"{k}={v}" for k, v in extra.items()]] if extra else []


def _path(geom):
    """SVG path data (um, y up) for a polygon or multipolygon."""
    out = []
    for q in getattr(geom, "geoms", [geom]):
        if q.geom_type != "Polygon" or q.is_empty:
            continue
        for ring in [q.exterior, *q.interiors]:
            pts = [(x * 1e3, y * 1e3) for x, y in ring.coords]
            out.append("M" + "L".join(f"{x:.3f},{y:.3f}" for x, y in pts) + "Z")
    return "".join(out)


def preview(state, knobs=None):
    """Shapes of the parts picked so far, by role, in um (local frame: x from
    the left SQUID finger, y from the SQUID bar top), plus placeholders for
    planned choices."""
    if GEOMETRY_ERROR:
        return dict(error=GEOMETRY_ERROR)
    d = state.get("design", {})
    parts = _parts_for(d)
    raw = dict(default_knobs(parts), **BASE, **(knobs or {}))
    p = g.Attr({k: _mm(v) for k, v in raw.items() if k in default_knobs(parts)})
    show = set()
    for group, roles in ROLE_OF_CHOICE.items():
        if _status(group, d.get(group)) == "simulated":
            show.update(roles)
    shapes, island = {}, []
    if "squid" in show:
        bottom, jj = g.squid_bottom(p)
        shapes["squid"] = unary_union([g.squid_top(p), bottom])
        island.append(bottom)
        shapes["junctions"] = unary_union([ln.buffer(p.tab_width / 2, cap_style=2)
                                           for ln in jj.values()])
    if "coupler" in show:
        t = g.t_coupler(p)
        shapes["coupler"] = t
        island.append(t)
    if "res_cap" in show:
        isl = unary_union(island) if island else g.t_coupler(p)
        shapes["resonator"] = unary_union(list(g.finger_cap(p)) + [g.meander(p)]
                                          + [g.column_lanes(p, isl)])
    cuts, terms = [], []
    if "feedline" in parts:
        fl = g.feedline(p)
        shapes["feedline"] = fl["feed"][0]
        cuts += fl["cut"]
        terms += [(b, "feedline") for b, _ in fl["terms"].values()]
    if "flux_line" in parts:
        fx = g.flux_line(p)
        cuts += fx["cut"]
        terms += [(b, "flux line") for b, _ in fx["terms"].values()]
        cx, yb = p.flux_x, p.flux_bottom
        yf, ye = yb + p.flux_fork_height, g.flux_end_y(p)
        yt = yf + p.flux_taper_length
        tw, w = p.flux_tip_width / 2, p.flux_width / 2
        from shapely.geometry import Polygon, box
        shapes["flux_line"] = unary_union([          # its centre conductor (ground)
            box(cx - tw, yb - 0.004, cx + tw, yf),
            Polygon([(cx - tw, yf), (cx + tw, yf), (cx + w, yt), (cx - w, yt)]),
            box(cx - w, yt, cx + w, ye)])
    pocket = g.pocket(p)
    bx = g.ground_bounds(p)
    sq = (p.junction_x - 0.012, -0.012 - p.t_length - 0.004, p.cell_pitch + 0.012, p.block_top)
    placeholders = []
    if d.get("qubit") and _status("qubit", d["qubit"]) != "simulated":
        placeholders.append(dict(label="single junction + pads (planned)",
                                 box=[-30, -40, 50, 20], anchor="top"))
    if d.get("resonator") and _status("resonator", d["resonator"]) != "simulated":
        x0, y0, x1, y1 = (v * 1e3 for v in g.pocket_bounds(p))
        kind = next(o["label"] for o in OPTIONS["resonator"] if o["id"] == d["resonator"])
        placeholders.append(dict(label=f"{kind} resonator (planned)",
                                 box=[x0 + 40, y0 + 40, x1 - 20, -80], anchor="center"))
    if d.get("coupler") and _status("coupler", d["coupler"]) != "simulated":
        placeholders.append(dict(label="coupling pads (planned)", box=[-8, -45, 8, -8],
                                 anchor="below"))
    meander = g.meander_layout(p) if "res_cap" in show else None
    ground = g.ground_frame(p)
    if cuts:
        ground = ground.difference(unary_union(cuts))
    return dict(
        ground=_path(ground), pocket=_path(pocket),
        terms=[dict(label=lab, x=(b.bounds[0] + b.bounds[2]) / 2 * 1e3,
                    y=(b.bounds[1] + b.bounds[3]) / 2 * 1e3) for b, lab in terms],
        shapes={k: _path(v) for k, v in shapes.items()},
        bounds=[v * 1e3 for v in bx], zoom=[v * 1e3 for v in sq],
        placeholders=placeholders,
        info=dict(meander=f"{meander.n_runs} runs \u00d7 {meander.run * 1e3:.1f} um"
                  if meander else None,
                  chip_um=[round((bx[2] - bx[0]) * 1e3, 1), round((bx[3] - bx[1]) * 1e3, 1)],
                  knobs={k: raw[k] for k in ("ind_length", "cap_length", "t_length",
                                             "feed_pad_length", "feed_length", "flux_length")
                         if k in raw}))


# ---------------------------------------------------------------------------
# optimize jobs (one at a time: one AEDT desktop)
# ---------------------------------------------------------------------------
JOB = dict(proc=None, log=None, cmd=None, started=None, state=None, kind=None)
LOCK = threading.Lock()


def _cmd(state, dry_run):
    f = state["fom"]
    lj_j, cj_j = 2 * float(f["lj_total_nH"]), float(f["cj_total_fF"]) / 2   # SQUID: 2 in parallel
    cmd = [sys.executable, "-B", os.path.join(ROOT, "optimize_multi.py"),
           "--fr", str(float(f["fr_GHz"])), "--fr-tol", str(float(f.get("fr_tol_GHz") or 0.1)),
           "--chi", str(float(f["chi_MHz"])), "--lj", f"{lj_j:g}", "--cj", f"{cj_j:g}", "--yes"]
    if f.get("chi_tol_MHz") not in (None, ""):
        cmd += ["--chi-tol", str(float(f["chi_tol_MHz"]))]
    cmd += _part_args(state.get("design", {}))
    k = kappa_target(state)
    if k and k.get("finger_um"):                  # kappa target: fix the finger length
        cmd += ["--base", f"feed_pad_length={k['finger_um']}um"]
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def _launch(cmd, kind, state, **extra):
    """Start one background job (caller holds LOCK); its output goes to a log."""
    os.makedirs(LOG_DIR, exist_ok=True)
    log = os.path.join(LOG_DIR, f"frontend_{kind}_{time.strftime('%Y%m%d-%H%M%S')}.log")
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1")
    fh = open(log, "w", encoding="utf-8")
    fh.write("$ " + " ".join(cmd) + "\n\n")
    fh.flush()
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT, env=env,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    JOB.update(proc=proc, log=log, cmd=cmd, started=time.time(), state=state, kind=kind,
               sweep_label=None)
    return dict(ok=True, log=os.path.relpath(log, ROOT), **extra)


def _busy():
    return JOB["proc"] is not None and JOB["proc"].poll() is None


def start_job(state, dry_run=False):
    with LOCK:
        if _busy():
            return dict(ok=False, error="a job is already running")
        missing = caps.why_not("plan" if dry_run else "simulate")
        if missing:
            return dict(ok=False, error=missing)
        issues = check_state(state)
        if issues:
            return dict(ok=False, error="; ".join(m for _, m in issues))
        return _launch(_cmd(state, dry_run), "dry_run" if dry_run else "optimize", state)


# ---------------------------------------------------------------------------
# sweeps: a star sweep of one knob around a base, through sweep.py
# ---------------------------------------------------------------------------
SWEEP_DIR = os.path.join(ROOT, "output", "frontend_sweeps")
SWEEP_KNOBS = {
    "feed_pad_length": dict(label="Feedline coupling capacitor (finger) length",
                            needs="feedline", note="sets C_kappa -> kappa; the finger must "
                            "still span the feedline: >= 134.5 um"),
    "t_length": dict(label="Qubit + readout resonator coupling (T) length",
                     note="sets C_g -> g, chi"),
    "ind_length": dict(label="Inductor (meander) length", note="sets L_r -> f_r"),
    "cap_length": dict(label="Resonator capacitor length", note="sets C_r -> f_r"),
}


def _sweep_issues(state, knob, values):
    issues = [m for f, m in check_state(state)
              if not (f == "fom" and (m.startswith("f_r") or m.startswith("chi")))]
    if knob not in SWEEP_KNOBS:
        issues.append(f"unknown sweep knob {knob!r}")
    elif SWEEP_KNOBS[knob].get("needs") and \
            (state.get("design", {}).get(SWEEP_KNOBS[knob]["needs"]) or "none") == "none":
        issues.append(f"{knob} needs a {SWEEP_KNOBS[knob]['needs']}: pick one in the Design tab")
    if not values or len(values) > 15 or any(v <= 0 for v in values):
        issues.append("give 1-15 positive values")
    return issues


def start_sweep(state, knob, values_um, base_knobs=None, base_name=None, dry_run=False):
    """Write output/frontend_sweeps/<label>.py and run sweep.py on it."""
    values = sorted({round(float(v), 4) for v in values_um or []})
    with LOCK:
        if _busy():
            return dict(ok=False, error="a job is already running")
        missing = caps.why_not("simulate")        # solving, or previewing in AEDT
        if missing:
            return dict(ok=False, error=missing)
        issues = _sweep_issues(state, knob, values)
        if issues:
            return dict(ok=False, error="; ".join(issues))
        f, d = state["fom"], state.get("design", {})
        parts = {k: v for k, v in _parts_for(d).items() if DEFAULT_PARTS.get(k) != v}
        base = dict(BASE, **{k: v for k, v in (base_knobs or {}).items()
                             if k not in ("pos_x", "pos_y", "layer")})
        label = f"gui_{time.strftime('%m%d_%H%M%S')}"
        os.makedirs(SWEEP_DIR, exist_ok=True)
        cfg = os.path.join(SWEEP_DIR, f"{label}.py")
        preview = os.path.join(SWEEP_DIR, f"{label}_preview.aedt")
        meta = dict(label=label, knob=knob, values_um=values, base_name=base_name or "nominal",
                    created=time.strftime("%Y-%m-%dT%H:%M:%S"), parts=parts,
                    preview_project=os.path.relpath(preview, ROOT),
                    lj_total_nH=float(f["lj_total_nH"]), cj_total_fF=float(f["cj_total_fF"]))
        lines = [
            f'"""Front-end sweep {label}: {knob} over {values} um around '
            f'{meta["base_name"]} (written by frontend/server.py)."""',
            "",
            f"LABEL = {label!r}",
            f"LJ_NH = {2 * float(f['lj_total_nH'])!r}      # per junction (SQUID: 2 in parallel)",
            f"CJ_FF = {float(f['cj_total_fF']) / 2!r}",
            f"BASE = {base!r}",
            f"PARTS = {parts!r}",
            f"SWEEPS = {{{knob!r}: {[f'{v:g}um' for v in values]!r}}}",
            "SETUP = dict()",
            "JJ = dict()",
            f"META = {meta!r}",
            "",
        ]
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        cmd = [sys.executable, "-B", os.path.join(ROOT, "sweep.py"), cfg]
        if dry_run:                      # preview: every point rendered in AEDT, not solved
            cmd += ["--prepare", "--project", preview]
        out = _launch(cmd, "sweep_preview" if dry_run else "sweep", state,
                      label=label, config=os.path.relpath(cfg, ROOT))
        JOB["sweep_label"] = label
        return out


def rerun_sweep(label, preview=False):
    """Solve (or preview) an existing front-end sweep as written: same config,
    same label, so the preview and the solved points stay one sweep."""
    with LOCK:
        if _busy():
            return dict(ok=False, error="a job is already running")
        missing = caps.why_not("simulate")
        if missing:
            return dict(ok=False, error=missing)
        try:
            cfg_path = os.path.join(SWEEP_DIR, f"{label}.py")
            _load_cfg(label)
        except FileNotFoundError:
            return dict(ok=False, error=f"no sweep {label!r}")
        cmd = [sys.executable, "-B", os.path.join(ROOT, "sweep.py"), cfg_path]
        if preview:
            cmd += ["--prepare", "--project", _preview_path(label)]
        out = _launch(cmd, "sweep_preview" if preview else "sweep", None, label=label)
        JOB["sweep_label"] = label
        return out


def _load_cfg(label):
    import sweep
    path = os.path.join(SWEEP_DIR, f"{label}.py")
    if not re.fullmatch(r"gui_[0-9_]+", label or "") or not os.path.exists(path):
        raise FileNotFoundError(label)
    return sweep.load_config(path)


def _preview_path(label):
    """The sweep's AEDT preview project (also for configs written before it existed)."""
    cfg = _load_cfg(label)
    rel = cfg.META.get("preview_project") or os.path.join("output", "frontend_sweeps",
                                                          f"{label}_preview.aedt")
    return os.path.join(ROOT, rel)


def sweeps():
    out = []
    for path in sorted(glob.glob(os.path.join(SWEEP_DIR, "gui_*.py")), reverse=True):
        try:
            out.append(_load_cfg(os.path.splitext(os.path.basename(path))[0]).META)
        except Exception:                             # noqa: BLE001
            continue
    return out


SWEEP_FOMS = ("f_r_GHz", "f01_GHz", "chi_MHz", "g_MHz", "alpha_MHz", "kappa_MHz", "Q_r",
              "T1_purcell_us", "p_q_total")


def sweep_results(label):
    """Every point of a front-end sweep, solved or not, in knob order."""
    missing = caps.why_not("plan")
    if missing:
        _load_cfg(label)                              # still 404 for an unknown sweep
        return dict(error=missing)
    import store as st
    import sweep
    from assembly import all_knobs
    from parts.catalog import full_choice
    cfg = _load_cfg(label)
    knob = cfg.META["knob"]
    base_val = to_number(dict(all_knobs(full_choice(cfg.PARTS)), **cfg.BASE)[knob])
    rows = []
    for k, value, _knobs, pid in sweep.config_plan(cfg):
        rec = st.find_existing_run(os.path.join(ROOT, "data"), pid)
        x = base_val if k is None else to_number(value)
        f = (rec or {}).get("fom") or {}
        rows.append(dict(x_um=x, base=k is None, point_id=pid, solved=rec is not None,
                         run_name=(rec or {}).get("run_name"),
                         design=((rec or {}).get("run_context") or {}).get("design_name"),
                         warnings=(rec or {}).get("warnings") or [],
                         fom={m: f.get(m) for m in SWEEP_FOMS}))
    rows.sort(key=lambda r: r["x_um"])
    running = _busy() and JOB.get("sweep_label") == label
    prev = _preview_path(label)
    return dict(meta=dict(cfg.META, preview_project=os.path.relpath(prev, ROOT)),
                knob_label=SWEEP_KNOBS.get(knob, {}).get("label", knob),
                base_um=base_val, rows=rows, running=running,
                preview_exists=os.path.exists(prev))


def job_status():
    p = JOB["proc"]
    if p is None:
        return dict(running=False, kind=None)
    try:
        with open(JOB["log"], encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        text = ""
    lines = [s for s in text.splitlines() if not s.startswith("  ignoring")]
    key = [s for s in lines if re.match(r"\s*(\[\d+\]|\[\d+/\d+\]|\[[0-9a-f]{16}\]|got |"
                                        r"model predicts|next:|status:|deliver:|\d+ distinct|"
                                        r"targets:|joint step|start:|.*rendering|.*solving|"
                                        r"FAILED|  GDS|  AEDT|  summary|WARNING|Traceback|.*Error)",
                                        s)]
    folder = next((s.split("deliver:", 1)[1].strip() for s in lines if s.startswith("deliver:")),
                  None)
    return dict(running=p.poll() is None, returncode=p.poll(), kind=JOB["kind"],
                elapsed_min=round((time.time() - JOB["started"]) / 60, 1),
                log=os.path.relpath(JOB["log"], ROOT), key=key[-40:], tail=lines[-15:],
                folder=os.path.basename(folder) if folder else None,
                sweep_label=JOB.get("sweep_label"))


def stop_job():
    p = JOB["proc"]
    if p is not None and p.poll() is None:
        p.terminate()
        return dict(ok=True)
    return dict(ok=False, error="no job running")


# ---------------------------------------------------------------------------
# results: the delivered request folders
# ---------------------------------------------------------------------------
def results():
    out = []
    for req in glob.glob(os.path.join(ROOT, "*", "request.json")):
        folder = os.path.dirname(req)
        try:
            with open(req, encoding="utf-8") as fh:
                r = json.load(fh)
        except (OSError, ValueError):
            continue
        name = os.path.basename(folder)
        aedt = os.path.join(folder, f"{name}.aedt")
        out.append(dict(
            name=name, created=r.get("created"), targets=r.get("targets"),
            tolerances=r.get("tolerances"), fom=r.get("final_fom"), knobs_um=r.get("knobs_um"),
            Lj_nH=r.get("Lj_nH"), Cj_fF=r.get("Cj_fF"), gds_ok=(r.get("gds_check") or {}).get("ok"),
            knobs=r.get("knobs"), parts=r.get("parts"),
            aedt_solved=bool(r.get("final_record")) and os.path.exists(aedt),
            summary=f"/files/{name}/summary.png" if os.path.exists(
                os.path.join(folder, "summary.png")) else None,
            layout=f"/files/{name}/{name}_layout.png",
            paths=dict(folder=folder, aedt=aedt, gds=os.path.join(folder, f"{name}.gds"))))
    return sorted(out, key=lambda r: r.get("created") or "", reverse=True)


def open_in_aedt(path):
    """Show a project in the AEDT the automation drives (frontend/aedt_open.py,
    its own process: pyEPR cannot be imported from a server thread). Not while
    a job runs: switching the active project mid-run could send the next render
    into the wrong project."""
    if _busy():
        return dict(ok=False, error="AEDT is busy with a job: switch to the project in the "
                                    "AEDT window yourself, or wait for the job to finish")
    missing = caps.why_not("simulate")
    if missing:
        return dict(ok=False, error=missing)
    if not os.path.exists(path):
        return dict(ok=False, error=f"{os.path.relpath(path, ROOT)} does not exist (yet)")
    out = subprocess.run([sys.executable, "-B", os.path.join(HERE, "aedt_open.py"), path],
                         cwd=ROOT, capture_output=True, text=True, timeout=300,
                         env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if "OPENED" not in out.stdout:
        return dict(ok=False, error=(out.stderr or out.stdout)[-600:])
    return dict(ok=True, project=out.stdout.split("OPENED", 1)[1].strip())


def open_path(name, what):
    folder = os.path.join(ROOT, name)
    if os.path.dirname(os.path.abspath(folder)) != ROOT or not os.path.isfile(
            os.path.join(folder, "request.json")):
        return dict(ok=False, error="unknown request")
    if what == "aedt":
        target = os.path.join(folder, f"{name}.aedt")
        if not os.path.exists(target):
            return dict(ok=False, error="no .aedt in this folder yet")
        if not caps.report()["have"]["aedt"]:
            return dict(ok=False, error="AEDT is not installed on this computer: the .aedt "
                                        f"is at {target}")
        _open_with_os(target)                         # the .aedt association: AEDT
    else:
        _open_with_os(folder)
    return dict(ok=True)


def _open_with_os(path):
    """Open a file or folder with its default application, on any OS."""
    if sys.platform == "win32":
        os.startfile(path)                            # noqa: S606
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):                        # quiet console
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path):
        if not os.path.isfile(path):
            return self._send(404, dict(error="not found"))
        with open(path, "rb") as fh:
            self._send(200, fh.read(), mimetypes.guess_type(path)[0] or "application/octet-stream")

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self._file(os.path.join(STATIC, "index.html"))
        if url.path.startswith("/static/"):
            p = os.path.normpath(os.path.join(STATIC, unquote(url.path[len("/static/"):])))
            return self._file(p) if p.startswith(STATIC) else self._send(403, {})
        if url.path.startswith("/files/"):            # images inside request folders only
            rel = unquote(url.path[len("/files/"):])
            p = os.path.normpath(os.path.join(ROOT, rel))
            ok = (p.startswith(ROOT + os.sep) and p.lower().endswith(".png")
                  and os.path.isfile(os.path.join(os.path.dirname(p), "request.json")))
            return self._file(p) if ok else self._send(403, {})
        if url.path == "/api/options":
            return self._send(200, OPTIONS)
        if url.path == "/api/capabilities":
            return self._send(200, caps.report())
        if url.path == "/api/job":
            return self._send(200, job_status())
        if url.path == "/api/results":
            return self._send(200, results())
        if url.path == "/api/sweep_knobs":
            return self._send(200, SWEEP_KNOBS)
        if url.path == "/api/sweeps":
            return self._send(200, sweeps())
        if url.path == "/api/sweep_results":
            label = (parse_qs(url.query).get("label") or [""])[0]
            try:
                return self._send(200, sweep_results(label))
            except FileNotFoundError:
                return self._send(404, dict(error=f"no sweep {label!r}"))
        return self._send(404, dict(error="not found"))

    def do_POST(self):
        url = urlparse(self.path)
        try:
            body = self._body()
            if url.path == "/api/preview":
                return self._send(200, preview(body.get("state", {}), body.get("knobs")))
            if url.path == "/api/check":
                return self._send(200, dict(issues=[m for _, m in check_state(body)],
                                            kappa=kappa_target(body)))
            if url.path == "/api/optimize":
                return self._send(200, start_job(body.get("state", {}), body.get("dry_run")))
            if url.path == "/api/sweep":
                return self._send(200, start_sweep(
                    body.get("state", {}), body.get("knob"), body.get("values_um"),
                    body.get("base_knobs"), body.get("base_name"), body.get("dry_run")))
            if url.path == "/api/stop":
                return self._send(200, stop_job())
            if url.path == "/api/sweep_rerun":
                return self._send(200, rerun_sweep(body.get("label", ""), body.get("preview")))
            if url.path == "/api/open_preview":
                return self._send(200, open_in_aedt(_preview_path(body.get("label", ""))))
            if url.path == "/api/open":
                return self._send(200, open_path(body.get("name", ""), body.get("what")))
        except Exception as err:                      # noqa: BLE001
            return self._send(500, dict(error=f"{type(err).__name__}: {err}"))
        return self._send(404, dict(error="not found"))


class Server(ThreadingHTTPServer):
    """One server per port. HTTPServer's SO_REUSEADDR lets a second copy bind the
    same port on Windows; requests then land on either at random, and only one of
    them knows about the running job."""
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


if __name__ == "__main__":
    url = f"http://localhost:{PORT}"
    try:
        srv = Server(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"the front end is already running on {url}; opening it")
        if "--no-browser" not in sys.argv:
            webbrowser.open(url)
        sys.exit(0)
    print(f"mergemon front end on {url}  (Ctrl+C to stop)")
    print(f"python: {sys.executable}")
    for f in caps.report()["features"].values():
        print(f"  {'on ' if f['ok'] else 'OFF'}  {f['label']}" +
              ("" if f["ok"] else f"  (missing: {', '.join(f['missing'])})"))
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
