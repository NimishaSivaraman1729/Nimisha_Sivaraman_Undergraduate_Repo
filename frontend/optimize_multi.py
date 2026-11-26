"""Multi-target inverse design: physics-scaled (power-law Newton) updates.

Hits f_r, chi and (optionally) Delta together, one HFSS solve per iteration,
in the spirit of Chalmers' open-source QDesignOptimizer: each target is paired
with ONE design variable and an approximate nonlinear model, and every
iteration updates all variables at once from the latest solve.

Model: locally f ~ x^n, so the Newton step in log space is

    x_{k+1} = x_k * (f_target / f_k)^(1/n)

with n from the stored trend fits (log-log, this design family; the
legacy-meander star5 points lend their exponent where the fixed meander has
no sweep yet, see optimize_common.PRIOR_PARTS) or, without data, from physics
(PHYSICS_N). The exponents stay fixed during the run; the solves correct the
prefactor every iteration.

Pairings (target -> variable, through which physics):
  f_r      -> ind_length (or cap_length)  f_r ~ x^n           LC: n = -1/2 ideal;
                                                              fits: -0.69 / -0.17
  Delta    -> Lj_nH  (fab knob)           f01* = f_r* + Delta*, f01 ~ Lj^n
                                                              transmon: n = -1/2
  chi      -> t_length (via g)            chi_qr = 2 alpha g^2 / (Delta (Delta + alpha))
                                          -> g* from chi*, Delta*, alpha_k;
                                          g scales as sqrt(f01 f_r) at fixed
                                          capacitances, then g ~ t_length^n
chi is pyEPR's full cross-Kerr chi_qr (the resonator's shift when the qubit
goes 0 -> 1) = 2x the chi of H = chi a^dag a sigma_z / 2 conventions.

Joint mode (the default): every knob moves at once. Each of f_r, f01, g is a
local power law in ALL the knobs,

    y_{k+1} = y_k * prod_i (x_i,{k+1} / x_i,k)^J[y, i]      J = d log y / d log x

(J fitted from the database like the exponents above), and chi follows from
f_r, f01, g through the chi_qr formula. Three geometry knobs for two targets
(f_r, chi) leave one direction free: the step is the smallest move in log
space, i.e. the smallest relative geometry change (Gauss-Newton with the
pseudoinverse; --cost k=w makes a knob w times dearer to move). A knob that
hits its bound is pinned there and the rest re-solve. --paired: the old
one-knob-per-target step.

    python frontend/optimize_multi.py                                  # CHECKPOINT: asks for the targets
    python frontend/optimize_multi.py --fr 6.5 --chi 1:2 --delta -2.0  # chi as a range lo:hi
    python frontend/optimize_multi.py --fr 6.5 --chi 1.5 --chi-tol 0.5 --dry-run
    options: --fr-tol (GHz, default 0.1) --delta-tol (GHz, default 0.2)
             --knobs ind_length cap_length t_length [Lj_nH]  (joint; default: the
                 three geometry knobs, + Lj_nH when Delta is targeted)
             --cost cap_length=0.5 ...  (joint) --paired --fr-knob ind_length|cap_length
             --max-iter (default 5) --lj --cj --base k=v --part role=variant
             --yes (no confirm prompt)
             --no-deliver (on convergence, skip deliver.py: the request folder with
                 the fab GDS, the design re-solved in its own .aedt, summary.png)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import optimize_common as o

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = o.LOG_DIR
# Physics exponents (f ~ x^n) used when the database has no trend for a pair.
# LC: the resonator knobs set f_r, Lj sets f01; g ~ C_g sqrt(w_q w_r / C_q C_r)
# -> through L_r g ~ w_r^(1/2), through C_r g ~ w_r^(3/2), through Lj g ~ w_q^(1/2);
# the cross terms (f_r vs t_length / Lj, f01 vs geometry) are small: 0.
PHYSICS_N = {("f_r_GHz", "ind_length"): -0.5, ("f_r_GHz", "cap_length"): -0.5,
             ("f_r_GHz", "t_length"): 0.0, ("f_r_GHz", "Lj_nH"): 0.0,
             ("f01_GHz", "ind_length"): 0.0, ("f01_GHz", "cap_length"): 0.0,
             ("f01_GHz", "t_length"): 0.0, ("f01_GHz", "Lj_nH"): -0.5,
             ("g_MHz", "ind_length"): -0.25, ("g_MHz", "cap_length"): -0.75,
             ("g_MHz", "t_length"): 1.0, ("g_MHz", "Lj_nH"): -0.25}
JOINT_FOMS = ("f_r_GHz", "f01_GHz", "g_MHz")         # the rows of J
BOUNDS = dict(o.KNOB_BOUNDS, Lj_nH=(16.0, 40.0))     # Lj: the lj_cj sweep's range
STEP = {"ind_length": 1.0, "Lj_nH": 0.1}             # rounding; others 0.1 um
GEOMETRY_KNOBS = ("ind_length", "cap_length", "t_length")


def _um(v):
    from store import to_number
    return float(to_number(v))


def exponent(db, fom, knob, base, parts, lj_nH, cj_fF, prior_parts=o.PRIOR_PARTS):
    """(n, source) for fom ~ knob^n: a log-log fit over this design's points
    that differ only in `knob`. When this design has fewer than 3 distinct
    values, related families join, each as its own offset group (shared
    exponent): the same design without its feedline / flux line (the lines
    barely move these exponents), then the legacy-meander prior."""
    from parts.catalog import OPTIONAL_ROLES
    groups, src = [], []
    own = o.family(db, fom, knob, lj_nH, cj_fF, base, parts, verbose=False)
    if len(own):
        groups.append(own)
        src.append(f"{len(own)} own")
    bare = {k: v for k, v in (parts or {}).items() if k not in OPTIONAL_ROLES}
    if own["x"].nunique() < 3 and bare != (parts or {}):
        lend = o.family(db, fom, knob, lj_nH, cj_fF, base, bare, verbose=False)
        if len(lend):
            groups.append(lend)
            src.append(f"{len(lend)} without-lines")
    if sum(g["x"].nunique() for g in groups) < 3 and prior_parts and knob != "Lj_nH":
        prior = dict(bare, **prior_parts)
        lend = o.family(db, fom, knob, lj_nH, cj_fF, base, prior, verbose=False)
        if lend["x"].nunique() >= 2:
            groups.append(lend)
            src.append(f"{len(lend)} legacy-meander")
    groups = [g for g in groups if len(g)]
    if sum(g["x"].nunique() for g in groups) >= 2 and any(g["x"].nunique() >= 2 for g in groups):
        n, _ = o.fit_shared_slope([(np.log(g["x"].astype(float)),
                                    np.log(np.abs(g[fom].astype(float)))) for g in groups])
        return float(n), "fit: " + " + ".join(src) + " point(s)"
    return PHYSICS_N[(fom, knob)], "physics"


def _round(knob, x):
    step = STEP.get(knob, 0.1)
    return round(round(x / step) * step, 6)


def _clip(knob, x, notes):
    lo, hi = BOUNDS[knob]
    if x < lo or x > hi:
        notes.append(f"{knob} {x:.1f} clipped to [{lo}, {hi}]")
    return _round(knob, float(np.clip(x, lo, hi)))


def step(state, foms, targets, n, fr_knob):
    """One power-law Newton step for every paired variable. state: {knob: value
    (um / nH)}; foms: the latest solve's FoMs. Returns (new state, predictions, notes)."""
    new, notes, pred = dict(state), [], {}
    f_r, f01 = foms["f_r_GHz"], foms["f01_GHz"]
    fr_t = targets["f_r_GHz"]
    new[fr_knob] = _clip(fr_knob, state[fr_knob] * (fr_t / f_r) ** (1 / n["f_r"]), notes)
    if "delta_GHz" in targets:
        f01_t = fr_t + targets["delta_GHz"]
        new["Lj_nH"] = _clip("Lj_nH", state["Lj_nH"] * (f01_t / f01) ** (1 / n["f01"]), notes)
    else:
        f01_t = f01                                    # Lj fixed: the qubit stays put
    pred.update(f_r_GHz=fr_t, f01_GHz=f01_t, delta_GHz=f01_t - fr_t)
    if "chi_MHz" in targets:
        alpha = foms["alpha_MHz"]                      # ~ -E_C: geometric, barely moves
        delta = (f01_t - fr_t) * 1e3
        g_t = np.sqrt(abs(targets["chi_MHz"] * delta * (delta + alpha) / (2 * alpha)))
        # g ~ C_g sqrt(w_q w_r / C_q C_r): tuned through L_r, g ~ w_r^(1/2); through
        # C_r (cap_length, C_r ~ w_r^-2), g ~ w_r^(3/2) (checked: 112.1 -> 104.9
        # predicted vs 103.8 solved at cap_length 375.9 um)
        p_r = 1.5 if fr_knob == "cap_length" else 0.5
        g_here = foms["g_MHz"] * (f01_t / f01) ** 0.5 * (fr_t / f_r) ** p_r
        new["t_length"] = _clip("t_length",
                                state["t_length"] * (g_t / g_here) ** (1 / n["g"]), notes)
        pred.update(g_MHz=float(g_t), chi_MHz=targets["chi_MHz"])
    return new, pred, notes


