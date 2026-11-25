"""Isolated transmon: two capacitor pads in a ground pocket (Quantum Metal).

Matches the ADS Q_TransmonPocket instance in the lab (Main Pad tab), with no
connection / corner pads. Same pattern as the tutorial's transmon_design.py:
one `build_design()` so the GDS check and a later HFSS runner share geometry.

The junction is NOT drawn in metal. TransmonPocket records it as a 'junction'
qgeometry (a line across the pad gap); the GDS has just the empty gap, and in
HFSS Eigenmode that gap gets a lumped RLC sheet (L = Lj, optional C = Cj).

Quantum Metal is the current name of the package formerly called Qiskit Metal.
The import namespace is still `qiskit_metal`, so the imports below are
unchanged; verified against quantum-metal 0.8.1 in the `hfss_env` environment.
"""

from qiskit_metal import designs, Dict
from qiskit_metal.qlibrary.qubits.transmon_pocket import TransmonPocket


DEFAULTS = Dict(
    # --- chip (not specified by the lab) ---
    chip_size_x="2mm",
    chip_size_y="2mm",
    chip_size_z="-675um",
    chip_material="silicon",

    # --- ADS Main Pad tab ---
    pad_gap="30um",        # Gap: pad-to-pad, where the junction sits
    pad_width="455um",     # Width
    pad_height="100um",    # Height (the library default is 90um)
    pocket_width="650um",  # Pocket Width
    pocket_height="650um", # Pocket Height
    # ADS "Inductor Offset" = 0 and "Radius" = 0: TransmonPocket has neither
    # option and both are 0 here, so nothing is lost.

    # Width of the junction sheet HFSS will put in the gap (not in the ADS tab).
    inductor_width="20um",
)


def build_design(**overrides):
    """Build the isolated transmon.

    Args:
        **overrides: any key of DEFAULTS, e.g. build_design(pad_width='500um').

    Returns:
        (design, names) where `names` maps role -> component / qgeometry name.
    """
    p = Dict(DEFAULTS)
    unknown = set(overrides) - set(p)
    if unknown:
        raise KeyError(f"unknown design parameter(s): {sorted(unknown)}. "
                       f"Valid keys: {sorted(p)}")
    p.update(overrides)

    design = designs.DesignPlanar()
    design.overwrite_enabled = True
    design.chips.main.size.size_x = p.chip_size_x
    design.chips.main.size.size_y = p.chip_size_y
    design.chips.main.size.size_z = p.chip_size_z
    design.chips.main.material = p.chip_material

    # connection_pads left empty -> no corner/readout pads.
    TransmonPocket(design, "Q1", options=Dict(
        pos_x="0mm", pos_y="0mm", orientation="0",
        pad_gap=p.pad_gap, pad_width=p.pad_width, pad_height=p.pad_height,
        pocket_width=p.pocket_width, pocket_height=p.pocket_height,
        inductor_width=p.inductor_width,
        connection_pads=Dict(),
    ))

    design.rebuild()
    names = Dict(qubit="Q1", junction="rect_jj")
    return design, names


if __name__ == "__main__":
    d, n = build_design()
    print("components:", list(d.components.keys()))
    print(d.qgeometry.tables["junction"][["component", "name", "width"]])
