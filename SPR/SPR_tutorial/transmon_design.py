"""
transmon_design.py
==================

Geometry and simulation settings for the transmon surface-participation (SPR)
study, transcribed from the Ansys Electronics Desktop project
``old_flipmon_design/transmon_spr.aedt`` (AEDT 2024 R1).

The three designs that were simulated in that project are described here:

    HFSS (3D, "HFSS Terminal Network", driven at 5 GHz, lumped port across the junction)
      * ``designA``       - 250 x 500 um double-pad transmon, 100 um pad gap
      * ``designC``       - narrow-trace (6 um wide) electrodes; this design
                             holds the complete Wang et al. calculation in its
                             Fields Calculator (pMA / pMS, interior + perimeter)

    Maxwell 2D (electrostatic)
      * ``Maxwell2DDesign1`` - cross-section of one metal edge, used to get the
                               edge-correction factors F_MS and F_MA

Conventions
-----------
* All lengths are in micrometres unless the name says otherwise.
* The chip is in the z = 0 plane: substrate for z < 0, vacuum for z > 0.
* Metal is modelled in 3D as a zero-thickness perfect-E sheet.
* Every in-plane shape is an axis-aligned rectangle ``Rect(x0, y0, x1, y1)``.
  Each design lists the *upper* electrode (y > 0). The lower electrode is
  its mirror image through y = 0 (in Ansys it was made with a mirror or a
  180-degree rotation about z; both give the same shape because every
  electrode is symmetric in x).

How the interface surfaces are set up in 3D (same scheme in every HFSS design)
------------------------------------------------------------------------------
The thin lossy layers (MA, MS, SA) are not meshed in 3D. Instead the field is
sampled on sheets placed ``INTERFACE_OFFSET_UM`` (1 nm) above the metal
(MA, vacuum side) or below the metal (MS, substrate side). Each sheet is
split by distance from the metal edge, following Wang et al. (2015):

    region A ("interior")  : more than 1 um inside the edge   -> 3D field is trusted
    region B ("perimeter") : 0.5 to 1 um inside the edge      -> 3D field, then scaled by F
    region C               : last 0.5 um at the edge          -> only resolved in the 2D model

The 2D model supplies F = (U_B + U_C) / U_B, so the 3D region-B integral
times F stands in for regions B + C together.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Basic building blocks
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle in the chip plane, corners (x0, y0) and (x1, y1), in um."""
    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def from_ansys(cls, x_start, y_start, width, height):
        """Build from Ansys 'Create Rectangle' fields (Width / Height may be negative)."""
        return cls(min(x_start, x_start + width), min(y_start, y_start + height),
                   max(x_start, x_start + width), max(y_start, y_start + height))

    def mirrored_y(self) -> "Rect":
        """Mirror through the y = 0 line (gives the lower electrode)."""
        return Rect(self.x0, -self.y1, self.x1, -self.y0)

    def inset(self, d: float) -> "Rect":
        return Rect(self.x0 + d, self.y0 + d, self.x1 - d, self.y1 - d)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height


@dataclass(frozen=True)
class Material:
    name: str
    eps_r: float


@dataclass(frozen=True)
class InterfaceLayer:
    """Thin lossy dielectric layer (Wang et al. 2015 assume 3 nm, eps_r = 10)."""
    name: str            # "MA", "MS" or "SA"
    thickness_nm: float
    eps_r: float


# ---------------------------------------------------------------------------
# Materials and interface assumptions used in transmon_spr.aedt
# ---------------------------------------------------------------------------

VACUUM = Material("vacuum", 1.0)
SILICON = Material("silicon", 11.9)            # Ansys library value used in the project
INTERFACE_DIELECTRIC = Material("permittivity10", 10.0)

INTERFACE_THICKNESS_NM = 3.0                   # thickness of every lossy layer
INTERFACE_LAYERS = {
    "MA": InterfaceLayer("MA", INTERFACE_THICKNESS_NM, INTERFACE_DIELECTRIC.eps_r),
    "MS": InterfaceLayer("MS", INTERFACE_THICKNESS_NM, INTERFACE_DIELECTRIC.eps_r),
    "SA": InterfaceLayer("SA", INTERFACE_THICKNESS_NM, INTERFACE_DIELECTRIC.eps_r),
}

