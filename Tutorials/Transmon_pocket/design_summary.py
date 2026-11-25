"""One figure: the transmon layout, the knobs we turned, and what came out.

    python design_summary.py
    python design_summary.py --width 620 --height 120 --pocket 815 --lj 10

Draws the layout straight from the Quantum Metal design rather than
screenshotting AEDT, so the geometry in the picture is the geometry that was
solved, with real dimensions on it. Results are read from the solved point's
JSON in output/ -- nothing is re-derived here, so the numbers cannot drift
from what HFSS and pyEPR actually produced.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import warnings
import logging

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
OUT = HERE / "output"

# Reference palette: slot 1 for metal, ink tokens for everything textual.
C_METAL = "#2a78d6"
C_JJ = "#eb6834"
INK, INK2, MUT = "#0b0b0b", "#52514e", "#8a8982"
C_ETCH = "#ffffff"
C_GND = "#dcdcd6"


def result_json(width, pocket, lj, height):
    tag = f"_pk{pocket:g}"
    if lj != 12.0:
        tag += f"_Lj{lj:g}"
    names = [f"transmon_eigen_pw{width:g}um{tag}_ph{height:g}.json"]
    if height == 100.0:
        names.append(f"transmon_eigen_pw{width:g}um{tag}.json")
    for n in names:
        f = OUT / n
        if f.is_file():
            return json.loads(f.read_text()), n
    raise SystemExit(f"no solved result found; looked for {names}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=float, default=620.0)
    ap.add_argument("--height", type=float, default=120.0)
    ap.add_argument("--pocket", type=float, default=815.0)
    ap.add_argument("--lj", type=float, default=10.0)
    ap.add_argument("--png", default=str(HERE / "transmon_design_summary.png"))
    args = ap.parse_args()

    res, src = result_json(args.width, args.pocket, args.lj, args.height)
    print(f"using {src}")

    from transmon_pocket_design import build_design, DEFAULTS
    design, _ = build_design(pad_width=f"{args.width:g}um",
                             pad_height=f"{args.height:g}um",
                             pocket_width=f"{args.pocket:g}um")
    poly = design.qgeometry.tables["poly"]
    jj = design.qgeometry.tables["junction"]
    geo = {r["name"]: r["geometry"] for _, r in poly.iterrows()}
    jj_line = jj.iloc[0]["geometry"]
    jj_w = float(jj.iloc[0]["width"])

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MplPoly, Rectangle, FancyArrowPatch

    fig = plt.figure(figsize=(13.4, 5.9))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.35, 1.0], wspace=0.08)
    ax = fig.add_subplot(gs[0, 0])
    axr = fig.add_subplot(gs[0, 1])
    axr.axis("off")

    # ---- layout ----------------------------------------------------------
    pk = geo["rect_pk"]
    pk_x0, pk_y0, pk_x1, pk_y1 = pk.bounds
    pad = 0.08
    ax.add_patch(Rectangle((pk_x0 - pad, pk_y0 - pad),
                           (pk_x1 - pk_x0) + 2 * pad, (pk_y1 - pk_y0) + 2 * pad,
                           facecolor=C_GND, edgecolor="none", zorder=0))
    ax.add_patch(MplPoly(list(pk.exterior.coords), closed=True,
                         facecolor=C_ETCH, edgecolor=MUT, lw=1.0, zorder=1))
    for nm in ("pad_top", "pad_bot"):
        ax.add_patch(MplPoly(list(geo[nm].exterior.coords), closed=True,
                             facecolor=C_METAL, edgecolor="none", zorder=2))
    (jx0, jy0), (jx1, jy1) = list(jj_line.coords)
    ax.add_patch(Rectangle((jx0 - jj_w / 2, min(jy0, jy1)), jj_w,
                           abs(jy1 - jy0), facecolor=C_JJ, edgecolor="none",
                           zorder=3))

    def dim(x0, y0, x1, y1, label, off=(0, 0), ha="center", va="center"):
        ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1),
                                     arrowstyle="<->", color=INK2, lw=1.3,
                                     mutation_scale=11, zorder=4))
        ax.text((x0 + x1) / 2 + off[0], (y0 + y1) / 2 + off[1], label,
                fontsize=9.5, color=INK, ha=ha, va=va, zorder=5,
                bbox=dict(fc="white", ec="none", alpha=0.85, pad=1.4))

    w_mm, h_mm = args.width / 1e3, args.height / 1e3
    gap_mm = float(str(DEFAULTS["pad_gap"]).rstrip("um")) / 1e3
    ph_mm = float(str(DEFAULTS["pocket_height"]).rstrip("um")) / 1e3
    top_y = gap_mm / 2 + h_mm
    dim(-w_mm / 2, top_y + 0.045, w_mm / 2, top_y + 0.045,
        f"pad width  {args.width:g} um", off=(0, 0.032))
    dim(w_mm / 2 + 0.045, gap_mm / 2, w_mm / 2 + 0.045, top_y,
        f"pad height {args.height:g} um", off=(0.028, 0), ha="left")
    ax.annotate(f"pad gap {gap_mm*1e3:g} um", xy=(-w_mm / 2 + 0.02, 0),
                xytext=(pk_x0 - 0.26, 0.0), fontsize=9.5, color=INK,
                ha="left", va="center", zorder=5,
                arrowprops=dict(arrowstyle="->", color=INK2, lw=1.2))
    dim(pk_x0, pk_y0 - 0.055, pk_x1, pk_y0 - 0.055,
        f"pocket  {args.pocket:g} um", off=(0, -0.035))
    # Pushed well clear of the pad-height dimension, which sits just inside it.
    dim(pk_x1 + 0.20, pk_y0, pk_x1 + 0.20, pk_y1,
        f"pocket height {ph_mm*1e3:g} um", off=(0.028, 0), ha="left")

    ax.text(0, -top_y - 0.012, "junction (lumped RLC, $L_J$)", fontsize=9,
            color=C_JJ, ha="center", va="top", zorder=5)
    ax.set_aspect("equal")
    ax.set_xlim(pk_x0 - 0.38, pk_x1 + 0.62)
    ax.set_ylim(pk_y0 - 0.13, pk_y1 + 0.13)
    ax.axis("off")
    ax.set_title("Isolated transmon — layout as solved", fontsize=13,
                 color=INK, loc="left", pad=10)

    # ---- numbers ---------------------------------------------------------
    y = 0.97
    axr.text(0, y, "geometry", fontsize=11.5, color=INK2, va="top")
    y -= 0.068
    for k, v in (("pad width", f"{args.width:g} um"),
                 ("pad height", f"{args.height:g} um"),
                 ("pad gap", f"{gap_mm*1e3:g} um"),
                 ("pocket width", f"{args.pocket:g} um"),
                 ("pocket height", f"{ph_mm*1e3:g} um"),
                 ("ground clearance", f"{res.get('clearance_um', 0):.1f} um")):
        axr.text(0.02, y, k, fontsize=10.5, color=INK2, va="top")
        axr.text(0.52, y, v, fontsize=10.5, color=INK, va="top")
        y -= 0.055

    y -= 0.03
    axr.text(0, y, "junction", fontsize=11.5, color=INK2, va="top")
    y -= 0.068
    for k, v in (("$L_J$", f"{res['Lj_nH']:g} nH"),
                 ("$E_J$", f"{res['EJ_GHz']:.2f} GHz"),
                 ("participation $p$", f"{res['p_junction']:.4f}")):
        axr.text(0.02, y, k, fontsize=10.5, color=INK2, va="top")
        axr.text(0.52, y, v, fontsize=10.5, color=INK, va="top")
        y -= 0.055

    y -= 0.05
    axr.axhline(y + 0.03, xmin=0.0, xmax=0.95, color=MUT, lw=0.8)
    y -= 0.03
    axr.text(0, y, "result   (HFSS eigenmode + pyEPR)", fontsize=11.5,
             color=INK2, va="top")
    y -= 0.095
    axr.text(0.02, y, "$f_{01}$", fontsize=13, color=INK2, va="center")
    axr.text(0.52, y, f"{res['f01_GHz']:.4f} GHz", fontsize=19,
             color=INK, va="center", weight="bold")
    y -= 0.105
    axr.text(0.02, y, "$\\alpha$", fontsize=13, color=INK2, va="center")
    axr.text(0.52, y, f"{res['alpha_MHz']:.1f} MHz", fontsize=19,
             color=INK, va="center", weight="bold")
    y -= 0.10
    for k, v in (("$E_C$", f"{res['EC_GHz']*1e3:.1f} MHz"),
                 ("$E_J/E_C$", f"{res['EJ_over_EC']:.1f}")):
        axr.text(0.02, y, k, fontsize=10.5, color=INK2, va="top")
        axr.text(0.52, y, v, fontsize=10.5, color=INK, va="top")
        y -= 0.055
    axr.text(0, y - 0.03,
             f"converged in {res['n_passes']} passes, "
             f"$\\Delta f$ = {res['max_delta_f_pct']:.3f} %",
             fontsize=9, color=MUT, va="top")

    fig.savefig(args.png, dpi=200, bbox_inches="tight", facecolor="white")
    print(f"wrote {args.png}")


if __name__ == "__main__":
    main()
