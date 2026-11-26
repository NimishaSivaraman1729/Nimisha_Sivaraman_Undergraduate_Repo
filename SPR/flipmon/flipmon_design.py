"""
flipmon_design.py
=================

Geometry and simulation settings for the flip-chip transmon ("flipmon") surface
participation study, transcribed from these Ansys Electronics Desktop projects
in ``old_flipmon_design/``:

    flipmon_spr - Copy.aedt          3D HFSS design ``flipmon`` (+ Q3D and 2D helpers)
                                      -> the design that matches the "calculation flipmon"
                                         and "flipmon" tabs of ``spr graphs.xlsx``
    FLIPMON_large_top_vacuum.aedt    2D Maxwell edge model, vacuum gap swept 1-10 um
    FLIPMON_equal_top_vacuum.aedt    same 2D edge model, smaller simulation box

Layout (all circular, centred on the z axis)
--------------------------------------------
Bottom chip ("carrier"), metal in the plane z = 0:
    inner pad  : annulus,  r from cap_width/2       to 1.5*cap_width
    outer pad  : annulus,  r from 1.5*cap_width + cap_gap  to 2.5*cap_width + cap_gap
    junction   : 0.2 um wide leads along -x bridging the cap_gap, 50 ohm lumped
                 port between x = -65 um and -64 um
Top chip ("chip"), flipped, metal facing down in the plane z = vacuum_gap:
    chip pad   : annulus,  r from cap_width/2 to chip_radius
Indium bump (cylinder, r = cap_width/2, height = vacuum_gap) joins the inner pad
to the chip pad, so inner pad + bump + chip pad form one electrode (pad 1). The
outer pad is pad 2.

Both chips are 450 x 450 x 200 um silicon. The HFSS Region has zero padding,
so the only vacuum is the gap between the chips.

Conventions match ../SPR_tutorial/transmon_design.py: lengths in um, metal as
zero-thickness perfect-E sheets, 3 nm / eps_r = 10 lossy interface layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Materials and interface layers
# ---------------------------------------------------------------------------

EPS_SILICON = 11.9            # Ansys "silicon"
EPS_INTERFACE = 10.0          # Ansys "permittivity10"
INTERFACE_THICKNESS_NM = 3.0
INTERFACE_OFFSET_UM = 0.001   # sampling sheets sit 1 nm off the metal / substrate plane
PERIMETER_BAND_UM = 1.0       # width of the 3D "perimeter" sheets (see note in PerimeterNote)


# ---------------------------------------------------------------------------
# 3D design
# ---------------------------------------------------------------------------

@dataclass
class FlipmonDesign:
    """HFSS design ``flipmon`` in flipmon_spr - Copy.aedt. Defaults = the values saved in the project."""
    cap_width: float = 30.0       # um, radial width of each bottom pad
    cap_gap: float = 40.0         # um, radial gap between inner and outer pad
    vacuum_gap: float = 5.0       # um, chip-to-chip spacing (= indium bump height)
    chip_radius: float = 135.0    # um, outer radius of the top-chip pad
    chip_size_um: tuple = (450.0, 450.0, 200.0)      # top chip ("chip")
    carrier_size_um: tuple = (450.0, 450.0, 200.0)   # bottom chip ("carrier")
    frequency_GHz: float = 5.0
    ansys_project: str = "old_flipmon_design/flipmon_spr - Copy.aedt"
    ansys_design: str = "flipmon"
    solution_type: str = "HFSS Terminal Network, driven 5 GHz, 50 ohm lumped port across the junction"

    # Fields Calculator objects, grouped the way spr_calculation sums them
    calculator_surfaces: dict = field(default_factory=lambda: {
        # bottom chip, metal-air (vacuum side, z = +1 nm)
        "bottom_MA_interior": ["interior_pad_MA_interior", "exterior_pad_MA_interior"],
        "bottom_MA_perimeter": ["interior_pad_MA_perimeter", "exterior_pad_inside_MA_perimeter",
                                "exterior_pad_outside_MA_perimeter"],
        # bottom chip, metal-substrate (substrate side, z = -1 nm)
        "bottom_MS_interior": ["interior_pad_MS_interior", "exterior_pad_MS_interior"],
        "bottom_MS_perimeter": ["interior_pad_MS_perimeter", "exterior_pad_inside_MS_perimeter",
                                "exterior_pad_outside_MS_perimeter"],
        # bottom chip, substrate-air (sheets at z = -1 nm)
        "bottom_SA_interior": ["outer_SA_interior", "interior_SA_interior"],
        "bottom_SA_perimeter": ["interior_pad_SA_perimeter", "exterior_pad_interior_SA_perimeter",
                                "exterior_pad_SA_perimeter"],
        # top chip (metal faces down: MA is below it at z = gap - 1 nm, MS above at gap + 1 nm)
        "top_MA_interior": ["chip_MA_interior"],
        "top_MA_perimeter": ["chip_MA_perimeter"],
        "top_MS_interior": ["chip_MS_interior"],
        "top_MS_perimeter": ["chip_MS_perimeter"],
        "top_SA_interior": ["chip_SA_interior"],
        "top_SA_perimeter": ["chip_SA_exterior"],
        # volumes
        "bottom_substrate": ["carrier"],
        "top_substrate": ["chip"],
        "vacuum": ["Region"],
    })

    # --- derived radii (um) -------------------------------------------------
    @property
    def bump_radius(self):
        return self.cap_width / 2

    @property
    def inner_pad(self):
        """(r_in, r_out) of the inner pad."""
        return self.cap_width / 2, 1.5 * self.cap_width

    @property
    def outer_pad(self):
        """(r_in, r_out) of the outer pad."""
        return 1.5 * self.cap_width + self.cap_gap, 2.5 * self.cap_width + self.cap_gap

    @property
    def chip_pad(self):
        """(r_in, r_out) of the top-chip pad (centre hole filled by the bump)."""
        return self.cap_width / 2, self.chip_radius

    def metal_edges(self):
        """Every free metal edge (radius in um, which chip). The edges at the bump are not free."""
        return [
            (self.inner_pad[1], "bottom", "inner pad, outer edge"),
            (self.outer_pad[0], "bottom", "outer pad, inner edge"),
            (self.outer_pad[1], "bottom", "outer pad, outer edge"),
            (self.chip_pad[1], "top", "chip pad, outer edge"),
        ]


FLIPMON = FlipmonDesign()


# Parameter sweeps run on this design (results in data/epr_vs_*.csv and
# data/capacitance_vs_*.csv). The other three parameters stay at their defaults.
SWEEPS = {
    "vacuum_gap": [0.5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
    "chip_radius": [100, 110, 120, 130, 135, 140, 150, 160, 170],
    "cap_gap": [30, 35, 40, 45, 50, 55, 60, 65, 70, 75],
    "cap_width": [10, 20, 30, 40, 50, 60],
}


# ---------------------------------------------------------------------------
# 2D edge model (FLIPMON_*_top_vacuum.aedt) -> F_MS, F_MA, F_SA
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FlipmonEdge2D:
    """Electrostatic XY cross-section through a bottom-pad edge with the top chip overhead.

    Bottom: 30 um half-strip, 50 nm thick, on Si, with 3 nm eps = 10 MS / MA / SA layers.
    Top: a 60 um wide, 50 nm metal plate at height vacuum_gap above the bottom metal,
    with its own MA / MS layers and silicon above. Both metals are at 1 V (they are
    the same electrode in the 3D model, joined by the bump).
    """
    half_width_um: float = 30.0
    metal_thickness_nm: float = 50.0
    region_B_um: tuple = (29.0, 29.5)     # MS / MA: 0.5-1 um inside the edge
    region_C_um: tuple = (29.5, 30.0)     # MS / MA: last 0.5 um
    SA_B_um: tuple = (30.5, 31.0)         # SA: 0.5-1 um outside the edge
    SA_C_um: tuple = (30.0, 30.5)         # SA: first 0.5 um outside the edge
    swept_vacuum_gap_um: tuple = (1, 10, 1)   # FLIPMON_large_top_vacuum: LIN 1um 10um 1um
    region_padding_percent: dict = field(default_factory=lambda: {
        "FLIPMON_large_top_vacuum": {"+x": 30, "+y": 100, "-y": 20},
        "FLIPMON_equal_top_vacuum": {"+x": 40, "+y": 40, "-y": 40},
    })
    # Calculator definitions (both projects):
    #   Fms = (MS_B + MS_C) / MS_B
    #   Fma = (MA_B + MA_C + MA_metal_edge) / MA_B      (denominator uses a *volume* integral
    #                                                     of MA_B in the project, see README)
    #   Fsa = (SA_B + SA_C) / SA_B


EDGE_MODEL_2D = FlipmonEdge2D()

# Edge factors as used. They differ between the Ansys calculator and the spreadsheet:
F_FROM_ANSYS = {                                      # coefficient / interior prefactor
    "MS": 1.781964941295e-07 / 2.12415e-08,           # = 8.389
    "MA": 9.169215e-08 / 1.5e-10,                     # = 611.28
    "SA": 1.62771e-09 / 1.5e-10,                      # = 10.85
}
F_FROM_SPREADSHEET = {"MS": 8.389, "MA": 616.28, "SA": 10.85}   # 'calculation flipmon' B20:B22


# Li et al., "Vacuum-gap transmon qubits realized using flip-chip technology",
# Appl. Phys. Lett. 119, 184003 (2021), arXiv:2106.00341, Table I. Their own flipmon
# geometry (not the same dimensions as FLIPMON), 3 nm eps = 10 layers, 5 um gap.
# They state the corner effect can be ignored, i.e. no edge correction was applied.
LI_2021_TABLE_I = {
    "top_substrate": 0.105, "top_MS": 1.31e-5, "top_SA": 1.12e-5, "top_MA": 3.32e-5,
    "vacuum": 0.532, "indium_bump": 1.03e-11,
    "bottom_substrate": 0.363, "bottom_MS": 3.86e-5, "bottom_SA": 1.20e-4, "bottom_MA": 2.07e-5,
}


# ---------------------------------------------------------------------------
# Quick look: python flipmon_design.py  -> flipmon_layout.png
# ---------------------------------------------------------------------------

def spr_table(ax, d: FlipmonDesign = FLIPMON):
    """p for each chip from data/ansys_field_integrals.json, next to Li et al. (2021) Table I."""
    import csv
    import json
    from pathlib import Path
    from flipmon_spr_calculation import FlipmonIntegrals, surface_participation

    here = Path(__file__).resolve().parent
    js = json.loads((here / "data" / "ansys_field_integrals.json").read_text())
    p = surface_participation(FlipmonIntegrals(**js["integrals"], SA_perp=js.get("SA_perp"))).p
    # chip / carrier / vacuum split of the energy: the vacuum-gap sweep at this gap
    with open(here / "data" / "epr_vs_vacuum_gap.csv", newline="") as f:
        epr = next(r for r in csv.DictReader(f) if float(r["vacuum_gap_um"]) == d.vacuum_gap)
    L = LI_2021_TABLE_I

    def e(x):
        return f"{x:.2e}"

    rows, cells = [], []
    for side, sub in (("top", "top_chip_epr"), ("bottom", "bottom_chip_epr")):
        chip = "top chip" if side == "top" else "bottom chip"
        rows.append(f"{chip}: substrate")
        cells.append([f"{float(epr[sub]):.3f}", f"{float(epr[sub]):.3f}", f"{L[side + '_substrate']:.3f}"])
        for iface in ("MS", "SA", "MA"):
            rows.append(f"{chip}: p_{iface}")
            cells.append([e(p[f"{side}_{iface}_interior"]), e(p[f"{side}_{iface}"]), e(L[f"{side}_{iface}"])])
        if side == "top":
            rows.append("vacuum gap")
            cells.append([f"{float(epr['vacuum_epr']):.3f}"] * 2 + [f"{L['vacuum']:.3f}"])
    rows += ["total p_MS", "total p_SA", "total p_MA"]
    for iface in ("MS", "SA", "MA"):
        cells.append([e(p[f"bottom_{iface}_interior"] + p[f"top_{iface}_interior"]), e(p[iface]),
                      e(L[f"bottom_{iface}"] + L[f"top_{iface}"])])

    ax.axis("off")
    cols = ["this work,\ninterior only (region A)", "this work,\nwith edge correction",
            "Li et al. 2021\n(Table I)"]
    tb = ax.table(cellText=cells, rowLabels=rows, colLabels=cols, loc="upper center",
                  cellLoc="center", colWidths=[0.2, 0.2, 0.2])
    tb.auto_set_font_size(False)
    tb.set_fontsize(8)
    tb.scale(1.0, 1.35)
    for (r, c), cell in tb.get_celld().items():
        if r == 0:
            cell.set_text_props(weight="bold")
            cell.set_height(cell.get_height() * 1.8)
        if c == 2:
            cell.set_facecolor("#f2f2f2")
        if c == 1 and r > 0:
            cell.set_text_props(weight="bold")
    ax.set_title(f"Participation ratios (this design: vacuum_gap {d.vacuum_gap:g} um)", fontsize=9)
    ax.text(0.5, 0.1,
            "Note: in the paper, Li et al. most likely did not account for edge convergence: they "
            "integrate the 3D field directly, with no 2D edge correction\n(they only note that the "
            "corner effect near the indium bump can be ignored). Their values are closest to the "
            "interior-only column; here the edge correction\n(Wang et al. 2015) raises p_MS ~7x, "
            "p_SA ~10x and p_MA ~30x. Li et al. simulate their own flipmon geometry, not this one.",
            transform=ax.transAxes, ha="center", va="top", fontsize=8, style="italic", wrap=True)


def plot_design(d: FlipmonDesign = FLIPMON, path=None, table: bool = True):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Annulus, Rectangle, Circle

    if table:
        fig, axd = plt.subplot_mosaic([["a", "b", "c"], ["t", "t", "t"]], figsize=(15, 11),
                                      gridspec_kw={"width_ratios": [1, 1, 1.5], "height_ratios": [1, 1]})
        ax1, ax2, ax3 = axd["a"], axd["b"], axd["c"]
        spr_table(axd["t"], d)
    else:
        fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(15, 5.2),
                                            gridspec_kw={"width_ratios": [1, 1, 1.5]})
    metal, si = "#c9a227", "#e8e8e8"
    h = d.chip_size_um[0] / 2

    # bottom chip, top view
    ax1.add_patch(Rectangle((-h, -h), 2 * h, 2 * h, fc=si, ec="#999"))
    for (r0, r1), lab in ((d.inner_pad, "inner pad (pad 1)"), (d.outer_pad, "outer pad (pad 2)")):
        ax1.add_patch(Annulus((0, 0), r1, r1 - r0, fc=metal, ec="k", lw=0.5))
    ax1.add_patch(Circle((0, 0), d.bump_radius, fc="#8a8fa0", ec="k", lw=0.5))
    ax1.plot([-d.outer_pad[0] - 11, -d.inner_pad[1] + 1], [0, 0], color="r", lw=1.2)
    ax1.annotate("junction / port", (-65, 0), (-200, 160), fontsize=8,
                 arrowprops=dict(arrowstyle="->", lw=0.6))
    ax1.set_title("bottom chip (carrier), z = 0", fontsize=9)

    # top chip, top view (looking through the chip)
    ax2.add_patch(Rectangle((-h, -h), 2 * h, 2 * h, fc=si, ec="#999"))
    r0, r1 = d.chip_pad
    ax2.add_patch(Annulus((0, 0), r1, r1 - r0, fc=metal, ec="k", lw=0.5))
    ax2.add_patch(Circle((0, 0), d.bump_radius, fc="#8a8fa0", ec="k", lw=0.5))
    for (a, b) in (d.inner_pad, d.outer_pad):
        for rr in (a, b):
            ax2.add_patch(Circle((0, 0), rr, fc="none", ec="#555", lw=0.5, ls=":"))
    ax2.set_title(f"top chip pad, z = vacuum_gap (dotted: bottom pads)", fontsize=9)

    for ax in (ax1, ax2):
        ax.set_xlim(-h, h); ax.set_ylim(-h, h); ax.set_aspect("equal")
        ax.set_xlabel("x (um)"); ax.set_ylabel("y (um)")

    # side cross-section along +x (not to scale in z)
    zs = 6.0   # visual stretch of the vacuum gap
    g = d.vacuum_gap * zs
    ax3.add_patch(Rectangle((0, -40), h, 40, fc=si, ec="#999"))
    ax3.add_patch(Rectangle((0, g), h, 40, fc=si, ec="#999"))
    for (a, b) in (d.inner_pad, d.outer_pad):
        ax3.add_patch(Rectangle((a, -1.5), b - a, 1.5, fc=metal, ec="k", lw=0.5))
    ax3.add_patch(Rectangle((d.chip_pad[0], g), d.chip_pad[1] - d.chip_pad[0], 1.5, fc=metal, ec="k", lw=0.5))
    ax3.add_patch(Rectangle((0, 0), d.bump_radius, g, fc="#8a8fa0", ec="k", lw=0.5))
    ax3.text(h * 0.75, -20, "carrier (Si, 200 um)", ha="center", fontsize=8)
    ax3.text(h * 0.75, g + 20, "chip (Si, 200 um)", ha="center", fontsize=8)
    ax3.text(h * 0.75, g / 2, f"vacuum gap {d.vacuum_gap:g} um", ha="center", va="center", fontsize=8)
    ax3.text(d.bump_radius / 2, g / 2, "In\nbump", ha="center", va="center", fontsize=7)
    ax3.set_xlim(0, h); ax3.set_ylim(-40, g + 40)
    ax3.set_yticks([])
    ax3.set_xlabel("r (um)")
    ax3.set_title("side view (z not to scale)", fontsize=9)

    fig.suptitle(f"flipmon: cap_width {d.cap_width:g}, cap_gap {d.cap_gap:g}, "
                 f"vacuum_gap {d.vacuum_gap:g}, chip_radius {d.chip_radius:g} (um)", fontsize=10)
    fig.tight_layout()
    if path:
        fig.savefig(path, dpi=150, bbox_inches="tight")
    return fig


if __name__ == "__main__":
    from pathlib import Path

    d = FLIPMON
    print(f"{d.ansys_project} / {d.ansys_design}")
    print(f"  inner pad  r = {d.inner_pad[0]:g} .. {d.inner_pad[1]:g} um")
    print(f"  outer pad  r = {d.outer_pad[0]:g} .. {d.outer_pad[1]:g} um")
    print(f"  chip pad   r = {d.chip_pad[0]:g} .. {d.chip_pad[1]:g} um  at z = {d.vacuum_gap:g} um")
    print(f"  bump       r = {d.bump_radius:g} um, height {d.vacuum_gap:g} um")
    print("  edge factors  Ansys:", {k: round(v, 3) for k, v in F_FROM_ANSYS.items()},
          " sheet:", F_FROM_SPREADSHEET)
    out = Path(__file__).with_name("flipmon_layout.png")
    plot_design(d, out)
    print(f"saved {out}")