def jacobian(db, knobs, base, parts, lj_nH, cj_fF):
    """J[(fom, knob)] = d log fom / d log knob (fits, physics fallback) and sources."""
    J, src = {}, {}
    for fom in JOINT_FOMS:
        for k in knobs:
            J[fom, k], src[fom, k] = exponent(db, fom, k, base, parts, lj_nH, cj_fF)
    return J, src


def _chi(f_r, f01, g, alpha):
    d = (f01 - f_r) * 1e3
    return 2 * alpha * g ** 2 / (d * (d + alpha))


def predict(foms, J, u):
    """FoMs after the log-space move u = {knob: log(x_new / x)}; chi through the
    chi_qr formula, scaled to equal the solved chi at u = 0."""
    y = {f: foms[f] * np.exp(sum(J[f, k] * uk for k, uk in u.items())) for f in JOINT_FOMS}
    y["delta_GHz"] = y["f01_GHz"] - y["f_r_GHz"]
    a = foms["alpha_MHz"]
    scale = foms["chi_MHz"] / _chi(foms["f_r_GHz"], foms["f01_GHz"], foms["g_MHz"], a)
    y["chi_MHz"] = scale * _chi(y["f_r_GHz"], y["f01_GHz"], y["g_MHz"], a)
    return y


def _residual(foms, J, targets, tols, u):
    """Misses in units of each target's half tolerance (log for f_r, chi)."""
    y, r = predict(foms, J, u), []
    for t, v in targets.items():
        half = (tols[t][1] - tols[t][0]) / 2
        if t == "delta_GHz":
            r.append((y[t] - v) / half)
        else:
            r.append(np.log(abs(y[t] / v)) / (half / abs(v)))
    return np.array(r), y


