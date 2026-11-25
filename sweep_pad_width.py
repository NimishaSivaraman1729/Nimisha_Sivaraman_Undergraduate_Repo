"""Pad-width sweep: lower E_C by widening the pads, and see what it costs.

    python sweep_pad_width.py                      # 455, 465, 475, 485 um
    python sweep_pad_width.py --values 455,465,475,485,495
    python sweep_pad_width.py --force              # re-solve points already done

Wider pads mean more shunt capacitance, so C_sigma goes up and

    E_C = e^2 / (2 C_sigma)          falls
    |alpha| ~ E_C                    falls with it
    f01 ~ sqrt(8 E_J E_C) - E_C      falls too

which is the trade: a flatter, more charge-noise-insensitive qubit (higher
E_J/E_C) bought with lower anharmonicity, so the 0->1 and 1->2 transitions
crowd together and fast pulses leak. Both plots below are two halves of that
one trade-off.

Each point is a full eigenmode solve plus pyEPR, into its OWN HFSS design
(transmon_pw455, transmon_pw465, ...) so every one stays in the project and
can be reopened. Results land in output/transmon_eigen_pw<width>um.json and a
point already solved is reused unless --force is given -- so a run interrupted
by a time limit can simply be re-run.

Outputs
-------
    output/transmon_eigen_pw<w>um.json   one per point
    pad_width_sweep.csv                  the table
    pad_width_f01.png                    f01 vs pad width
    pad_width_alpha.png                  anharmonicity vs pad width
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import pathlib
import time
import warnings

import numpy as np

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)

from qiskit_metal import Dict                               # noqa: E402
from transmon_pocket_design import build_design, DEFAULTS   # noqa: E402

HERE = pathlib.Path(__file__).parent
OUT = HERE / "output"

LJ_NH = 12.0
CJ_FF = 0.0
JJ_RECT = "JJ_rect_Lj_Q1_rect_jj"
JJ_LINE = "JJ_Lj_Q1_rect_jj_"
SETUP_NAME = "eigen_setup"
PROJECT_DIR = str(HERE / "hfss_projects")

SETUP = dict(
    min_freq_ghz=3,
    n_modes=1,
    max_passes=30,
    min_passes=2,
    max_delta_f=0.1,
    min_converged=2,
    pct_refinement=30,
    basis_order=1,
)

# Reference palette, categorical slots 1 and 2; ink tokens for all text.
C_F, C_A = "#2a78d6", "#eb6834"
INK, INK2, MUT = "#0b0b0b", "#52514e", "#8a8982"


# --------------------------------------------------------------------------
def solve_point(pad_width_um: float, pocket_width_um: float | None = None,
                pad_height_um: float | None = None) -> dict:
    """One full eigenmode + EPR run at this pad width.

    `pocket_width_um` overrides the fixed pocket. Passing pad + 2*margin keeps
    the pad-to-ground clearance constant as the pad grows, which is what the
    HFSS transmon campaign ended up doing: a FIXED pocket that a swept
    pad_width outgrows shorts the island to ground and the junction quietly
    stops participating.
    """
    t0 = time.time()
    pw = f"{pad_width_um:g}um"
    print(f"\n{'='*70}\npad_width = {pw}\n{'='*70}", flush=True)

    extra = {}
    if pocket_width_um is not None:
        extra["pocket_width"] = f"{pocket_width_um:g}um"
    if pad_height_um is not None:
        extra["pad_height"] = f"{pad_height_um:g}um"
    design, _ = build_design(pad_width=pw, **extra)

    # The pocket is FIXED at pocket_width while the pad grows into it. Once the
    # pad is as wide as its own ground cutout it shorts straight to the ground
    # plane, the junction stops participating, and the solve still looks
    # perfectly healthy. Watch the clearance shrink.
    pocket = (pocket_width_um if pocket_width_um is not None
              else float(str(DEFAULTS["pocket_width"]).rstrip("um")))
    clearance = (pocket - pad_width_um) / 2.0
    print(f"  pad-to-pocket clearance: {clearance:.1f} um "
          f"(pocket {pocket:g}um fixed)")
    if clearance <= 0:
        raise SystemExit(f"pad_width {pad_width_um:g}um >= pocket_width "
                         f"{pocket:g}um -- the island would short to ground.")
    ph = (pad_height_um if pad_height_um is not None
          else float(str(DEFAULTS["pad_height"]).rstrip("um")))
    gap = float(str(DEFAULTS["pad_gap"]).rstrip("um"))
    pocket_h = float(str(DEFAULTS["pocket_height"]).rstrip("um"))
    v_clear = (pocket_h - (2 * ph + gap)) / 2.0
    print(f"  pad stack {2*ph+gap:.0f} um tall in a {pocket_h:g} um pocket "
          f"-> {v_clear:.1f} um vertical clearance")
    if v_clear <= 0:
        raise SystemExit(f"pad stack {2*ph+gap:.0f}um exceeds pocket_height "
                         f"{pocket_h:g}um -- the pads would short to ground.")
    if clearance < 40:
        print(f"  WARNING: only {clearance:.1f} um of ground clearance; the pad "
              f"is close to the pocket wall and E_C will fall faster than "
              f"linearly from here")

    jj = design.qgeometry.tables["junction"]
    assert len(jj) > 0, "no junction geometry"
    # Lj must be written onto the junction qgeometry: setting only the Lj
    # variable is silently ignored and the table's default 10nH is used.
    for col, val in (("hfss_inductance", f"{LJ_NH}nH"),
                     ("hfss_capacitance", f"{CJ_FF}fF"),
                     ("q3d_inductance", f"{LJ_NH}nH"),
                     ("q3d_capacitance", f"{CJ_FF}fF"),
                     ("aedt_hfss_inductance", LJ_NH * 1e-9),
                     ("aedt_hfss_capacitance", CJ_FF * 1e-15)):
        if col in jj.columns:
            jj[col] = val

    from qiskit_metal.analyses.quantization import EPRanalysis
    eig = EPRanalysis(design, "hfss")
    s = eig.sim.setup
    s.name = SETUP_NAME
    for k, v in SETUP.items():
        s[k] = v
    s.vars = Dict(Lj=f"{LJ_NH} nH", Cj=f"{CJ_FF} fF")
    eig.setup.junctions = Dict(
        jj=Dict(Lj_variable="Lj", Cj_variable="Cj", rect=JJ_RECT, line=JJ_LINE))
    eig.setup.sweep_variable = "Lj"

    if not eig.sim.renderer_initialized:
        eig.sim._initialize_renderer()
    os.makedirs(PROJECT_DIR, exist_ok=True)
    try:
        eig.sim.renderer.pinfo.desktop.project_directory = PROJECT_DIR
    except Exception:                                   # noqa: BLE001
        pass

    # Its own HFSS design, so each width stays in the project.
    _tag = ("" if pocket_width_um is None else f"_pk{pocket_width_um:g}")
    # Lj only enters the tag when it is not the 12 nH default, so the files
    # already on disk keep their names.
    if LJ_NH != 12.0:
        _tag += f"_Lj{LJ_NH:g}"
    if pad_height_um is not None:
        _tag += f"_ph{pad_height_um:g}"
    eig.sim.run(name=f"transmon_pw{pad_width_um:g}{_tag}", components=[],
                open_terminations=[], port_list=[], box_plus_buffer=True)

    objs = list(eig.sim.renderer.pinfo.get_all_object_names())
    missing = [n for n in (JJ_RECT, JJ_LINE) if n not in objs]
    if missing:
        raise RuntimeError(f"junction objects {missing} missing from HFSS")

    ct = eig.sim.convergence_t
    n_pass = int(ct.index.max())
    dfreq = float(ct["Max Delta Freq. %"].dropna().iloc[-1])

    eig.run_epr()
    epra = eig.sim.renderer.epr_quantum_analysis
    r = epra.results[list(epra.results)[-1]]

    f_lin = float(np.asarray(r["f_0"]).flatten()[0]) / 1e3
    f_01 = float(np.asarray(r["f_ND"]).flatten()[0]) / 1e3
    alpha_nd = -abs(float(np.asarray(r["chi_ND"])[0, 0]))
    alpha_o1 = -abs(float(np.asarray(r["chi_O1"])[0, 0]))
    p = float(np.asarray(r["Pm_normed"]).flatten()[0])

    h, e = 6.62607015e-34, 1.602176634e-19
    phi0r = h / (2 * e) / (2 * np.pi)
    EJ = phi0r**2 / (LJ_NH * 1e-9) / h / 1e9
    EC = abs(alpha_nd) / 1e3

    warns = []
    if p < 0.5:
        warns.append(f"p = {p:.4f}: junction barely participating")
    if not 1.0 < f_lin < 20.0:
        warns.append(f"f = {f_lin:.3f} GHz outside 1-20 GHz")
    if n_pass >= SETUP["max_passes"]:
        warns.append("stopped at max_passes -- not the same as converged")

    row = dict(pad_width_um=pad_width_um, pocket_width_um=pocket,
               clearance_um=clearance, pad_height_um=ph,
               v_clearance_um=v_clear, Lj_nH=LJ_NH,
               n_passes=n_pass, max_delta_f_pct=dfreq,
               f_linear_GHz=f_lin, f01_GHz=f_01, p_junction=p,
               alpha_MHz=alpha_nd, alpha_O1_MHz=alpha_o1,
               EJ_GHz=EJ, EC_GHz=EC, EJ_over_EC=EJ / EC,
               warnings=warns, minutes=(time.time() - t0) / 60)
    OUT.mkdir(exist_ok=True)
    (OUT / f"transmon_eigen_pw{pad_width_um:g}um{_tag}.json").write_text(
        json.dumps(row, indent=2, default=str))
    print(f"  f01 = {f_01:.4f} GHz   alpha = {alpha_nd:.1f} MHz   "
          f"EC = {EC*1e3:.1f} MHz   EJ/EC = {EJ/EC:.1f}   p = {p:.4f}   "
          f"[{n_pass} passes, df {dfreq:.3f}%, {row['minutes']:.1f} min]")
    for w in warns:
        print("  WARNING:", w)
    return row


def load_point(pad_width_um: float):
    """Reuse a solved point. Also picks up the original single-point run, which
    was written before these files carried the pad width in their name."""
    f = OUT / f"transmon_eigen_pw{pad_width_um:g}um.json"
    if f.is_file():
        return json.loads(f.read_text())
    legacy = OUT / f"transmon_eigen_Lj{LJ_NH:g}nH.json"
    if legacy.is_file():
        d = json.loads(legacy.read_text())
        try:
            if float(str(d["design"]["pad_width"]).rstrip("um")) == pad_width_um:
                d["pad_width_um"] = pad_width_um
                return d
        except Exception:                               # noqa: BLE001
            pass
    return None


# --------------------------------------------------------------------------
def one_panel(ax, x, y, colour, ylabel, fmt):
    ax.plot(x, y, "-o", color=colour, lw=2.0, ms=9, mec="white", mew=1.6)
    for xv, yv in zip(x, y):
        ax.annotate(fmt.format(yv), (xv, yv), textcoords="offset points",
                    xytext=(0, 12), ha="center", fontsize=10, color=INK)
    ax.set_ylabel(ylabel, color=INK)
    ax.set_xlabel("pad width  (um)", color=INK)
    ax.grid(True, color=MUT, alpha=0.18, lw=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUT)
        ax.spines[s].set_linewidth(0.8)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.margins(y=0.22, x=0.08)


def make_plots(rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = sorted(rows, key=lambda r: r["pad_width_um"])
    x = [r["pad_width_um"] for r in rows]
    f01 = [r["f01_GHz"] for r in rows]
    alpha = [r["alpha_MHz"] for r in rows]

    # Two measures on different scales -> two figures, never a dual axis.
    # One series each, so no legend: the title names it.
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    one_panel(ax, x, f01, C_F, "qubit frequency  $f_{01}$  (GHz)", "{:.4f}")
    ax.set_title(f"Transmon $f_{{01}}$ vs pad width   "
                 f"($L_J$ = {LJ_NH:g} nH)",
                 fontsize=12.5, color=INK, loc="left")
    fig.savefig(HERE / "pad_width_f01.png", dpi=200, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    one_panel(ax, x, alpha, C_A, "anharmonicity  $\\alpha$  (MHz)", "{:.1f}")
    ax.set_title("Transmon anharmonicity vs pad width   "
                 "(pyEPR numerical diagonalisation)",
                 fontsize=12.5, color=INK, loc="left")
    fig.savefig(HERE / "pad_width_alpha.png", dpi=200, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    print(f"\nwrote {HERE / 'pad_width_f01.png'}")
    print(f"wrote {HERE / 'pad_width_alpha.png'}")


def main():
    ap = argparse.ArgumentParser(description="Pad-width sweep of the transmon.")
    ap.add_argument("--values", default="455,465,475,485",
                    help="pad widths in um")
    ap.add_argument("--force", action="store_true",
                    help="re-solve points that already have a JSON")
    ap.add_argument("--plots-only", action="store_true")
    ap.add_argument("--pocket", type=float, default=None,
                    help="override pocket_width (um) for every point solved")
    ap.add_argument("--pad-height", type=float, default=None,
                    help="override pad_height (um). Grows pad area without "
                         "changing the pad-to-pad facing edge, so it lowers "
                         "E_C mainly through pad-to-ground capacitance.")
    ap.add_argument("--lj", type=float, default=12.0,
                    help="junction inductance in nH. E_J ~ 1/Lj, so this moves "
                         "E_J/E_C without touching E_C or the anharmonicity.")
    args = ap.parse_args()

    global LJ_NH
    LJ_NH = args.lj
    widths = [float(v) for v in args.values.split(",") if v.strip()]
    rows, t0 = [], time.time()
    for w in widths:
        cached = (None if (args.force or args.pocket or args.lj != 12.0
                           or args.pad_height) else load_point(w))
        if cached is not None:
            print(f"pad_width = {w:g}um: reusing "
                  f"f01 = {cached['f01_GHz']:.4f} GHz, "
                  f"alpha = {cached['alpha_MHz']:.1f} MHz")
            rows.append(cached)
            continue
        if args.plots_only:
            print(f"pad_width = {w:g}um: no result yet, skipping")
            continue
        rows.append(solve_point(w, args.pocket, args.pad_height))

    if not rows:
        raise SystemExit("nothing to plot")

    rows = sorted(rows, key=lambda r: r["pad_width_um"])
    fields = ["pad_width_um", "f01_GHz", "f_linear_GHz", "alpha_MHz",
              "alpha_O1_MHz", "EC_GHz", "EJ_GHz", "EJ_over_EC", "p_junction",
              "n_passes", "max_delta_f_pct"]
    with open(HERE / "pad_width_sweep.csv", "w", newline="") as fh:
        wri = csv.DictWriter(fh, fieldnames=fields)
        wri.writeheader()
        for r in rows:
            wri.writerow({k: r.get(k, "") for k in fields})

    print("\n" + "=" * 78)
    print(f"RESULTS   ({(time.time()-t0)/60:.1f} min)")
    print("=" * 78)
    print(f"{'pad (um)':>9} {'f01 (GHz)':>11} {'alpha (MHz)':>12} "
          f"{'EC (MHz)':>10} {'EJ/EC':>8} {'p':>8} {'passes':>7}")
    for r in rows:
        print(f"{r['pad_width_um']:>9.0f} {r['f01_GHz']:>11.4f} "
              f"{r['alpha_MHz']:>12.1f} {r['EC_GHz']*1e3:>10.1f} "
              f"{r['EJ_over_EC']:>8.1f} {r['p_junction']:>8.4f} "
              f"{r['n_passes']:>7}")
    print(f"\nwrote {HERE / 'pad_width_sweep.csv'}")
    make_plots(rows)


if __name__ == "__main__":
    main()