INTERFACE_OFFSET_UM = 0.001   # sampling sheets sit 1 nm above (MA) / below (MS) the metal
REGION_A_INSET_UM = 1.0       # region A starts 1 um inside the metal edge
REGION_B_INSET_UM = 0.5       # region B is the band from 0.5 um to 1 um inside the edge


# ---------------------------------------------------------------------------
# 3D (HFSS) transmon designs
# ---------------------------------------------------------------------------

@dataclass
class TransmonDesign:
    name: str                          # HFSS design name inside transmon_spr.aedt
    description: str
    upper_electrode: list[Rect]        # metal shapes for y > 0 (lower electrode = mirror image)
    junction: Rect                     # thin lead bridging to the junction / lumped port
    substrate_size_um: tuple[float, float, float]   # (x, y, thickness) of the silicon block
    substrate_center_um: tuple[float, float] = (0.0, 0.0)
    region_padding_percent: dict = field(default_factory=dict)  # Ansys "Region" padding
    frequency_GHz: float = 5.0
    edge_mesh_um: float = 3.0          # length-based surface mesh on the metal sheets
    # MA / MS sampling sheets 1 nm off the metal as model objects (mesh conforms, as in the
    # original designA). In a tall model the 1 nm layer exceeds the mesher's 1e5 stack-ratio
    # limit, so designC uses non-model sheets, as the original designC did.
    model_sheets: bool = True
    substrate: Material = SILICON
    # Names of the Fields Calculator objects for each integral (see calculation file)
    calculator_surfaces: dict = field(default_factory=dict)
    notes: str = ""

    @property
    def lower_electrode(self) -> list[Rect]:
        return [r.mirrored_y() for r in self.upper_electrode]

    @property
    def all_metal(self) -> list[Rect]:
        return (self.upper_electrode + self.lower_electrode
                + [self.junction, self.junction.mirrored_y()])


# designA -------------------------------------------------------------------
# Pads 250 x 500 um starting 50 um from the centre line (100 um pad-to-pad gap),
# 25 x 35 um leads, and a 0.1 um wide lead into the junction gap (1 um).
DESIGN_A = TransmonDesign(
    name="designA",
    description="Double-pad transmon, 250 x 500 um pads, 100 um gap, on 400 um Si.",
    upper_electrode=[
        Rect.from_ansys(-125, 50, 250, 500),        # pad
        Rect.from_ansys(-12.5, 15, 25, 35),         # lead
    ],
    junction=Rect.from_ansys(-0.05, 0.5, 0.1, 14.5),
    substrate_size_um=(500, 1400, 400),
    region_padding_percent={"+x": 0, "-x": 0, "+y": 0, "-y": 0, "+z": 75, "-z": 0},
    calculator_surfaces={
        # region A (interior) and region B (perimeter band) sheets, both electrodes
        "MA_A": ["MA_interior_left", "MA_interior_right"],
        "MA_B": ["MA_B_perimeter_left", "MA_B_perimeter_right"],
        "MS_A": ["MS_interior_left", "MS_interior_right"],
        "MS_B": ["MS_B_perimeter_left", "MS_B_perimeter_right"],
        "SA":   ["SA_interior"],
        "substrate_volume": ["Box1"],
        "vacuum_volume": ["Region"],
    },
    notes=("Fields Calculator here has the raw |E|^2 integrals (silicon_energy, "
           "MA_interior_energy, MA_B_perimeter, MS_interior_energy, MS_B_perimeter_energy, "
           "SA_energy, vacuum_energy) and the energy participation ratios vacuum_epr / "
           "silicon_epr. NOTE: its 'vacuum_energy' already includes the factor 0.5."),
)