def joint_step(state, foms, targets, tols, J, knobs, cost=None):
    """Move every knob at once: the smallest (cost-weighted, log-space) move whose
    predicted FoMs hit the targets; least squares if the targets outnumber the
    free knobs. Knobs pushed past a bound are pinned there and the rest re-solve.
    Returns (new state, predictions, notes)."""
    cost, pinned, notes = cost or {}, {}, []
    while True:
        free = [k for k in knobs if k not in pinned]
        w = np.array([cost.get(k, 1.0) for k in free])

        def r_of(v, free=free, w=w):
            return _residual(foms, J, targets, tols, dict(pinned, **dict(zip(free, v / w))))[0]

        v = np.zeros(len(free))
        for _ in range(30):            # Gauss-Newton, minimum-norm solution of each linearisation
            r = r_of(v)
            if not free or np.max(np.abs(r)) < 1e-4:
                break
            A = np.column_stack([(r_of(v + 1e-6 * e) - r) / 1e-6 for e in np.eye(len(free))])
            v = np.linalg.pinv(A) @ (A @ v - r)
        u = dict(pinned, **dict(zip(free, v / w)))
        out = [k for k in free if not BOUNDS[k][0] <= state[k] * np.exp(u[k]) <= BOUNDS[k][1]]
        if not out:
            break
        for k in out:
            pinned[k] = np.log(_clip(k, state[k] * np.exp(u[k]), notes) / state[k])
    new = dict(state)
    for k in knobs:
        new[k] = _round(k, state[k] * np.exp(u[k]))
    pred = predict(foms, J, {k: np.log(new[k] / state[k]) for k in knobs})
    r = _residual(foms, J, targets, tols, {k: np.log(new[k] / state[k]) for k in knobs})[0]
    if np.max(np.abs(r)) > 1:
        notes.append("the model cannot reach every target from here (bounds / too few knobs): "
                     "closest point taken")
    return new, {k: float(v) for k, v in pred.items()}, notes


def misses(foms, targets, tols):
    """{target: (value, ok)}."""
    out = {}
    for k, t in targets.items():
        v = foms.get(k)
        lo, hi = tols[k]
        out[k] = (v, v is not None and lo <= v <= hi)
    return out


