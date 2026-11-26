"""
flipmon_ansys.py
================

Build the flipmon from ``flipmon_design.py`` in Ansys HFSS, solve it, and
extract the |E|^2 integrals used by ``flipmon_spr_calculation.py``.

Needs PyAEDT (ansys.aedt.core) and a local AEDT install. On this workstation:
the ``quantum-metal-env`` conda env and AEDT 2024 R1.

    conda activate quantum-metal-env
    python flipmon_ansys.py                         # default design: build + solve + extract
    python flipmon_ansys.py --quick                 # coarse convergence, for a fast check
    python flipmon_ansys.py --vacuum-gap 3 --chip-radius 150
    python flipmon_ansys.py --band 0.5              # Wang et al. region B (0.5-1 um), see below
    python flipmon_ansys.py --no-solve              # just build and look at it
    python flipmon_ansys.py --non-graphical         # batch mode, AEDT closes at the end
    python flipmon_ansys.py --extract-only          # re-extract from a solved project

Output
------
../../SPR_testing/flipmon/ansys_runs/flipmon_<params>.aedt   the generated project
data/ansys_field_integrals.json            read by flipmon_spr_calculation.py

What gets built (same as flipmon_spr - Copy.aedt / flipmon)
-----------------------------------------------------------
carrier and chip (450 x 450 x 200 um Si) with the vacuum gap between them; inner
and outer pads (perfect-E sheets) on the carrier at z = 0; chip pad on the
chip underside at z = vacuum_gap; a PEC bump joining inner pad and chip pad; a
50 ohm lumped port in the middle of cap_gap on the -x axis, with 0.2 um leads to
each pad. The Region has zero padding, so its outer faces (perfect E) sit on the
chip faces, as in the original project. Driven at 5 GHz (Modal solution type;
for a single port this gives the same fields as the original Terminal Network).

Sampling sheets (non-model, 1 nm from the surface), for every free metal edge:
    MA : vacuum side of the metal       MS : substrate side of the metal
    SA : vacuum side of the bare silicon
    *_interior  : more than 1 um from the edge
    *_perimeter : between ``band`` and 1 um from the edge

Two deliberate differences from the original project, both switchable:
* ``--band``: the original perimeter sheets cover 0-1 um from the edge
  (``--band 0``, the default, matches it). The 2D model that gives F defines
  region B as 0.5-1 um, which is ``--band 0.5``.
* The original SA sheets sit 1 nm *inside* the silicon, but the SA prefactor
  t/(2 eps_SA) assumes a vacuum-side normal field. Here SA is sampled on the
  vacuum side, and |E_z|^2 is extracted too, so the calculation can split the
  normal and tangential parts exactly.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from pathlib import Path

from flipmon_design import FLIPMON, INTERFACE_OFFSET_UM, FlipmonDesign

HERE = Path(__file__).resolve().parent
# Generated .aedt projects are kept outside the repository, in
# <SPR folder>/../SPR_testing/flipmon/ansys_runs/
ANSYS_RUNS = HERE.parent.parent / "SPR_testing" / "flipmon" / "ansys_runs"
AEDT_VERSION = "2024.1"
LEAD_WIDTH_UM = 0.2
PORT_LENGTH_UM = 1.0
INTERIOR_INSET_UM = 1.0
PAD_MESH_UM = 1.0              # length mesh on the bottom pads (original: 1 um)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _annulus(m, r_in, r_out, z, name, model=True):
    o = m.create_circle("XY", [0, 0, z], r_out, name=name)
    if r_in > 0:
        hole = m.create_circle("XY", [0, 0, z], r_in, name=name + "_hole")
        m.subtract(o, hole, keep_originals=False)
    o.model = model
    return o


def _annuli(m, ranges, z, name):
    """Non-model sheet made of several concentric annuli [(r_in, r_out), ...]."""
    parts = [_annulus(m, a, b, z, f"{name}_{i}", model=False) for i, (a, b) in enumerate(ranges)]
    if len(parts) > 1:
        m.unite(parts)
    parts[0].name = name
    parts[0].model = False
    return parts[0]


def _square_minus_circle(m, half, r, z, name, cut=None):
    o = m.create_rectangle("XY", [-half, -half, z], [2 * half, 2 * half], name=name)
    hole = m.create_circle("XY", [0, 0, z], r, name=name + "_hole")
    m.subtract(o, hole, keep_originals=False)
    if cut is not None:
        m.subtract(o, cut, keep_originals=False)
    o.model = False
    return o


def build_flipmon(hfss, d: FlipmonDesign, band: float = 0.0):
    m = hfss.modeler
    m.model_units = "um"
    g = d.vacuum_gap
    rb, (_, r1), (r2, r3), (_, R) = d.bump_radius, d.inner_pad, d.outer_pad, d.chip_pad
    hx, hy, t = d.carrier_size_um[0] / 2, d.carrier_size_um[1] / 2, d.carrier_size_um[2]

    m.create_box([-hx, -hy, 0], [2 * hx, 2 * hy, -t], name="carrier", material="silicon")
    m.create_box([-hx, -hy, g], [2 * hx, 2 * hy, d.chip_size_um[2]], name="chip", material="silicon")

    inner = _annulus(m, rb, r1, 0, "inner_pad")
    outer = _annulus(m, r2, r3, 0, "outer_pad")
    chip_pad = _annulus(m, rb, R, g, "chip_pad")
    m.create_cylinder("XY", [0, 0, 0], rb, g, name="bump", material="pec")

    # junction on the -x axis, in the middle of cap_gap
    xc = -(r1 + d.cap_gap / 2)
    xa, xb = xc + PORT_LENGTH_UM / 2, xc - PORT_LENGTH_UM / 2      # inner / outer end of the port
    w = LEAD_WIDTH_UM
    lead_in = m.create_rectangle("XY", [-(r1 - 1), -w / 2, 0], [xa + (r1 - 1), w], name="lead_inner")
    lead_out = m.create_rectangle("XY", [xb, -w / 2, 0], [-(r2 + 1) - xb, w], name="lead_outer")
    m.unite([inner, lead_in])
    m.unite([outer, lead_out])
    port = m.create_rectangle("XY", [xb, -w / 2, 0], [xa - xb, w], name="port_sheet")
    for o in (inner, outer, chip_pad):
        hfss.assign_perfecte_to_sheets([o.name], name=f"PerfE_{o.name}")
    # 1 um length mesh on the bottom pads, as in the original project (Length1)
    hfss.mesh.assign_length_mesh([inner.name, outer.name], inside_selection=False,
                                 maximum_length=f"{PAD_MESH_UM}um", maximum_elements=None,
                                 name="pad_length")
    hfss.lumped_port(port, create_port_sheet=False, integration_line=[[xa, 0, 0], [xb, 0, 0]],
                     impedance=50, name="junction_port")

    m.create_region(pad_value=0, pad_type="Percentage Offset", name="Region")

    # --- sampling sheets ----------------------------------------------------
    o, a = INTERFACE_OFFSET_UM, INTERIOR_INSET_UM
    bot_int = [(rb, r1 - a), (r2 + a, r3 - a)]
    bot_per = [(r1 - a, r1 - band), (r2 + band, r2 + a), (r3 - a, r3 - band)]
    top_int = [(rb, R - a)]
    top_per = [(R - a, R - band)]
    for iface, z in (("MA", +o), ("MS", -o)):
        _annuli(m, bot_int, z, f"bottom_{iface}_interior")
        _annuli(m, bot_per, z, f"bottom_{iface}_perimeter")
    for iface, z in (("MA", g - o), ("MS", g + o)):         # chip metal faces down
        _annuli(m, top_int, z, f"top_{iface}_interior")
        _annuli(m, top_per, z, f"top_{iface}_perimeter")

    # SA on the vacuum side; the junction leads are cut out of the bottom sheets
    def lead_cut(z, name):
        return m.create_rectangle("XY", [-(r2 + a), -(w / 2 + a), z], [r2 - r1 + 2 * a, w + 2 * a], name=name)

    sa_gap = _annuli(m, [(r1 + a, r2 - a)], +o, "bottom_SA_interior")
    m.subtract(sa_gap, lead_cut(+o, "cut1"), keep_originals=False)
    outside = _square_minus_circle(m, hx, r3 + a, +o, "bottom_SA_interior_outer")
    m.unite([sa_gap, outside])                 # keeps the name bottom_SA_interior
    sa_per = _annuli(m, [(r1 + band, r1 + a), (r2 - a, r2 - band), (r3 + band, r3 + a)], +o,
                     "bottom_SA_perimeter")
    m.subtract(sa_per, lead_cut(+o, "cut2"), keep_originals=False)
    _square_minus_circle(m, hx, R + a, g - o, "top_SA_interior")
    _annuli(m, [(R + band, R + a)], g - o, "top_SA_perimeter")


def add_setup(hfss, d: FlipmonDesign, passes: int, delta_s: float):
    setup = hfss.create_setup("Setup1", setup_type="HFSSDriven")
    setup.props["Frequency"] = f"{d.frequency_GHz}GHz"
    setup.props["MaximumPasses"] = passes
    setup.props["MaxDeltaS"] = delta_s
    setup.props["MinimumConvergedPasses"] = 2
    setup.update()


# ---------------------------------------------------------------------------
# Field extraction (Fields Calculator)
# ---------------------------------------------------------------------------

def integrate_E2(hfss, obj, kind, frequency_GHz, component=None, solution="Setup1 : LastAdaptive"):
    """Integral of |E|^2 (or |E_z|^2 with component='z') over a surface or volume."""
    fr = hfss.ofieldsreporter
    fr.CalcStack("clear")
    if component is None:
        fr.CopyNamedExprToStack("Mag_E")
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


def extract_integrals(hfss, d: FlipmonDesign) -> tuple[dict, dict]:
    f = d.frequency_GHz
    I = {
        "substrate_volume": integrate_E2(hfss, "carrier", "volume", f) + integrate_E2(hfss, "chip", "volume", f),
        "vacuum_volume": integrate_E2(hfss, "Region", "volume", f),
    }
    sa_perp = {}
    for side in ("bottom", "top"):
        for iface in ("MA", "MS", "SA"):
            for part in ("interior", "perimeter"):
                k = f"{side}_{iface}_{part}"
                I[k] = integrate_E2(hfss, k, "surface", f)
                if iface == "SA":
                    sa_perp[k] = integrate_E2(hfss, k, "surface", f, component="z")
    return I, sa_perp


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(d: FlipmonDesign = FLIPMON, band: float = 0.0, solve=True, extract_only=False,
        graphical=False, passes=30, delta_s=0.001, cores=4, out_dir: Path = ANSYS_RUNS):
    from ansys.aedt.core import Hfss

    tag = (f"cw{d.cap_width:g}_cg{d.cap_gap:g}_gap{d.vacuum_gap:g}_R{d.chip_radius:g}"
           f"_band{band:g}").replace(".", "p")
    out_dir.mkdir(parents=True, exist_ok=True)
    project = out_dir / f"flipmon_{tag}.aedt"
    if not extract_only and project.exists():
        project.unlink()

    hfss = Hfss(project=str(project), design="flipmon", solution_type="Modal", version=AEDT_VERSION,
                non_graphical=not graphical, new_desktop=True, close_on_exit=not graphical)
    try:
        if not extract_only:
            t0 = time.time()
            build_flipmon(hfss, d, band)
            add_setup(hfss, d, passes, delta_s)
            hfss.save_project()
            print(f"built {project} in {time.time() - t0:.0f} s")
            if not solve:
                return None
            t0 = time.time()
            hfss.analyze_setup("Setup1", cores=cores)
            hfss.save_project()
            print(f"solved in {time.time() - t0:.0f} s")

        I, sa_perp = extract_integrals(hfss, d)
        js = HERE / "data" / "ansys_field_integrals.json"
        js.write_text(json.dumps({
            "design": {"cap_width": d.cap_width, "cap_gap": d.cap_gap,
                       "vacuum_gap": d.vacuum_gap, "chip_radius": d.chip_radius, "band": band},
            "source": f"{project.name} / Setup1 : LastAdaptive, {passes} passes max, dS {delta_s}",
            "integrals": I,
            "SA_perp": sa_perp,
        }, indent=2))
        print(json.dumps(I, indent=2))
        print(f"wrote {js}")
        return I, sa_perp
    finally:
        if not graphical:
            hfss.release_desktop(close_projects=True, close_desktop=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build, solve and extract the flipmon in HFSS.")
    ap.add_argument("--cap-width", type=float, default=FLIPMON.cap_width)
    ap.add_argument("--cap-gap", type=float, default=FLIPMON.cap_gap)
    ap.add_argument("--vacuum-gap", type=float, default=FLIPMON.vacuum_gap)
    ap.add_argument("--chip-radius", type=float, default=FLIPMON.chip_radius)
    ap.add_argument("--band", type=float, default=0.0,
                    help="inner edge of the perimeter band in um (0 = original project, 0.5 = Wang region B)")
    ap.add_argument("--no-solve", action="store_true")
    ap.add_argument("--extract-only", action="store_true")
    ap.add_argument("--non-graphical", action="store_true",
                    help="run AEDT without a window and close it at the end (default: window stays open)")
    ap.add_argument("--quick", action="store_true", help="8 passes, delta S 0.02 (fast check)")
    ap.add_argument("--passes", type=int, default=30)
    ap.add_argument("--delta-s", type=float, default=0.001)
    ap.add_argument("--cores", type=int, default=4)
    a = ap.parse_args()
    if a.quick:
        a.passes, a.delta_s = 8, 0.02
    d = replace(FLIPMON, cap_width=a.cap_width, cap_gap=a.cap_gap, vacuum_gap=a.vacuum_gap,
                chip_radius=a.chip_radius)
    run(d, band=a.band, solve=not a.no_solve, extract_only=a.extract_only, graphical=not a.non_graphical,
        passes=a.passes, delta_s=a.delta_s, cores=a.cores)