# designC -------------------------------------------------------------------
# Narrow-trace electrodes: a 6 um wide, 886 um long stem ending in an 80 x 80 um
# square, a 132 um crossbar, two 6 um legs and two 100 um feet. The lead into the
# junction is 0.05 um wide.
DESIGN_C = TransmonDesign(
    name="designC",
    description="Narrow-trace (6 um wide) transmon electrodes on 200 um Si.",
    upper_electrode=[
        Rect.from_ansys(-3, 30, 6, 886),            # stem
        Rect.from_ansys(-40, 916, 80, 80),          # end square
        Rect.from_ansys(-66, 60, 132, 6),           # crossbar
        Rect.from_ansys(-66, 60, 6, -51),           # left leg
        Rect.from_ansys(60, 60, 6, -51),            # right leg (mirror of left)
        Rect.from_ansys(-60, 9, -100, -6),          # left foot
        Rect.from_ansys(60, 9, 100, -6),            # right foot (mirror of left)
    ],
    junction=Rect.from_ansys(-0.025, 0.5, 0.05, 29.5),
    substrate_size_um=(520, 2120, 200),
    edge_mesh_um=5.0,
    model_sheets=False,
    region_padding_percent={"+x": 30, "-x": 30, "+y": 30, "-y": 30, "+z": 100, "-z": 20},
    calculator_surfaces={
        "MA_A": ["MA_interface_left", "MA_interface_right"],
        "MA_B": ["MA_B_left", "MA_B_right"],
        "MS_A": ["MS_interface_left", "MS_interface_right"],
        "MS_B": ["MS_B_left", "MS_B_right"],
        "substrate_volume": ["Box1"],
        "vacuum_volume": ["Region"],
    },
    notes=("This design's Fields Calculator holds the full Wang et al. calculation: "
           "total_energy, pMA_interior, pMS_interior, pMA_perimeter and pMS_perimeter, "
           "with the prefactors written in as constants. No SA surface is defined here."),
)

HFSS_DESIGNS = {d.name: d for d in (DESIGN_A, DESIGN_C)}

# Wang et al. (2015), Table S1: participation ratios of their designs A and C6
# (sapphire substrate), for comparison with designA and designC.
PAPER_SPR = {
    "designA": {"label": "Wang A", "MA": 0.11e-4, "MS": 0.99e-4, "SA": 1.18e-4},
    "designC": {"label": "Wang C6", "MA": 0.89e-4, "MS": 7.67e-4, "SA": 8.96e-4},
}


# ---------------------------------------------------------------------------
# 2D (Maxwell electrostatic) edge model -> correction factors F_MS, F_MA
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EdgeCrossSection2D:
    """Maxwell2DDesign1: cross-section through one edge of a strip (symmetry plane at x = 0)."""
    half_width_um: float = 30.0          # metal from x = 0 to 30 um
    metal_thickness_nm: float = 50.0
    substrate_width_um: float = 60.0
    substrate_depth_um: float = 30.0
    interface_thickness_nm: float = INTERFACE_THICKNESS_NM
    interface_eps_r: float = INTERFACE_DIELECTRIC.eps_r
    region_B_um: tuple[float, float] = (29.0, 29.5)   # x-range of region B
    region_C_um: tuple[float, float] = (29.5, 30.0)   # x-range of region C (at the edge)
    excitation: str = "1 V on the metal, electrostatic"
    mesh_max_length_nm: float = 3.0      # length-based mesh on every interface layer
    percent_error: float = 1e-4

    # Fields Calculator definitions used in the project:
    #   Fms = (MS_B_energy + MS_C_energy) / MS_B_energy
    #   Fma = (MA_B_energy + MA_C_energy + MA_metalEdge_energy) / MA_B_energy
    # MA includes the 3 nm layer on the metal side wall ("MA_metal_edge").


EDGE_MODEL_2D = EdgeCrossSection2D()

# The 2D results were written into designC's Fields Calculator as
#   pMS_perimeter coefficient = 1.88856e-07  = (MS prefactor 2.12415e-08) * F_MS
#   pMA_perimeter coefficient = 2.498325e-08 = (MA prefactor 1.5e-10)     * F_MA
# so the F values used in the study are:
F_MS_FROM_PROJECT = 1.88856e-07 / 2.12415e-08     # ~ 8.891
F_MA_FROM_PROJECT = 2.498325e-08 / 1.5e-10        # ~ 166.6


