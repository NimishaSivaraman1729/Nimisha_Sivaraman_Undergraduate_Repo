"""One figure: the coupled transmon + readout resonator, as solved.

    python readout_summary.py
    python readout_summary.py --lj 10

Left panel is a frequency diagram rather than a third table, because the three
headline quantities are RELATIONAL -- the detuning is the distance between the
two modes, and the dispersive shift is a splitting of one of them. A table
states those; a diagram shows them to scale, including how enormously chi
(0.9 MHz) is dwarfed by Delta (1.6 GHz), which is the whole reason the
dispersive approximation holds.

Numbers are read from the solved JSON in output/; nothing is recomputed here,
so the figure cannot drift from what HFSS and pyEPR produced.
"""
from __future__ import annotations

import argparse
import json
import pathlib

HERE = pathlib.Path(__file__).parent
OUT = HERE / "output"

# Reference palette: slot 1 qubit, slot 2 resonator; ink tokens for all text.
C_Q, C_R = "#2a78d6", "#eb6834"
INK, INK2, MUT = "#0b0b0b", "#52514e", "#8a8982"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lj", type=float, default=10.0)
    ap.add_argument("--png", default=str(HERE / "readout_summary.png"))
    args = ap.parse_args()

    f = OUT / f"transmon_readout_Lj{args.lj:g}nH.json"
    if not f.is_file():
        raise SystemExit(f"no solved result at {f}")
    d = json.loads(f.read_text())
    q, r = d["qubit"], d["resonator"]
    f_q, f_r = q["f01_GHz"], r["f_GHz"]
    delta = d["delta_GHz"]
    chi, g = d["chi_MHz"], d["g_MHz"]
    alpha = q["alpha_MHz"]
    print(f"using {f.name}")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch

    fig = plt.figure(figsize=(13.2, 6.2))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.15, 1.0], wspace=0.10)
    ax = fig.add_subplot(gs[0, 0])
    axr = fig.add_subplot(gs[0, 1])
    axr.axis("off")

    # ---- frequency diagram -------------------------------------------------
    lo, hi = min(f_q, f_r) - 0.40, max(f_q, f_r) + 0.68
    for fr, col, lab, dy, va in ((f_q, C_Q, "qubit  $f_{01}$", +0.045, "bottom"),
                                 (f_r, C_R, "resonator  $f_r$", -0.045, "top")):
        ax.plot([0.12, 0.72], [fr, fr], "-", color=col, lw=3.2,
                solid_capstyle="round")
        ax.text(0.12, fr + dy, lab, fontsize=11, color=col, ha="left", va=va)
        ax.text(0.745, fr, f"{fr:.4f} GHz", fontsize=12.5, color=INK,
                ha="left", va="center", weight="bold")

    # Detuning, drawn to scale between the two modes.
    ax.add_patch(FancyArrowPatch((0.42, f_q), (0.42, f_r), arrowstyle="<->",
                                 color=INK2, lw=1.5, mutation_scale=13))
    ax.text(0.445, (f_q + f_r) / 2,
            f"$\\Delta = f_{{01}} - f_r$\n{delta:+.4f} GHz",
            fontsize=11, color=INK, ha="left", va="center",
            bbox=dict(fc="white", ec="none", alpha=0.9, pad=2))

    # chi is ~1800x smaller than Delta, so it cannot be drawn on this axis at
    # all. Say so, and show the splitting schematically instead.
    ax.text(0.12, f_r + 0.38,
            "dispersive splitting (not to scale — $\\chi$ is "
            f"{abs(delta)*1e3/abs(chi):.0f}$\\times$ smaller than $\\Delta$)",
            fontsize=8.5, color=MUT, ha="left", va="bottom")
    for sgn, st, dy in ((+1, r"$|0\rangle$", +0.17), (-1, r"$|1\rangle$", +0.04)):
        ax.plot([0.30, 0.56], [f_r + dy, f_r + dy], "-", color=C_R, lw=1.6,
                alpha=0.55)
        ax.text(0.575, f_r + dy, f"$f_r {sgn:+d}\\chi$   {st}".replace("+1", "+").replace("-1", "-"),
                fontsize=9, color=INK2, ha="left", va="center")
    ax.add_patch(FancyArrowPatch((0.38, f_r + 0.105), (0.38, f_r + 0.255),
                                 arrowstyle="<->", color=MUT, lw=1.1,
                                 mutation_scale=9))
    ax.text(0.225, f_r + 0.105, f"$2\\chi$ = {2*abs(chi):.3f} MHz",
            fontsize=9, color=INK2, ha="left", va="center")

    ax.set_ylim(lo, hi)
    ax.set_xlim(0, 1.25)
    ax.set_xticks([])
    ax.set_ylabel("frequency  (GHz)", color=INK)
    ax.grid(True, axis="y", color=MUT, alpha=0.16, lw=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right", "bottom"):
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_color(MUT)
    ax.spines["left"].set_linewidth(0.8)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.set_title("Transmon + readout resonator — HFSS eigenmode + pyEPR",
                 fontsize=13, color=INK, loc="left", pad=12)

    # ---- figures of merit --------------------------------------------------
    y = 0.98
    axr.text(0, y, "figures of merit", fontsize=11.5, color=INK2, va="top")
    y -= 0.085

    def big(label, value, colour=INK):
        nonlocal y
        axr.text(0.02, y, label, fontsize=13, color=INK2, va="center")
        axr.text(0.46, y, value, fontsize=18, color=colour, va="center",
                 weight="bold")
        y -= 0.108

    def row(label, value):
        nonlocal y
        axr.text(0.02, y, label, fontsize=10.5, color=INK2, va="center")
        axr.text(0.46, y, value, fontsize=11.5, color=INK, va="center")
        y -= 0.068

    big("qubit  $f_{01}$", f"{f_q:.4f} GHz", C_Q)
    big("resonator  $f_r$", f"{f_r:.4f} GHz", C_R)
    big("detuning  $\\Delta$", f"{delta:+.4f} GHz")
    big("disp. shift  $\\chi$", f"{chi:.4f} MHz")
    y -= 0.015
    row("coupling  $g$", f"{g:.2f} MHz")
    row("anharmonicity  $\\alpha$", f"{alpha:.1f} MHz")
    row("$E_J / E_C$", f"{d['EJ_over_EC']:.1f}")
    y -= 0.02
    row("$g/|\\Delta|$", f"{g/abs(delta*1e3):.4f}   (dispersive)")
    row("$n_{crit} = \\Delta^2/4g^2$", f"{(delta*1e3)**2/(4*g**2):.0f} photons")
    y -= 0.015
    axr.text(0, y,
             f"$L_J$ = {d['Lj_nH']:g} nH   ·   resonator "
             f"{d['design']['res_total_length']}   ·   pads "
             f"{d['design']['pad_width']} x {d['design']['pad_height']}",
             fontsize=9.5, color=INK2, va="top")
    y -= 0.055
    axr.text(0, y,
             f"converged in {d['n_passes']} passes, "
             f"$\\Delta f$ = {d['max_delta_f_pct']:.3f} %   ·   "
             f"$p_q$ = {q['p']:.4f},  $p_r$ = {r['p']:.1e}",
             fontsize=9, color=MUT, va="top")

    fig.savefig(args.png, dpi=200, bbox_inches="tight", facecolor="white")
    print(f"wrote {args.png}")


if __name__ == "__main__":
    main()
