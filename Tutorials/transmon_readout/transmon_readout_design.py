"""Transmon capacitively coupled to a lambda/2 readout resonator (Quantum Metal).

The qubit is the solved isolated design (f01 = 4.999 GHz, alpha = -224 MHz at
Lj = 10 nH: 620 x 120 um pads, 815 x 650 um pocket). One corner pad (top right,
as in the ADS Q_TransmonPocket instance) couples it to a meandered CPW that ends
open -- matching the ADS schematic TR -> TL2 (CpwLine) -> TL1 (CpwMeander) ->
TL3 (CpwLineOpen). Both ends are capacitively loaded, so it is a lambda/2 line.

Same pattern as the tutorial's transmon_design.py: one `build_design()` shared
by the GDS check and the HFSS eigenmode runner.

Quantum Metal is the current name of the package formerly called Qiskit Metal.
The import namespace is still `qiskit_metal`, so the imports below are
unchanged; verified against quantum-metal 0.8.1 in the `hfss_env` environment.
"""

import numpy as np
from qiskit_metal import designs, Dict
from qiskit_metal.qlibrary.qubits.transmon_pocket import TransmonPocket
from qiskit_metal.qlibrary.terminations.open_to_ground import OpenToGround
from qiskit_metal.qlibrary.tlines.meandered import RouteMeander


DEFAULTS = Dict(
    # --- chip (not specified) ---
    chip_size_x="5mm",
    chip_size_y="3.5mm",
    chip_size_z="-675um",
    chip_material="silicon",

    # --- CPW (lab 2 values): resonator and the coupling-pad lead ---
    cpw_width="15um",
    cpw_gap="8um",

    # --- qubit: ADS Main Pad tab, the solved isolated design ---
    pad_gap="30um",
    pad_width="620um",
    pad_height="120um",
    pocket_width="815um",
    pocket_height="650um",
    inductor_width="20um",

    # --- coupling pad: ADS Corner Pads tab, Top Right enabled ---
    cp_gap="15um",          # Gap: coupling pad -> nearest qubit pad
    cp_width="125um",       # Width
    cp_height="30um",       # Height
    cp_cpw_shift="5um",     # CPW Shift
    cp_cpw_extent="25um",   # CPW Extend (the library's pad_cpw_extent)
    cp_pocket_extent="5um", # Pocket Extent
    cp_pocket_rise="65um",  # library default
    cp_lead_out="100um",    # library cpw_extend: lead length past the pocket

    # --- readout resonator ---
    # 8.81 mm targets f_r = 6.5 GHz. Calibrated from the solved 8 mm point
    # (7.1347 GHz), not from a textbook formula: the coupling pad and open end
    # add a FIXED 0.278 mm of effective length that does not scale, so f_r
    # falls slightly slower than 1/L. Pure 1/L would have said 8.78 mm.
    res_total_length="8.81mm",
    res_span="2.4mm",       # straight-line distance pin -> open end (x)
    res_spacing="200um",
    res_fillet="50um",
    res_lead_straight="200um",  # the ADS TL2 straight section before the meander
    res_open_gap="8um",         # open-end gap to ground (= cpw_gap)
)


class MeanderGeometryError(ValueError):
    """A RouteMeander produced a segment too short for its fillet radius."""


def _check_meander(design, comp, fillet):
    # RouteMeander dumps leftover length into its last segment; the library
    # only warns, and HFSS later fails to fillet it with a bare COM error.
    # Interior segments are filleted at both ends (need 2f), end segments at one.
    f = design.parse_value(fillet)
    pts = np.asarray(design.components[comp].get_points(), dtype=float)
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    need = np.full(len(seg), 2 * f)
    need[[0, -1]] = f
    bad = np.flatnonzero(seg < need - 1e-9)
    if bad.size:
        i = bad[0]
        raise MeanderGeometryError(
            f"{comp}: segment {i} is {seg[i]*1e3:.1f}um, needs >= "
            f"{need[i]*1e3:.1f}um for a {f*1e3:.1f}um fillet. "
            f"Nudge res_total_length, res_span or res_spacing.")


def build_design(**overrides):
    """Build the transmon + readout resonator.

    Args:
        **overrides: any key of DEFAULTS, e.g. build_design(res_total_length='7.5mm').

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
    design.variables["cpw_width"] = p.cpw_width
    design.variables["cpw_gap"] = p.cpw_gap

    # Qubit centred left of the chip centre so the resonator fits to its right.
    q_x = -(design.parse_value(p.res_span) + design.parse_value(p.pocket_width)) / 2
    TransmonPocket(design, "Q1", options=Dict(
        pos_x=f"{q_x}mm", pos_y="0mm", orientation="0",
        pad_gap=p.pad_gap, pad_width=p.pad_width, pad_height=p.pad_height,
        pocket_width=p.pocket_width, pocket_height=p.pocket_height,
        inductor_width=p.inductor_width,
        connection_pads=Dict(readout=Dict(
            loc_W="+1", loc_H="+1",
            pad_gap=p.cp_gap, pad_width=p.cp_width, pad_height=p.cp_height,
            pad_cpw_shift=p.cp_cpw_shift, pad_cpw_extent=p.cp_cpw_extent,
            pocket_extent=p.cp_pocket_extent, pocket_rise=p.cp_pocket_rise,
            cpw_extend=p.cp_lead_out,
            cpw_width=p.cpw_width, cpw_gap=p.cpw_gap)),
    ))

    # Open end placed res_span to the right of the coupling-pad pin, same y.
    pin = design.components["Q1"].pins["readout"]
    x_open = pin.middle[0] + design.parse_value(p.res_span)
    OpenToGround(design, "res_open", options=Dict(
        pos_x=f"{x_open}mm", pos_y=f"{pin.middle[1]}mm", orientation="0",
        width=p.cpw_width, gap=p.cpw_gap, termination_gap=p.res_open_gap))

    RouteMeander(design, "readout_res", options=Dict(
        pin_inputs=Dict(start_pin=Dict(component="Q1", pin="readout"),
                        end_pin=Dict(component="res_open", pin="open")),
        total_length=p.res_total_length, fillet=p.res_fillet,
        lead=Dict(start_straight=p.res_lead_straight, end_straight="100um"),
        meander=Dict(spacing=p.res_spacing, asymmetry="0um")))

    design.rebuild()
    _check_meander(design, "readout_res", p.res_fillet)

    names = Dict(qubit="Q1", junction="rect_jj", resonator="readout_res",
                 open_end="res_open")
    return design, names


if __name__ == "__main__":
    d, n = build_design()
    print("components:", list(d.components.keys()))
