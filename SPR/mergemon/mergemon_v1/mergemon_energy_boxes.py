"""
mergemon_energy_boxes.py
========================

Adds the four coupling-capacitor energy boxes, and the Fields Calculator
expressions that integrate the electric energy in them, to the 3D mergemon
HFSS project. The boxes were used for the "3d mergemon simulation" section of
``spr graphs.xlsx`` (tab Mergemon) but were not saved in
``full_mergemon_resonator_design.aedt``. This script puts them back.

    conda activate quantum-metal-env        # PyAEDT + AEDT 2024 R1
    python mergemon_energy_boxes.py         # opens AEDT (window stays open)

It reads ``full_mergemon_resonator_design.aedt`` (a copy of
E:/Nimisha/Mergemon/full_mergemon_resonator_design.aedt) and saves
``full_mergemon_resonator_design_energy_boxes.aedt`` next to it. The projects
are not part of the repository; by default they are looked for in
``../../../SPR_testing/mergemon/`` (use --source / --target otherwise). Nothing is
solved: the energies in the spreadsheet were taken from the solved original
and are confirmed by measurement.

The boxes (non-model, so they do not change the mesh)
-----------------------------------------------------
Lateral footprint: the T coupler and the two lanes beside its stem
(see mergemon_design.py):
    x : stem axis +/- max(T-bar half width, lane outer edge) = +/- 6.5 um
    y : from the T-bar top (local y = -0.4 um) down to where the column
        narrows into the lanes (local y = -41 um)
Heights: each box fills the solved region in z
    cc_vac_top        metal plane (z = 0) -> top of the Region   (vacuum)
    cc_top_substrate  z = 0 -> -0.22 um                          (SOI device layer)
    cc_vac_below      z = -0.22 -> -3.22 um                       (etched vacuum gap)
    cc_bottom_substrate z = -3.22 um -> bottom of the handle      (Si handle)

The footprint is RECONSTRUCTED from the layout code (the original boxes were
not saved). The heights follow the original instruction: as tall as the
solved region, up to the vacuum edge or down to the substrate bottom.

Calculator expressions, in the same form as mergemon_3D_SPR.aedt:
    U_box = (eps0 eps_r / 2) * integral of Re(E . E*) dV
"""

from __future__ import annotations

import argparse
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The .aedt projects are kept outside the repository (they are large). Default:
# <SPR folder>/../SPR_testing/mergemon/; override with --source / --target.
ANSYS_DIR = HERE.parents[2] / "SPR_testing" / "mergemon"      # SPR/mergemon/mergemon_v1 -> SPR/..
SOURCE = ANSYS_DIR / "full_mergemon_resonator_design.aedt"
TARGET = ANSYS_DIR / "full_mergemon_resonator_design_energy_boxes.aedt"
DESIGN = "HFSSDesign1"
AEDT_VERSION = "2024.1"

EPS0 = 8.8541878128e-12
EPS_SI = 11.9                    # Ansys "silicon", the material of Box1 / Box2

# SOI stack of the 3D model (Box1 = device layer, Box2 = handle)
SOI_DEVICE_UM = 0.22
SOI_GAP_UM = 3.0

# Coupling-capacitor geometry, local layout coordinates (mergemon_design.OPTIONS)
T_BAR_TOP = -0.4                 # t_bar_top
T_BAR_WIDTH = 13.0               # t_bar_width
T_STEM_WIDTH = 3.0               # t_stem_width
T_BAR_HEIGHT = 3.0               # t_bar_height
T_LENGTH = 34.0                  # t_length
CC_GAP = 1.5                     # cc_gap
CC_LANE_WIDTH = 3.0              # cc_lane_width
COL_TAPER_TOP = -41.0            # col_taper_top
# bottom-squid island extent in local x (pad_x of left cell, right pad's right edge)
ISLAND_X_MIN = -10.9             # pad_x
ISLAND_X_MAX = 18.7 - 10.9 + 3.4 # cell_pitch + pad_x + pad_width

BOX_HALF_WIDTH = max(T_BAR_WIDTH / 2, T_STEM_WIDTH / 2 + CC_GAP + CC_LANE_WIDTH)   # 6.5 um
STEM_TIP_Y = T_BAR_TOP - T_BAR_HEIGHT - T_LENGTH                                     # -37.4 um


