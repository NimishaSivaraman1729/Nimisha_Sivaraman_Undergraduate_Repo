"""
transmon_ansys.py
=================

Build a transmon from ``transmon_design.py`` in Ansys HFSS, solve it, and
extract the |E|^2 integrals that ``spr_calculation.py`` needs.

Needs PyAEDT (``pip install pyaedt``, imported as ansys.aedt.core) and a local
AEDT install. On this workstation that is the ``quantum-metal-env`` conda env
and AEDT 2024 R1:

    conda activate quantum-metal-env
    python transmon_ansys.py designA                  # build + solve + extract
    python transmon_ansys.py designA --quick          # coarse convergence, for a fast check
    python transmon_ansys.py designC --no-solve      # just build and look at it
    python transmon_ansys.py edge2d                  # 2D edge model -> F_MS, F_MA, F_SA
    python transmon_ansys.py designA --non-graphical # batch mode, AEDT closes at the end
    python transmon_ansys.py designA --extract-only   # re-extract from a solved project

Output
------
../../SPR_testing/SPR_tutorial/ansys_runs/transmon_<design>.aedt   the generated project
data/ansys_<design>_field_integrals.json       the integrals; spr_calculation.py
                                               picks these up automatically

What gets built (same scheme as transmon_spr.aedt)
--------------------------------------------------
* silicon substrate under z = 0, vacuum Region around it (outer faces are
  perfect E, as in the original project: no radiation boundary)
* both electrodes as zero-thickness perfect-E sheets at z = 0, the junction
  lead included
* a 50 ohm lumped port across the 1 um junction gap (|y| < 0.5 um)
* driven solution at the design frequency (5 GHz). Modal solution type
  instead of the project's Terminal Network; for a single port the fields are the same.
* non-model sampling sheets (not meshed, the field is interpolated onto them):
      MA_A, MA_B : 1 nm above the metal, region A / region B
      MS_A, MS_B : 1 nm below the metal, region A / region B
      SA_A, SA_B : 1 nm above the bare substrate, > 1 um / 0.5-1 um from any metal edge
  Region A = more than 1 um inside the metal edge, region B = 0.5 to 1 um inside
  (``REGION_A_INSET_UM`` and ``REGION_B_INSET_UM`` in transmon_design.py).

Extracted quantities (plain integrals of |E|^2, SI units)
--------------------------------------------------------
substrate_volume, vacuum_volume, MA_A, MA_B, MS_A, MS_B, SA_total, SA_perp,
and SA_par = SA_total - SA_perp. SA_perp is the integral of |E_z|^2, which lets
spr_calculation.py treat the SA layer exactly instead of assuming the field
is all tangential.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from transmon_design import (
    EDGE_MODEL_2D,
    HFSS_DESIGNS,
    EdgeCrossSection2D,
    INTERFACE_OFFSET_UM,
    REGION_A_INSET_UM,
    REGION_B_INSET_UM,
    Rect,
    TransmonDesign,
)

HERE = Path(__file__).resolve().parent
# Generated .aedt projects are kept outside the repository, in
# <SPR folder>/../SPR_testing/SPR_tutorial/ansys_runs/
ANSYS_RUNS = HERE.parent.parent / "SPR_testing" / "SPR_tutorial" / "ansys_runs"
AEDT_VERSION = "2024.1"
JUNCTION_GAP_UM = 1.0          # port spans |y| < JUNCTION_GAP_UM / 2


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _sheet_union(m, rects: list[Rect], z: float, name: str, model: bool = True):
    """Union of axis-aligned rectangles in the plane z. Returns the AEDT object (or None)."""
    objs = []
    for i, r in enumerate(rects):
        if r.width <= 0 or r.height <= 0:
            continue
        o = m.create_rectangle("XY", [r.x0, r.y0, z], [r.width, r.height], name=f"{name}_{i}")
        objs.append(o)
    if not objs:
        return None
    if len(objs) > 1:
        m.unite(objs)
    o = objs[0]
    o.name = name
    o.model = model
    return o


def _band(m, outer_rects, inner_rects, z, name, model=False):
    """Sheet = union(outer_rects) minus union(inner_rects)."""
    outer = _sheet_union(m, outer_rects, z, name, model=model)
    inner = _sheet_union(m, inner_rects, z, name + "_tool", model=model)
    if inner is not None:
        m.subtract(outer, inner, keep_originals=False)
    outer.model = model
    return outer


def _outset(r: Rect, d: float) -> Rect:
    return r.inset(-d)


def build_transmon(hfss, d: TransmonDesign):
    m = hfss.modeler
    m.model_units = "um"
    sx, sy, sz = d.substrate_size_um
    cx, cy = d.substrate_center_um
    hfss.modeler.create_box([cx - sx / 2, cy - sy / 2, 0], [sx, sy, -sz], name="substrate",
                            material="silicon")

    # electrodes (pads + leads + junction lead), each a single perfect-E sheet
    upper = _sheet_union(m, d.upper_electrode + [d.junction], 0, "upper_electrode")
    lower = _sheet_union(m, d.lower_electrode + [d.junction.mirrored_y()], 0, "lower_electrode")
    hfss.assign_perfecte_to_sheets([upper.name], name="PerfE_upper")
    hfss.assign_perfecte_to_sheets([lower.name], name="PerfE_lower")
    # surface length mesh on the metal, as in the original designs (3 um designA, 5 um designC)
    hfss.mesh.assign_length_mesh([upper.name, lower.name], inside_selection=False,
                                 maximum_length=f"{d.edge_mesh_um}um", maximum_elements=None,
                                 name="metal_length")

    # lumped port across the junction gap
    w = d.junction.width
    g = JUNCTION_GAP_UM / 2
    port = m.create_rectangle("XY", [d.junction.x0, -g, 0], [w, 2 * g], name="port_sheet")
    hfss.lumped_port(port, create_port_sheet=False, integration_line=[[0, -g, 0], [0, g, 0]],
                     impedance=50, name="junction_port")

    # region (same padding as the original design)
    p = d.region_padding_percent
    m.create_region(pad_value=[p.get(k, 0) for k in ("+x", "-x", "+y", "-y", "+z", "-z")],
                    pad_type="Percentage Offset", name="Region")

    # sampling sheets
    pads = d.upper_electrode + d.lower_electrode       # the junction lead is too thin for an inset
    a_rects = [r.inset(REGION_A_INSET_UM) for r in pads]
    b_outer = [r.inset(REGION_B_INSET_UM) for r in pads]
    zo = INTERFACE_OFFSET_UM
    # MA / MS sheets as *model* objects where the geometry allows (designA, as in the
    # original): the mesh conforms to them, which resolves region B (with non-model
    # sheets designA's region-B integral came out ~2x too low). designC is too tall for
    # a 1 nm model layer (mesher stack-ratio limit), so it uses non-model sheets, as the
    # original designC did; its 5 um edge mesh then carries the resolution.
    ms = d.model_sheets
    _sheet_union(m, a_rects, +zo, "MA_A", model=ms)
    _sheet_union(m, a_rects, -zo, "MS_A", model=ms)
    _band(m, b_outer, a_rects, +zo, "MA_B", model=ms)
    _band(m, b_outer, a_rects, -zo, "MS_B", model=ms)

    metal = pads + [d.junction, d.junction.mirrored_y()]
    substrate_rect = Rect(cx - sx / 2, cy - sy / 2, cx + sx / 2, cy + sy / 2)
    _band(m, [substrate_rect], [_outset(r, REGION_A_INSET_UM) for r in metal], +zo, "SA_A")
    _band(m, [_outset(r, REGION_A_INSET_UM) for r in metal],
          [_outset(r, REGION_B_INSET_UM) for r in metal], +zo, "SA_B")


def add_setup(hfss, d: TransmonDesign, passes: int, delta_s: float):
    setup = hfss.create_setup("Setup1", setup_type="HFSSDriven")
    setup.props["Frequency"] = f"{d.frequency_GHz}GHz"
    setup.props["MaximumPasses"] = passes
    setup.props["MaxDeltaS"] = delta_s
    setup.props["MinimumConvergedPasses"] = 2
    setup.update()
    return setup


# ---------------------------------------------------------------------------
# Field extraction (Fields Calculator)
# ---------------------------------------------------------------------------

def integrate_E2(hfss, obj: str, kind: str, frequency_GHz: float, component: str | None = None,
                 solution: str = "Setup1 : LastAdaptive") -> float:
    """Integral of |E|^2 (or |E_z|^2 with component='z') over a surface or volume."""
    fr = hfss.ofieldsreporter
    fr.CalcStack("clear")
    if component is None:
        fr.CopyNamedExprToStack("Mag_E")              # complex magnitude of E (phase independent)
    else:
        fr.EnterQty("E")
        fr.CalcOp({"x": "ScalarX", "y": "ScalarY", "z": "ScalarZ"}[component])
        fr.CalcOp("CmplxMag")
    fr.EnterScalar(2)
    fr.CalcOp("Pow")
    (fr.EnterVol if kind == "volume" else fr.EnterSurf)(obj)
    fr.CalcOp("Integrate")
    out = Path(hfss.working_directory) / f"_calc_{obj}_{component or 'mag'}.fld"
    fr.CalculatorWrite(str(out), ["Solution:=", solution],
                       ["Freq:=", f"{frequency_GHz}GHz", "Phase:=", "0deg"])
    value = float(out.read_text().strip().splitlines()[-1])
    out.unlink()
    fr.CalcStack("clear")
    return value


def extract_integrals(hfss, d: TransmonDesign) -> dict:
    f = d.frequency_GHz
    I = {
        "substrate_volume": integrate_E2(hfss, "substrate", "volume", f),
        "vacuum_volume": integrate_E2(hfss, "Region", "volume", f),
    }
    for s in ("MA_A", "MA_B", "MS_A", "MS_B"):
        I[s] = integrate_E2(hfss, s, "surface", f)
    for s in ("SA_A", "SA_B"):
        I[s] = integrate_E2(hfss, s, "surface", f)
        I[s + "_perp"] = integrate_E2(hfss, s, "surface", f, component="z")
    return I


# ---------------------------------------------------------------------------
# 2D edge model (Maxwell 2D, electrostatic) -> F_MS, F_MA, F_SA
# ---------------------------------------------------------------------------

def build_and_solve_edge2d(m2d, e: EdgeCrossSection2D, passes: int = 30, percent_error: float = None,
                           cores: int = 4) -> dict:
    """Rebuild Maxwell2DDesign1 (transmon_spr.aedt) and return the edge factors.

    Cross-section in the XY plane: x across the strip, y vertical (substrate y < 0).
    The metal half-strip (0 < x < w) sits on a 3 nm MS layer, with a 3 nm MA layer on
    its top and side wall, and a 3 nm SA layer on the bare substrate beside it. Every
    layer is split into region A (> x0 from the edge), B (x0/2 .. x0) and C (< x0/2),
    with x0 = 1 um as in Wang et al. Boundaries as in the original: 1 V on the metal,
    even symmetry at the strip centre (x = 0), odd symmetry at the far side of the
    region (an opposite electrode mirrored there).
    """
    m = m2d.modeler
    m.model_units = "um"
    t = e.interface_thickness_nm * 1e-3
    h = e.metal_thickness_nm * 1e-3
    w = e.half_width_um
    xB0, xB1 = e.region_B_um          # 29.0 .. 29.5
    xC0, xC1 = e.region_C_um          # 29.5 .. 30.0
    d0 = w - xB0                       # x0 = 1 um

    if "permittivity10" not in m2d.materials.material_keys:
        mat = m2d.materials.add_material("permittivity10")
        mat.permittivity = e.interface_eps_r

    def rect(x0, y0, dx, dy, name, material):
        return m.create_rectangle([x0, y0, 0], [dx, dy], name=name, material=material)

    metal = rect(0, t, w, h, "metal", "pec")
    rect(0, 0, e.substrate_width_um, -e.substrate_depth_um, "substrate", "silicon")
    # MS (under the metal) and MA (on top), split A / B / C
    for name, y0 in (("MS", 0.0), ("MA", t + h)):
        rect(0, y0, xB0, t, f"{name}_A", "permittivity10")
        rect(xB0, y0, xB1 - xB0, t, f"{name}_B", "permittivity10")
        rect(xC0, y0, xC1 - xC0, t, f"{name}_C", "permittivity10")
    rect(w, t, t, h + t, "MA_edge", "permittivity10")               # side wall layer
    # SA beside the metal, split C (0 .. x0/2), B (x0/2 .. x0), A (rest)
    rect(w, 0, d0 / 2, t, "SA_C", "permittivity10")
    rect(w + d0 / 2, 0, d0 / 2, t, "SA_B", "permittivity10")
    rect(w + d0, 0, e.substrate_width_um - w - d0, t, "SA_A", "permittivity10")

    # PyAEDT maps a 4-value 2D pad list as [+X, +Y, -X, -Y] (checked in the saved
    # project): +X 30 %, +Y 100 % (vacuum above the metal), -X 0 (symmetry plane at
    # the strip centre x = 0), -Y 20 %.
    region = m.create_region(pad_value=[30, 100, 0, 20], pad_type="Percentage Offset", name="Region")
    bb = region.bounding_box
    if abs(bb[0]) > 1e-6 or bb[4] < 10.0:
        raise RuntimeError(f"Region placed wrongly (bbox {bb}): need x_min = 0 and vacuum above the metal")
    xs = [v.position[0] for v in region.vertices]
    left = [ed.id for ed in region.edges if all(abs(v.position[0] - min(xs)) < 1e-6 for v in ed.vertices)]
    right = [ed.id for ed in region.edges if all(abs(v.position[0] - max(xs)) < 1e-6 for v in ed.vertices)]
    m2d.assign_symmetry(left, "even_centre", is_odd=False)
    m2d.assign_symmetry(right, "odd_far_side", is_odd=True)
    m2d.assign_voltage([metal.name], amplitude=1, name="metal_1V")

    layers = [o for o in m.object_names if o.split("_")[0] in ("MS", "MA", "SA")]
    m2d.mesh.assign_length_mesh(layers, maximum_length=f"{e.mesh_max_length_nm}nm",
                                maximum_elements=None, name="layer_mesh")
    setup = m2d.create_setup("Setup1")
    setup.props["MaximumPasses"] = passes
    setup.props["PercentError"] = percent_error if percent_error is not None else e.percent_error
    setup.update()
    m2d.analyze_setup("Setup1", cores=cores)

    def E2(obj):
        fr = m2d.ofieldsreporter
        fr.CalcStack("clear")
        fr.CopyNamedExprToStack("Mag_E")
        fr.EnterScalar(2)
        fr.CalcOp("Pow")
        fr.EnterSurf(obj)
        fr.CalcOp("Integrate")
        f = Path(m2d.working_directory) / f"_e2_{obj}.fld"
        fr.CalculatorWrite(str(f), ["Solution:=", "Setup1 : LastAdaptive"], [])
        v = float(f.read_text().strip().splitlines()[-1])
        f.unlink()
        return v

    m2d.save_project()
    U = {o: E2(o) for o in ("MS_A", "MS_B", "MS_C", "MA_A", "MA_B", "MA_C", "MA_edge", "SA_A", "SA_B", "SA_C")}
    print("layer integrals of |E|^2:", json.dumps(U, indent=1))
    if min(U["MS_B"], U["MA_B"], U["SA_B"]) <= 0:
        raise RuntimeError("an interface layer has zero field: check the region / layers in the AEDT window")
    return {
        "F_MS": (U["MS_B"] + U["MS_C"]) / U["MS_B"],
        "F_MA": (U["MA_B"] + U["MA_C"] + U["MA_edge"]) / U["MA_B"],
        "F_SA": (U["SA_B"] + U["SA_C"]) / U["SA_B"],
        "layer_integrals": U,
    }


def run_edge2d(graphical=False, passes=30, percent_error=None, cores=4, out_dir: Path = ANSYS_RUNS):
    from ansys.aedt.core import Maxwell2d

    out_dir.mkdir(parents=True, exist_ok=True)
    project = out_dir / "transmon_edge2d.aedt"
    if project.exists():
        project.unlink()
    m2d = Maxwell2d(project=str(project), design="edge2d", solution_type="ElectrostaticXY",
                    version=AEDT_VERSION, non_graphical=not graphical, new_desktop=True,
                    close_on_exit=not graphical)
    try:
        t0 = time.time()
        F = build_and_solve_edge2d(m2d, EDGE_MODEL_2D, passes, percent_error, cores)
        m2d.save_project()
        print(f"2D edge model solved in {time.time() - t0:.0f} s")
        js = HERE / "data" / "ansys_edge2d_factors.json"
        js.parent.mkdir(exist_ok=True)
        js.write_text(json.dumps({"source": project.name, **F}, indent=2))
        print(json.dumps(F, indent=2))
        print(f"wrote {js}")
        return F
    finally:
        if not graphical:
            m2d.release_desktop(close_projects=True, close_desktop=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(design_name: str, solve: bool = True, extract_only: bool = False, graphical: bool = False,
        passes: int = 30, delta_s: float = 0.001, cores: int = 4, out_dir: Path = ANSYS_RUNS):
    from ansys.aedt.core import Hfss

    d = HFSS_DESIGNS[design_name]
    out_dir.mkdir(parents=True, exist_ok=True)
    project = out_dir / f"transmon_{d.name}.aedt"
    if not extract_only and project.exists():
        project.unlink()

    hfss = Hfss(project=str(project), design=d.name, solution_type="Modal", version=AEDT_VERSION,
                non_graphical=not graphical, new_desktop=True, close_on_exit=not graphical)
    try:
        if not extract_only:
            t0 = time.time()
            build_transmon(hfss, d)
            add_setup(hfss, d, passes, delta_s)
            hfss.save_project()
            print(f"built {project} in {time.time() - t0:.0f} s")
            if not solve:
                return None
            t0 = time.time()
            hfss.analyze_setup("Setup1", cores=cores)
            hfss.save_project()
            print(f"solved in {time.time() - t0:.0f} s")

        I = extract_integrals(hfss, d)
        js = HERE / "data" / f"ansys_{d.name}_field_integrals.json"
        js.parent.mkdir(exist_ok=True)
        js.write_text(json.dumps({
            "design": d.name,
            "source": f"{project.name} / Setup1 : LastAdaptive, {passes} passes max, dS {delta_s}",
            "integrals": I,
        }, indent=2))
        print(json.dumps(I, indent=2))
        print(f"wrote {js}")
        return I
    finally:
        if not graphical:
            hfss.release_desktop(close_projects=True, close_desktop=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build, solve and extract transmon designs in Ansys.")
    ap.add_argument("design", choices=sorted(HFSS_DESIGNS) + ["edge2d"],
                    help="HFSS design to build, or 'edge2d' for the Maxwell 2D edge model (F factors)")
    ap.add_argument("--no-solve", action="store_true", help="only build and save the project")
    ap.add_argument("--extract-only", action="store_true", help="open the solved project and extract")
    ap.add_argument("--non-graphical", action="store_true",
                    help="run AEDT without a window and close it at the end (default: window stays open)")
    ap.add_argument("--quick", action="store_true", help="8 passes, delta S 0.02 (fast check)")
    ap.add_argument("--passes", type=int, default=30)
    ap.add_argument("--delta-s", type=float, default=0.001)
    ap.add_argument("--cores", type=int, default=4)
    a = ap.parse_args()
    if a.quick:
        a.passes, a.delta_s = 8, 0.02
    if a.design == "edge2d":
        run_edge2d(graphical=not a.non_graphical, passes=a.passes,
                   percent_error=0.1 if a.quick else None, cores=a.cores)
    else:
        run(a.design, solve=not a.no_solve, extract_only=a.extract_only, graphical=not a.non_graphical,
            passes=a.passes, delta_s=a.delta_s, cores=a.cores)
