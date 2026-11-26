"""Deliver a requested design: one folder per request, ready for fab review.

    frontend/fr_4_72GHz_chi_2MHz/
        fr_4_72GHz_chi_2MHz.gds          fab GDS, MicrowaveDesign layers (fab_gds.py)
        fr_4_72GHz_chi_2MHz_layout.png   the GDS, drawn
        fr_4_72GHz_chi_2MHz.aedt (+ .aedtresults)
                                         this design alone, re-solved here: fields,
                                         mesh and results open in AEDT
        mergemon_<stamp>_final_data.json the re-solve's record (same schema as data/;
                                         kept out of data/ so trends see no repeat)
        summary.png                      f01, f_r, alpha, chi, g, E_J/E_C at a glance
        request.json                     targets, tolerances, knobs, Lj/Cj, checks

The folder is named after the request, not the result. If it already holds a
different design, the new one goes to <name>_v2, _v3, ... The re-solve is
resumable: a design already solved in its folder is not solved again.

    python frontend/deliver.py                          # latest converged optimize_multi run
    python frontend/deliver.py --log frontend/output/optimize/optimize_multi_<stamp>.json
    python frontend/deliver.py --record frontend/data/<record>.json --fr 4.72 --chi 2.0
    options: --no-solve (GDS + summary only; ~40 min saved) --yes
optimize_multi.py calls deliver() itself when a run converges (--no-deliver).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import time

import optimize_common as o

HERE = os.path.dirname(os.path.abspath(__file__))
LABELS = {"f_r_GHz": "fr", "chi_MHz": "chi", "delta_GHz": "delta"}
UNITS = {"f_r_GHz": "GHz", "chi_MHz": "MHz", "delta_GHz": "GHz"}

# chart tokens (dataviz reference palette, light surface)
SURFACE, TILE, RULE = "#fcfcfb", "#f3f2ef", "#d9d8d4"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#8a8984"
QUBIT, RESONATOR = "#2a78d6", "#eb6834"         # categorical slots 1, 2
GOOD, CRITICAL = "#0ca30c", "#d03b3b"           # status: always with icon + label


def _num(v):
    s = f"{abs(v):.3f}".rstrip("0").rstrip(".").replace(".", "_")
    return ("m" if v < 0 else "") + s


def request_name(targets):
    """{'f_r_GHz': 4.72, 'chi_MHz': 2.0} -> 'fr_4_72GHz_chi_2MHz'."""
    return "_".join(f"{LABELS[k]}_{_num(v)}{UNITS[k]}" for k, v in targets.items()
                    if k in LABELS)


def request_folder(name, point_id, root=HERE):
    """root/name, or name_v2, ... if that folder already holds another design."""
    for i in range(1, 100):
        folder = os.path.join(root, name if i == 1 else f"{name}_v{i}")
        req = os.path.join(folder, "request.json")
        if not os.path.exists(req):
            return folder
        with open(req, encoding="utf-8") as fh:
            if json.load(fh).get("point_id") == point_id:
                return folder
    raise RuntimeError(f"too many versions of {name}")


def _knob_value(rec, k):
    from store import to_number
    return float(to_number(rec["knobs"][k])) if k in rec["knobs"] else None


def build_gds(record_path, gds_path):
    """fab_gds.py where gdspy is (site_config.geometry_python); returns its checks."""
    from site_config import geometry_python
    cmd = geometry_python() + ["-B", os.path.join(HERE, "fab_gds.py"), record_path, gds_path]
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
    line = [s for s in out.stdout.splitlines() if s.startswith("VERIFY ")]
    if out.returncode or not line:
        raise RuntimeError(f"fab_gds.py failed:\n{out.stdout[-2000:]}\n{out.stderr[-2000:]}")
    check = json.loads(line[-1][len("VERIFY "):])
    ok = (check["n_conductors"] == check.get("expected_conductors", 2)
          and check["max_deviation_um"] < 0.01
          and check["junction_marks_bridge"] and check["inside_chip"])
    return dict(check, ok=bool(ok))


# --------------------------------------------------------------- summary ----
def _tile(ax, label, value, unit, sub=None, status=None):
    from matplotlib.patches import FancyBboxPatch
    ax.set_axis_off()
    ax.add_patch(FancyBboxPatch((0.02, 0.04), 0.96, 0.92, boxstyle="round,pad=0,rounding_size=0.06",
                                fc=TILE, ec="none", transform=ax.transAxes))
    ax.text(0.09, 0.80, label, fontsize=10.5, color=INK2, transform=ax.transAxes)
    v = ax.text(0.09, 0.44, value.replace("-", "−"), fontsize=23, color=INK, weight="bold",
                transform=ax.transAxes)
    end = v.get_window_extent(ax.figure.canvas.get_renderer()).transformed(ax.transAxes.inverted())
    ax.text(end.x1 + 0.025, 0.44, unit, fontsize=12, color=INK2, transform=ax.transAxes)
    if sub:
        ax.text(0.09, 0.16, sub, fontsize=8.5, color=INK2, transform=ax.transAxes)
    if status is not None:
        ok, text = status
        ax.text(0.91, 0.80, ("✓ " if ok else "✗ ") + text, fontsize=9,
                color=GOOD if ok else CRITICAL, ha="right", weight="bold",
                transform=ax.transAxes)


def _frequency_map(ax, f):
    """The qubit's 0-1 and 1-2 lines and the resonator on one GHz axis."""
    f01, fr, a = f["f01_GHz"], f["f_r_GHz"], f["alpha_MHz"] / 1e3
    f12 = f01 + a
    lo, hi = min(f12, fr, f01) - 0.5, max(f12, fr, f01) + 0.25
    ax.set_xlim(lo, hi)
    ax.set_ylim(0, 1)
    for x, c, ls, lab, ha in ((f12, QUBIT, (0, (3, 2)), f"qubit 1→2\n{f12:.3f}", "right"),
                              (f01, QUBIT, "-", f"qubit 0→1\n{f01:.4f}", "left"),
                              (fr, RESONATOR, "-", f"resonator\n{fr:.4f}", "center")):
        ax.plot([x, x], [0.10, 0.64], color=c, lw=2.5, ls=ls, solid_capstyle="round")
        ax.text(x, 0.68, lab, ha=ha, va="bottom", fontsize=9, color=INK)
    ax.annotate("", xy=(fr, 0.30), xytext=(f01, 0.30),
                arrowprops=dict(arrowstyle="<->", color=INK2, lw=1))
    d = f["delta_GHz"]
    ax.text((f01 + fr) / 2, 0.33, f"Δ = {d * 1e3:+.0f} MHz".replace("-", "−"),
            ha="center", va="bottom", fontsize=9, color=INK2)
    ax.annotate("", xy=(f01, 0.18), xytext=(f12, 0.18),
                arrowprops=dict(arrowstyle="<->", color=INK2, lw=1))
    ax.text(f12 - 0.03, 0.18, f"α = {f['alpha_MHz']:.0f} MHz".replace("-", "−"),
            ha="right", va="center", fontsize=8.5, color=INK2)
    for s in ("left", "right", "top"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(RULE)
    ax.set_yticks([])
    ax.tick_params(axis="x", labelsize=8.5, colors=INK2)
    ax.set_xlabel("frequency (GHz)", fontsize=9, color=INK2)
    g, dm = f["g_MHz"], abs(d) * 1e3
    kappa = (f"    κ/2π = {f['kappa_MHz']:.3f} MHz (Q_r {f['Q_r']:.0f})    "
             f"T1,Purcell = {f['T1_purcell_us']:.1f} µs" if "kappa_MHz" in f else "")
    ax.set_title(f"frequency map    g/Δ = {g / dm:.3f}    critical photons (Δ/2g)² "
                 f"= {(dm / (2 * g)) ** 2:.0f}" + kappa, fontsize=10, color=INK, loc="left")


def summary_png(rec, req, path, layout_png=None, check_rec=None):
    """f01, f_r, alpha, chi, g, E_J/E_C (+ targets, the frequency map, the layout)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    f = rec["fom"]
    targets, tols = req["targets"], req["tolerances"]

    def status(k):
        if k not in targets:
            return None
        lo, hi = tols[k]
        return lo <= f[k] <= hi, "in band" if lo <= f[k] <= hi else "off target"

    def target_sub(k, fmt):
        lo, hi = tols[k]
        return f"target {targets[k]:{fmt}}  (band {lo:{fmt}} – {hi:{fmt}})"

    fig = plt.figure(figsize=(14, 9.2), facecolor=SURFACE)
    gs = fig.add_gridspec(4, 6, height_ratios=[0.42, 1, 1, 2.6], hspace=0.35, wspace=0.12,
                          left=0.035, right=0.985, top=0.97, bottom=0.05)
    head = fig.add_subplot(gs[0, :])
    head.set_axis_off()
    head.text(0, 0.75, f"{req['name']}  —  merged-element transmon + lumped readout "
              "resonator", fontsize=16, color=INK, weight="bold", transform=head.transAxes)
    head.text(0, 0.05, req["provenance"], fontsize=9.5, color=INK2, transform=head.transAxes)

    ej, ec = f["EJ_total_GHz"], f["EC_GHz"]
    tiles = [
        ("Qubit frequency  f01", f"{f['f01_GHz']:.4f}", "GHz", f"p_q = {f['p_q_total']:.3f}", None),
        ("Resonator frequency  f_r", f"{f['f_r_GHz']:.4f}", "GHz",
         target_sub("f_r_GHz", ".2f") if "f_r_GHz" in targets else None, status("f_r_GHz")),
        ("Anharmonicity  α", f"{f['alpha_MHz']:.1f}", "MHz", "f12 − f01", None),
        ("Dispersive shift  χ_qr", f"{f['chi_MHz']:.3f}", "MHz",
         target_sub("chi_MHz", ".2f") if "chi_MHz" in targets
         else "full shift (= 2χ of χ a†a σz)", status("chi_MHz")),
        ("Coupling  g", f"{f['g_MHz']:.1f}", "MHz", "from χ_qr = 2αg²/(Δ(Δ+α))",
         None),
        ("E_J / E_C", f"{f['EJ_over_EC']:.1f}", "",
         f"E_J {ej:.2f} GHz (2 × {req['Lj_nH']:g} nH)  ·  E_C {ec * 1e3:.0f} MHz", None),
    ]
    for i, (lab, val, unit, sub, st) in enumerate(tiles):
        _tile(fig.add_subplot(gs[1 + i // 3, 2 * (i % 3):2 * (i % 3) + 2]), lab, val, unit, sub, st)

    _frequency_map(fig.add_subplot(gs[3, :3]), f)
    ax = fig.add_subplot(gs[3, 3:])
    ax.set_axis_off()
    if layout_png and os.path.exists(layout_png):
        ax.imshow(plt.imread(layout_png))
    k = req["knobs_um"]
    ax.set_title("ind_length {ind_length:g} um  ·  cap_length {cap_length:g} um  ·  "
                 "t_length {t_length:g} um  ·  Lj {Lj:g} nH, Cj {Cj:g} fF per junction"
                 .format(**k, Lj=req["Lj_nH"], Cj=req["Cj_fF"]), fontsize=9, color=INK2)
    if check_rec is not None:
        c = check_rec["fom"]
        diff = ", ".join(f"{lab} {(f[key] - c[key]) * s:+.3f} {u}" for key, lab, s, u in (
            ("f01_GHz", "f01", 1e3, "MHz"), ("f_r_GHz", "f_r", 1e3, "MHz"),
            ("chi_MHz", "χ_qr", 1.0, "MHz"), ("g_MHz", "g", 1.0, "MHz")))
        fig.text(0.985, 0.012, f"re-solve − optimizer solve ({check_rec.get('run_name')}): "
                 + diff.replace("-", "−"), fontsize=8.5, color=MUTED, ha="right")
    fig.savefig(path, dpi=100, facecolor=SURFACE)
    plt.close(fig)
    return path


# --------------------------------------------------------------- deliver ----
def deliver(record, targets, tolerances, solve=True, confirm=False, root=HERE,
            record_path=None, run=None):
    """Folder + fab GDS + summary for a solved design; re-solve it in its own
    AEDT project unless solve=False. Returns the request dict."""
    name = request_name(targets)
    folder = request_folder(name, record["point_id"], root)
    stem = os.path.basename(folder)
    os.makedirs(folder, exist_ok=True)
    comp = record["component_params"]
    req = dict(name=stem, targets=targets, tolerances={k: list(v) for k, v in tolerances.items()},
               point_id=record["point_id"], source_record=record_path or record.get("run_name"),
               knobs=record["knobs"], parts=record.get("parts"),
               knobs_um={k: _knob_value(record, k) for k in ("ind_length", "cap_length", "t_length")},
               Lj_nH=comp["Lj_nH"], Cj_fF=comp["Cj_fF"],
               created=time.strftime("%Y-%m-%dT%H:%M:%S"))
    print(f"deliver: {folder}")

    # the record the GDS is built from, in the folder (fab_gds.py reads it)
    src = os.path.join(folder, "_design_record.json")
    with open(src, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, default=str)
    gds = os.path.join(folder, f"{stem}.gds")
    req["gds_check"] = build_gds(src, gds)
    os.remove(src)
    layout = os.path.splitext(gds)[0] + "_layout.png"
    c = req["gds_check"]
    print(f"  GDS -> {gds}\n     conductors {c['n_conductors']}, max deviation from the solved "
          f"geometry {c['max_deviation_um']:.4f} um, junction marks bridge: "
          f"{c['junction_marks_bridge']}  ({'OK' if c['ok'] else 'PROBLEM'})")

    def write(rec, check_rec=None, provenance=""):
        req["provenance"] = provenance
        summary_png(rec, req, os.path.join(folder, "summary.png"), layout, check_rec)
        with open(os.path.join(folder, "request.json"), "w", encoding="utf-8") as fh:
            json.dump(req, fh, indent=2, default=float)

    import store as st
    day = time.strftime("%Y-%m-%d")
    final = st.find_existing_run(folder, record["point_id"])    # re-solved here before
    if final is None:
        write(record, provenance=f"HFSS eigenmode + pyEPR (Cj-corrected), optimizer solve "
                                 f"{record.get('run_name')}; re-solve in this folder pending  ·  {day}")
        print(f"  summary -> {os.path.join(folder, 'summary.png')}")
        if not solve:
            return req
        if confirm and input("  re-solve this design in its own AEDT project (~40 min)? [y/N] "
                             ).strip().lower() != "y":
            return req
        from simulate import run_point
        knobs = {k: v for k, v in record["knobs"].items()}
        final = (run or run_point)(knobs, parts=record.get("parts"), lj_nH=comp["Lj_nH"],
                                   cj_fF=comp["Cj_fF"], data_dir=folder, label="final",
                                   sweep=dict(knob="request", value=stem, source="deliver",
                                              targets=targets),
                                   project=os.path.join(folder, f"{stem}.aedt"))
    q = final.get("quality", {})
    req.update(final_record=final.get("run_name"), aedt=f"{stem}.aedt",
               final_fom={k: final["fom"].get(k) for k in (
                   "f01_GHz", "f_r_GHz", "alpha_MHz", "chi_MHz", "g_MHz", "delta_GHz",
                   "EJ_over_EC", "p_q_total", "kappa_MHz", "Q_r", "Q_q", "T1_purcell_us")})
    write(final, check_rec=record,
          provenance=f"HFSS eigenmode + pyEPR (Cj-corrected), solved in {stem}.aedt  ·  "
                     f"{q.get('n_passes', '?')} passes, Δf {q.get('max_delta_f_pct', float('nan')):.2f} %"
                     f"  ·  {day}")
    print(f"  AEDT -> {os.path.join(folder, stem + '.aedt')}; summary updated with the re-solve")
    return req


def _latest_converged():
    logs = sorted(glob.glob(os.path.join(o.LOG_DIR, "optimize_multi_*.json")), reverse=True)
    for p in logs:
        with open(p, encoding="utf-8") as fh:
            if json.load(fh).get("status") == "converged":
                return p
    raise SystemExit("no converged optimize_multi run in output/optimize")


def _record_for_run(run_name):
    path = os.path.join(o.DATA_DIR, f"{run_name}_data.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh), path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", help="an optimize_multi log (default: latest converged)")
    ap.add_argument("--record", help="a data/ record instead of a log (give --fr etc.)")
    ap.add_argument("--fr", type=float)
    ap.add_argument("--fr-tol", type=float, default=0.1)
    ap.add_argument("--chi", type=float)
    ap.add_argument("--chi-tol", type=float)
    ap.add_argument("--no-solve", action="store_true")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    if a.record:
        with open(a.record, encoding="utf-8") as fh:
            rec, rec_path = json.load(fh), a.record
        targets, tols = {"f_r_GHz": a.fr}, {"f_r_GHz": (a.fr - a.fr_tol, a.fr + a.fr_tol)}
        if a.chi is not None:
            t = a.chi_tol if a.chi_tol is not None else 0.1 * a.chi
            targets["chi_MHz"], tols["chi_MHz"] = a.chi, (a.chi - t, a.chi + t)
    else:
        log_path = a.log or _latest_converged()
        with open(log_path, encoding="utf-8") as fh:
            log = json.load(fh)
        if log.get("status") != "converged":
            raise SystemExit(f"{log_path}: status {log.get('status')}, not converged")
        rec, rec_path = _record_for_run(log["result"]["run_name"])
        targets, tols = log["targets"], log["tolerances"]
    deliver(rec, targets, tols, solve=not a.no_solve, confirm=not a.yes, record_path=rec_path)