def _ask(prompt, default=None, cast=float):
    s = input(f"{prompt}" + (f" [{default}]" if default is not None else "") + ": ").strip()
    if not s:
        return default
    return s if cast is None else cast(s)


def checkpoint():
    """Ask the user for the spec (the tutorial's CHECKPOINT)."""
    print("CHECKPOINT -- design targets (blank = default / skip)")
    fr = _ask("  resonator frequency f_r (GHz)")
    fr_tol = _ask("  f_r tolerance (GHz)", 0.1)
    chi = _ask("  dispersive shift chi_qr (MHz; full cross-Kerr), value or lo:hi", cast=None)
    delta = _ask("  detuning Delta = f01 - f_r (GHz; blank = keep Lj fixed)")
    delta_tol = _ask("  Delta tolerance (GHz)", 0.2) if delta is not None else None
    return fr, fr_tol, chi, None, delta, delta_tol


def parse_chi(chi, chi_tol):
    """'1:2' -> (1.5, (1, 2)); '1.5' with tol 0.5 -> (1.5, (1, 2))."""
    if chi is None:
        return None, None
    s = str(chi)
    if ":" in s:
        lo, hi = sorted(float(v) for v in s.split(":"))
        return (lo + hi) / 2, (lo, hi)
    v = float(s)
    t = 0.1 * abs(v) if chi_tol is None else chi_tol
    return v, (v - t, v + t)


def _knob_strings(state):
    return {k: f"{state[k]:g}um" for k in GEOMETRY_KNOBS if k in state}


