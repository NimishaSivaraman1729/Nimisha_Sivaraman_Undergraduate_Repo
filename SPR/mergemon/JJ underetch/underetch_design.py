"""
underetch_design.py
===================

2D electrostatic cross-section of the mergemon Josephson junction with the
silicon underetched at its edge: design ``2d_etched_edges`` in
``E:/Nimisha/Mergemon/mergemon_3D_SPR.aedt`` (Maxwell 2D, XY). The junction
is treated as infinitely long (a 2D cross-section), so this gives how the
surface participation *at the junction* changes with the underetch, not the
participation of the whole qubit.

Cross-section (x across the junction, y up; lengths in um)
----------------------------------------------------------
    silicon substrate     x 0..5, y 0..-10, minus a circular pit of radius
                          etch_radius centred on the metal edge (x = 0.503, y = 0)
    bottom electrode      x 0..0.5, y 0.003..0.053   (50 nm, perfect conductor)
    tunnel barrier        x 0..0.5, y 0.053..0.056   (3 nm, Al2O3_ceramic)
    top electrode         x 0..0.5, y 0.056..0.106   (50 nm, perfect conductor)
    lossy layers, all 3 nm thick, eps_r = 10 ("permttivity10" in the project):
      MS        under the bottom electrode, only where it still rests on silicon
                (x 0 .. 0.503 - etch_radius)
      MA_bottom under the bottom electrode where the silicon is etched away
      MA_side   on the electrode side wall, MA_top on the top electrode
      SA        lining the etch pit plus the flat silicon beyond it
    boundaries: one electrode at 1 V, the other at 0 V; even symmetry at x = 0
                (junction centre), odd symmetry at the far side
    mesh: 3 nm on every lossy layer; 30 passes, 2e-5 percent error
    sweep: etch_radius = 0.35 .. 0.5 um in 0.05 um steps (project), plus 0.55
           and 0.6 um (spreadsheet)

Because the lossy layers are meshed explicitly, each participation is just
the electrostatic energy in that layer over the total (no boundary-condition
prefactors as in Wang et al.):

    MS_SPR       = Energy(MS) / Energy(all)
    MA_SPR       = Energy(MA_bottom + MA_side + MA_top) / Energy(all)
    SA_SPR       = Energy(SA) / Energy(all)
    junction_EPR = Energy(barrier) / Energy(all)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UnderetchCrossSection:
    etch_radius_um: float = 0.45          # project value
    electrode_half_width_um: float = 0.5  # x = 0 is the symmetry plane
    electrode_thickness_um: float = 0.05
    barrier_thickness_um: float = 0.003
    barrier_material: str = "Al2_O3_ceramic"
    layer_thickness_um: float = 0.003
    layer_eps_r: float = 10.0
    substrate_width_um: float = 5.0
    substrate_depth_um: float = 10.0
    ansys_project: str = "E:/Nimisha/Mergemon/mergemon_3D_SPR.aedt"
    ansys_design: str = "2d_etched_edges"

    @property
    def edge_x(self) -> float:
        """Outer edge of the metal including the side-wall layer."""
        return self.electrode_half_width_um + self.layer_thickness_um

    @property
    def ms_width_um(self) -> float:
        """Length of metal still resting on silicon (MS layer); 0 once the pit reaches x = 0."""
        return max(0.0, self.edge_x - self.etch_radius_um)


SWEEP_NM = [350, 400, 450, 500, 550, 600]


def plot_cross_section(c: UnderetchCrossSection = UnderetchCrossSection(), path=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle, Wedge

    t, h, w = c.layer_thickness_um, c.electrode_thickness_um, c.electrode_half_width_um
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.add_patch(Rectangle((0, -1.0), 1.4, 1.0, fc="#e8e8e8", ec="none", label="silicon"))
    ax.add_patch(Wedge((c.edge_x, 0), c.etch_radius_um, 180, 360, fc="white", ec="#c0392b", lw=1.5,
                       label=f"etch pit (r = {c.etch_radius_um * 1e3:.0f} nm), SA on its surface"))
    ax.add_patch(Rectangle((0, t), w, h, fc="#c9a227", label="electrodes (50 nm)"))
    ax.add_patch(Rectangle((0, t + h), w, c.barrier_thickness_um, fc="#3b6fb6", label="barrier (3 nm)"))
    ax.add_patch(Rectangle((0, t + h + c.barrier_thickness_um), w, h, fc="#c9a227"))
    ax.plot([0, c.ms_width_um], [0, 0], color="#8e44ad", lw=3, label="MS (metal on Si)")
    ax.axvline(0, color="k", ls=":", lw=0.8)
    ax.text(0.01, -0.95, "symmetry plane", fontsize=8)
    ax.set_xlim(-0.05, 1.4); ax.set_ylim(-1.0, 0.3)
    ax.set_xlabel("x (um)"); ax.set_ylabel("y (um)")
    ax.set_title("underetched junction cross-section (not to scale in y for the 3 nm layers)", fontsize=9)
    ax.legend(fontsize=7, loc="lower right")
    fig.tight_layout()
    if path:
        fig.savefig(path, dpi=150)
    return fig


if __name__ == "__main__":
    from pathlib import Path
    c = UnderetchCrossSection()
    for r in SWEEP_NM:
        cc = UnderetchCrossSection(etch_radius_um=r / 1000)
        print(f"etch radius {r} nm: metal still on silicon for {cc.ms_width_um * 1e3:.0f} nm")
    out = Path(__file__).with_name("underetch_cross_section.png")
    plot_cross_section(c, out)
    print(f"saved {out}")