def box_footprint_from_island(island_bbox):
    """(x0, y0, x1, y1) of the box footprint in HFSS coordinates.

    The island (SQUID_bottom) is the bar + pads + arms + T; its lowest point is the
    T stem tip and its left edge is the left pad, which fixes the local origin.
    """
    xmin, ymin = island_bbox[0], island_bbox[1]
    x_axis = xmin - ISLAND_X_MIN                # local x = 0 (the stem axis)
    y_origin = ymin - STEM_TIP_Y                # local y = 0 (SQUID bar top)
    return (x_axis - BOX_HALF_WIDTH, y_origin + COL_TAPER_TOP,
            x_axis + BOX_HALF_WIDTH, y_origin + T_BAR_TOP)


def add_energy_expression(fr, name, volume, eps_r):
    """Named expression: (eps0 eps_r / 2) * integral Re(E . E*) dV over `volume`."""
    fr.CalcStack("clear")
    fr.EnterQty("E")
    fr.CalcOp("Conj")
    fr.EnterQty("E")
    fr.CalcOp("Dot")
    fr.CalcOp("Real")
    fr.EnterVol(volume)
    fr.CalcOp("Integrate")
    fr.EnterScalar(EPS0 * eps_r / 2)
    fr.CalcOp("*")
    fr.AddNamedExpression(name, "Fields")
    fr.CalcStack("clear")


def add_sum_expression(fr, name, parts):
    fr.CalcStack("clear")
    fr.CopyNamedExprToStack(parts[0])
    for p in parts[1:]:
        fr.CopyNamedExprToStack(p)
        fr.CalcOp("+")
    fr.AddNamedExpression(name, "Fields")
    fr.CalcStack("clear")


def main(graphical: bool = True, source: Path = SOURCE, target: Path = TARGET):
    import shutil
    from ansys.aedt.core import Hfss

    shutil.copyfile(source, target)
    hfss = Hfss(project=str(target), design=DESIGN, version=AEDT_VERSION,
                non_graphical=not graphical, new_desktop=True, close_on_exit=not graphical)
    try:
        m = hfss.modeler
        m.model_units = "um"
        island = m["SQUID_bottom"].bounding_box
        region = m["Region"].bounding_box
        handle = m["Box2"].bounding_box
        dx, dy = island[3] - island[0], island[4] - island[1]
        print(f"SQUID_bottom bbox (um): {[round(v, 3) for v in island]}  size {dx:.2f} x {dy:.2f}")
        expected = (ISLAND_X_MAX - ISLAND_X_MIN)
        if abs(dx - expected) > 1.0:
            print(f"WARNING: island width {dx:.2f} um, expected {expected:.2f} um from the layout "
                  "code: check the box footprint in the AEDT window.")

        x0, y0, x1, y1 = box_footprint_from_island(island)
        z_top, z_bottom = region[5], handle[2]
        layers = [
            ("cc_vac_top", 0.0, z_top, "vacuum", 1.0),
            ("cc_top_substrate", -SOI_DEVICE_UM, 0.0, "silicon", EPS_SI),
            ("cc_vac_below", -SOI_DEVICE_UM - SOI_GAP_UM, -SOI_DEVICE_UM, "vacuum", 1.0),
            ("cc_bottom_substrate", z_bottom, -SOI_DEVICE_UM - SOI_GAP_UM, "silicon", EPS_SI),
        ]
        for name, za, zb, mat, _ in layers:
            b = m.create_box([x0, y0, za], [x1 - x0, y1 - y0, zb - za], name=name, material=mat)
            b.model = False
            b.transparency = 0.6
            print(f"{name:20s} x {x0:.2f}..{x1:.2f}  y {y0:.2f}..{y1:.2f}  z {za:.2f}..{zb:.2f} um")

        fr = hfss.ofieldsreporter
        for name, _, _, _, eps in layers:
            add_energy_expression(fr, f"U_{name}", name, eps)
        add_sum_expression(fr, "U_coupling_cap", [f"U_{n}" for n, *_ in layers])
        # totals used in the spreadsheet: substrate (Box1 + Box2) and vacuum (Region)
        add_energy_expression(fr, "U_device_layer", "Box1", EPS_SI)
        add_energy_expression(fr, "U_handle", "Box2", EPS_SI)
        add_sum_expression(fr, "U_substrate", ["U_device_layer", "U_handle"])
        add_energy_expression(fr, "U_vacuum", "Region", 1.0)

        hfss.save_project()
        print(f"saved {target}")
    finally:
        if not graphical:
            hfss.release_desktop(close_projects=True, close_desktop=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Add the coupling-capacitor energy boxes to the 3D mergemon project.")
    ap.add_argument("--non-graphical", action="store_true")
    ap.add_argument("--source", type=Path, default=SOURCE, help="original 3D project (.aedt)")
    ap.add_argument("--target", type=Path, default=TARGET, help="where to save the project with boxes")
    a = ap.parse_args()
    main(graphical=not a.non_graphical, source=a.source, target=a.target)