def optimize_multi(fr, fr_tol=0.1, chi=None, chi_tol=None, delta=None, delta_tol=0.2,
                   fr_knob=None, max_iter=5, lj_nH=36.0, cj_fF=45.0, base=None,
                   parts=None, mode="joint", knobs=None, cost=None,
                   dry_run=False, confirm=True, deliver_result=True, log_dir=LOG_DIR,
                   data_dir=o.DATA_DIR, run=None, geometry_check=o._check_geometry):
    import store as st
    from assembly import all_knobs
    from parts.catalog import DEFAULT_PARTS, expected_conductors, full_choice  # noqa: F401
    from simulate import JJ_MESH, JJ_MODEL, point_identity

    chi_v, chi_band = parse_chi(chi, chi_tol)
    targets, tols = {"f_r_GHz": fr}, {"f_r_GHz": (fr - fr_tol, fr + fr_tol)}
    if chi_v is not None:
        targets["chi_MHz"], tols["chi_MHz"] = chi_v, chi_band
    if delta is not None:
        targets["delta_GHz"], tols["delta_GHz"] = delta, (delta - delta_tol, delta + delta_tol)

    base = dict(o.DEFAULT_BASE, **(base or {}))
    nominal = dict(all_knobs(full_choice(parts)), **base)
    state = {k: _um(nominal[k]) for k in GEOMETRY_KNOBS}
    state["Lj_nH"] = float(lj_nH)
    geo_base = {k: v for k, v in base.items() if k not in GEOMETRY_KNOBS}
    log = dict(targets=targets, tolerances=tols, start=dict(state), base=base, parts=parts,
               cj_fF=cj_fF, started=time.strftime("%Y-%m-%dT%H:%M:%S"),
               iterations=[], hfss_runs=0, status="running")

    def finish(status):
        log["status"] = status
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
            path = os.path.join(log_dir, f"optimize_multi_{time.strftime('%Y%m%d-%H%M%S')}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(log, fh, indent=2, default=float)
            log["log_path"] = path
        print(f"status: {status}  ({log['hfss_runs']} new HFSS solve(s))")
        return log

    # exponents from the database (this design family at the start point)
    db = o.load_points((data_dir,))
    n, src = {}, {}
    pairs = {"f_r": ("f_r_GHz", None), "g": ("g_MHz", "t_length"), "f01": ("f01_GHz", "Lj_nH")}
    for key, (fom, knob) in pairs.items():
        if key == "f_r":
            continue
        n[key], src[key] = exponent(db, fom, knob, base, parts, lj_nH, cj_fF)

    def record_for(st_):
        knobs = dict(geo_base, **_knob_strings(st_))
        pid = point_identity(knobs, parts, st_["Lj_nH"], cj_fF, None, None, JJ_MODEL, JJ_MESH)[0]
        return knobs, pid, st.find_existing_run(data_dir, pid)

    _, pid, rec = record_for(state)
    if rec is None:
        print("the start point is not in the database: solving it first")
    foms = (rec or {}).get("fom")

    # f_r knob: cap_length (clean, interpolated) if the target is inside its band
    nc, sc = exponent(db, "f_r_GHz", "cap_length", base, parts, lj_nH, cj_fF)
    ni, si = exponent(db, "f_r_GHz", "ind_length", base, parts, lj_nH, cj_fF)
    if fr_knob is None and foms:
        band = sorted(foms["f_r_GHz"] * (b / state["cap_length"]) ** nc
                      for b in BOUNDS["cap_length"])
        fr_knob = "cap_length" if band[0] <= fr <= band[1] else "ind_length"
    fr_knob = fr_knob or "ind_length"
    n["f_r"], src["f_r"] = (nc, sc) if fr_knob == "cap_length" else (ni, si)

    print("targets: " + ", ".join(f"{k} {v:g} (ok in {tols[k][0]:g} .. {tols[k][1]:g})"
                                  for k, v in targets.items()))
    if mode == "joint":
        knobs = list(knobs or GEOMETRY_KNOBS + (("Lj_nH",) if "delta_GHz" in targets else ()))
        J, jsrc = jacobian(db, knobs, base, parts, lj_nH, cj_fF)
        log.update(mode=mode, knobs=knobs, cost=cost,
                   jacobian={f"{f}/{k}": v for (f, k), v in J.items()},
                   jacobian_sources={f"{f}/{k}": v for (f, k), v in jsrc.items()})
        print("joint step over " + ", ".join(knobs)
              + (f" (cost {cost})" if cost else "") + "; J = d log y / d log x:")
        print("    " + " " * 8 + "".join(f"{k:>12s}" for k in knobs))
        for f in JOINT_FOMS:
            print(f"    {f:8s}" + "".join(f"{J[f, k]:+12.3f}" for k in knobs))
        print("    (" + "; ".join(sorted({s for s in jsrc.values()})) + ")")
        if "delta_GHz" not in targets and "Lj_nH" not in knobs:
            print("    Lj fixed (no Delta target): f01 stays where the geometry puts it")
    else:
        log.update(mode=mode, fr_knob=fr_knob, exponents=n, exponent_sources=src)
        print(f"pairings: f_r_GHz <- {fr_knob} (n = {n['f_r']:+.3f}, {src['f_r']})")
        if "chi_MHz" in targets:
            print(f"          chi_MHz <- t_length via g (n_g = {n['g']:+.3f}, {src['g']})")
        if "delta_GHz" in targets:
            print(f"          delta_GHz <- Lj_nH via f01 (n = {n['f01']:+.3f}, {src['f01']})")
        else:
            print("          Lj fixed (no Delta target): f01 stays where the geometry puts it")
    print("start: " + ", ".join(f"{k} {v:g}" for k, v in state.items()))

    for it in range(0, max_iter + 1):
        if foms is None:                               # solve the current state
            geo_knobs, pid, rec = record_for(state)
            n_want = expected_conductors(parts)
            nconductors = geometry_check(geo_knobs, parts) if geometry_check else n_want
            if nconductors != n_want:
                print(f"    geometry is {nconductors} conductors, not {n_want}; stopping")
                return finish("bad_geometry")
            if dry_run:
                print("    dry run: geometry OK, not solving")
                return finish("dry_run")
            if confirm and not log["hfss_runs"]:         # once, before the first solve
                if input("    proceed with HFSS (~40 min per iteration)? [y/N] ").strip().lower() != "y":
                    return finish("cancelled")
            from simulate import run_point
            rec = (run or run_point)(geo_knobs, parts=parts, lj_nH=state["Lj_nH"], cj_fF=cj_fF,
                                     data_dir=data_dir, label="optm",
                                     sweep=dict(knob="iter", value=str(it), source="optimize_multi",
                                                targets=targets))
            log["hfss_runs"] += 1
            foms = rec.get("fom") or {}
        entry = dict(iter=it, state=dict(state), run_name=(rec or {}).get("run_name"),
                     foms={k: foms.get(k) for k in ("f_r_GHz", "f01_GHz", "delta_GHz", "chi_MHz",
                                                    "g_MHz", "alpha_MHz", "p_q_total")})
        log["iterations"].append(entry)
        m = misses(foms, targets, tols)
        print(f"[{it}] " + ", ".join(f"{k} {v:g}" for k, v in state.items()))
        print("    got " + ", ".join(f"{k} {v:.4g}{'' if ok else ' (off)'}"
                                     for k, (v, ok) in m.items())
              + f", f01 {foms['f01_GHz']:.4f}, g {foms['g_MHz']:.1f} MHz, p_q {foms['p_q_total']:.3f}")
        if foms.get("p_q_total", 1) < o.P_MIN:
            print(f"    p_q < {o.P_MIN}: qubit and resonator hybridised, chi/g invalid; stopping")
            return finish("hybridised")
        if all(ok for _, ok in m.values()):
            log["result"] = entry
            out = finish("converged")
            if deliver_result:                         # request folder: GDS, AEDT, summary
                import deliver
                out["delivery"] = deliver.deliver(rec, targets, tols, confirm=False)
            return out
        if it == max_iter:
            break
        if mode == "joint":
            new, pred, notes = joint_step(state, foms, targets, tols, J, knobs, cost)
        else:
            new, pred, notes = step(state, foms, targets, n, fr_knob)
        for note in notes:
            print(f"    {note}")
        if mode == "joint":
            print("    model predicts " + ", ".join(f"{k} {pred[k]:.4g}" for k in
                                                 ("f_r_GHz", "chi_MHz", "delta_GHz", "g_MHz")))
        if abs(pred["delta_GHz"]) < o.HYBRID_GHZ:
            print(f"    WARNING: predicted |Delta| < {o.HYBRID_GHZ} GHz: the modes will hybridise")
        changed = {k: (state[k], new[k]) for k in new if abs(new[k] - state[k]) > 1e-9}
        entry.update(next=new, predicted=pred, notes=notes)
        if not changed:
            print("    no variable moves (at bounds / resolution): stopping")
            return finish("stalled")
        print("    next: " + ", ".join(f"{k} {a:g} -> {b:g}" for k, (a, b) in changed.items()))
        state, foms = new, None
    return finish("max_iter")


def _kv(items):
    return dict(s.split("=", 1) for s in items or [])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fr", type=float, help="target f_r, GHz")
    ap.add_argument("--fr-tol", type=float, default=0.1, help="GHz")
    ap.add_argument("--chi", help="target chi_qr, MHz: value or lo:hi")
    ap.add_argument("--chi-tol", type=float, help="MHz (default 10%% of chi)")
    ap.add_argument("--delta", type=float, help="target Delta = f01 - f_r, GHz")
    ap.add_argument("--delta-tol", type=float, default=0.2, help="GHz")
    ap.add_argument("--knobs", nargs="+", choices=list(GEOMETRY_KNOBS) + ["Lj_nH"],
                    help="joint mode: the knobs that move")
    ap.add_argument("--cost", nargs="*", help="joint mode: k=w, w x dearer to move (default 1)")
    ap.add_argument("--paired", action="store_true", help="one knob per target (old step)")
    ap.add_argument("--fr-knob", choices=["ind_length", "cap_length"], help="paired mode")
    ap.add_argument("--max-iter", type=int, default=5)
    ap.add_argument("--lj", type=float, default=36.0, help="start Lj per junction, nH")
    ap.add_argument("--cj", type=float, default=45.0, help="Cj per junction, fF")
    ap.add_argument("--base", nargs="*", help="base-design knob overrides, k=v")
    ap.add_argument("--part", nargs="*", help="part choice, role=variant (e.g. feedline=met181)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true", help="no confirmation prompt")
    ap.add_argument("--no-deliver", action="store_true",
                    help="on convergence, skip the request folder (GDS + AEDT re-solve + summary)")
    a = ap.parse_args()
    fr, fr_tol, chi, chi_tol, delta, delta_tol = (a.fr, a.fr_tol, a.chi, a.chi_tol,
                                                  a.delta, a.delta_tol)
    if fr is None:
        if not sys.stdin.isatty():
            sys.exit("no targets given and no terminal to ask: pass --fr [--chi] [--delta]")
        fr, fr_tol, chi, chi_tol, delta, delta_tol = checkpoint()
        if fr is None:
            sys.exit("f_r is required")
    optimize_multi(fr, fr_tol, chi, chi_tol, delta, delta_tol if delta_tol else 0.2,
                   fr_knob=a.fr_knob, max_iter=a.max_iter, lj_nH=a.lj, cj_fF=a.cj,
                   base=_kv(a.base), parts=_kv(a.part) or None,
                   mode="paired" if a.paired else "joint", knobs=a.knobs,
                   cost={k: float(v) for k, v in _kv(a.cost).items()} or None,
                   dry_run=a.dry_run, confirm=not a.yes, deliver_result=not a.no_deliver)