# ---------------------------------------------------------------------------
# Quick look: python transmon_design.py  -> writes transmon_layouts.png
# ---------------------------------------------------------------------------

def plot_design(design: TransmonDesign, ax=None, show_regions: bool = True):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle as MplRect

    if ax is None:
        _, ax = plt.subplots(figsize=(5, 6))
    sx, sy, _ = design.substrate_size_um
    cx, cy = design.substrate_center_um
    ax.add_patch(MplRect((cx - sx / 2, cy - sy / 2), sx, sy, fc="#e8e8e8", ec="#999999", lw=0.8,
                         label=f"Si substrate ({design.substrate_size_um[2]:g} um thick)"))
    for i, r in enumerate(design.all_metal):
        ax.add_patch(MplRect((r.x0, r.y0), r.width, r.height, fc="#c9a227", ec="none",
                             label="metal (PEC sheet)" if i == 0 else None))
    if show_regions:
        for i, r in enumerate(design.upper_electrode + design.lower_electrode):
            a = r.inset(REGION_A_INSET_UM)
            if a.width > 0 and a.height > 0:
                ax.add_patch(MplRect((a.x0, a.y0), a.width, a.height, fc="none", ec="#1f5fbf",
                                     lw=0.4, ls="--", label="region A boundary" if i == 0 else None))
    ax.set_aspect("equal")
    ax.set_xlim(cx - sx / 2, cx + sx / 2)
    ax.set_ylim(cy - sy / 2, cy + sy / 2)
    ax.set_xlabel("x (um)")
    ax.set_ylabel("y (um)")
    ax.set_title(f"{design.name}\n{design.description}", fontsize=8)
    return ax


def spr_table(ax):
    """Table of p_MA, p_MS, p_SA for each design (from spr_calculation.py) next to the paper."""
    from spr_calculation import FIELD_INTEGRALS, surface_participation

    cols, data = [], {k: [] for k in ("MA", "MS", "SA")}
    for name, d in HFSS_DESIGNS.items():
        p = (surface_participation(d, FIELD_INTEGRALS[name]).p
             if name in FIELD_INTEGRALS else {})
        cols.append(f"{name}\n(this code)")
        for k in data:
            data[k].append(f"{p[k]:.2e}" if k in p else "-")
        if name in PAPER_SPR:
            cols.append(f"{PAPER_SPR[name]['label']}\n(paper)")
            for k in data:
                data[k].append(f"{PAPER_SPR[name][k]:.2e}")
    ax.axis("off")
    tb = ax.table(cellText=[data[k] for k in ("MS", "MA", "SA")],
                  rowLabels=["p_MS", "p_MA", "p_SA"], colLabels=cols,
                  loc="center", cellLoc="center")
    tb.auto_set_font_size(False)
    tb.set_fontsize(8)
    tb.scale(1.0, 2.6)
    for (r, c), cell in tb.get_celld().items():
        if r == 0:
            cell.set_text_props(weight="bold")
            cell.set_height(cell.get_height() * 1.3)
        if c >= 0 and "paper" in cols[c]:
            cell.set_facecolor("#f2f2f2")
    ax.set_title("Surface participation ratios\n(Wang et al. 2015 paper: sapphire substrate)",
                 fontsize=8)


if __name__ == "__main__":
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from pathlib import Path

    for d in HFSS_DESIGNS.values():
        print(f"{d.name:14s} {d.description}")
        print(f"{'':14s} substrate {d.substrate_size_um} um, f = {d.frequency_GHz} GHz")
    print(f"\n2D edge model: F_MS = {F_MS_FROM_PROJECT:.4f}, F_MA = {F_MA_FROM_PROJECT:.2f}")

    fig, axes = plt.subplots(1, 3, figsize=(16, 6), gridspec_kw={"width_ratios": [1, 1, 1.3]})
    for ax, d in zip(axes, HFSS_DESIGNS.values()):
        plot_design(d, ax)
    axes[0].legend(loc="lower left", fontsize=7)
    spr_table(axes[2])
    fig.tight_layout()
    out = Path(__file__).with_name("transmon_layouts.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"saved {out}")
