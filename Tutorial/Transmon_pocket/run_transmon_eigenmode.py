"""Isolated transmon: HFSS eigenmode -> pyEPR -> f_q and anharmonicity.

Follows the tutorial's sweep_runner.run_point(), cut down to one qubit with no
resonator and no feedline (so no 50 ohm terminations and no Q).

Must run on the machine with Ansys AEDT, with pywin32's post-install done once
(`python -m pywin32_postinstall -install`):

    conda activate hfss_env
    python run_transmon_eigenmode.py            # render only, Lj = 12 nH
    python run_transmon_eigenmode.py --solve    # render, then solve
    python run_transmon_eigenmode.py 10 --solve # any other Lj, in nH

Rendering and solving are separate on purpose: an eigenmode solve of a WRONG
layout still succeeds, and the only symptom is a participation ratio near zero
or a frequency outside the band -- which you find out after the adaptive passes
have already run. The default stops at a layout checkpoint; add --solve once it
looks right.

Writes output/transmon_eigen_Lj<..>nH.json.

QUANTUM METAL. The package formerly called Qiskit Metal; the import namespace
is still `qiskit_metal`, so nothing below changes. Verified against
quantum-metal 0.8.1: EPRanalysis imports and constructs, every key this script
writes into sim.setup exists (min_freq_ghz, n_modes, max_passes, min_passes,
max_delta_f, min_converged, pct_refinement, basis_order), `sim.run` is an alias
for `run_sim(name, components, open_terminations, port_list, box_plus_buffer)`,
and `renderer.epr_quantum_analysis` is still where the pyEPR object lives.

The env is `hfss_env`, NOT `qiskit-metal` -- no env of that name exists on this
machine (the envs are ads_env, hfss_env, metal, pymetal23, tim). hfss_env is
the one carrying quantum-metal 0.8.1, pyEPR and pywin32.
"""

import json
import logging
import os
import pathlib
import sys
import time
import warnings

import numpy as np

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)

from qiskit_metal import Dict                                 # noqa: E402
from transmon_pocket_design import build_design, DEFAULTS     # noqa: E402

HERE = pathlib.Path(__file__).parent

_ARGS = [a for a in sys.argv[1:] if not a.startswith("-")]
# Default is RENDER AND STOP, so the layout can be checked before a solve that
# takes twenty minutes to tell you it was wrong.
SOLVE = "--solve" in sys.argv[1:]
LJ_NH = float(_ARGS[0]) if _ARGS else 12.0
CJ_FF = 0.0      # pad capacitance is in the field solve; Cj = 0 like the tutorial

# Names the renderer gives the junction sheet and its line for component "Q1",
# junction "rect_jj" (verified in the tutorial; checked again after render).
JJ_RECT = "JJ_rect_Lj_Q1_rect_jj"
JJ_LINE = "JJ_Lj_Q1_rect_jj_"
SETUP_NAME = "eigen_setup"   # not "Setup": a stray default of that name gets
                             # picked up by pyEPR instead (tutorial gotcha 13)

# Where the .aedt project goes. Keep it off a nearly-full drive.
PROJECT_DIR = str(HERE / "hfss_projects")

SETUP = dict(
    # Must sit BELOW the expected f_q (gotcha 4), but not so far below that a
    # spurious low-frequency mode gets picked up instead. For Lj = 12 nH,
    # EJ = 13.6 GHz, and with C_sigma ~ 80 fF (EC ~ 0.24 GHz) the transmon
    # should land near sqrt(8*EJ*EC) - EC ~ 4.9 GHz, so 3 GHz is comfortably
    # under it with less room for junk than 2 GHz had.
    min_freq_ghz=3,
    n_modes=1,            # isolated transmon: one mode
    max_passes=20,
    min_passes=2,
    max_delta_f=0.1,      # percent
    min_converged=2,
    pct_refinement=30,
    basis_order=1,
)


def layout_checkpoint(eig, objs):
    """Everything worth eyeballing before committing to the solve.

    An eigenmode solve of a wrong layout still succeeds. The symptoms are
    indirect -- a participation ratio near zero, or a frequency nowhere near
    the band -- and they only appear after the adaptive passes have run. So
    check the things that can actually be wrong while it is still cheap.
    """
    renderer = eig.sim.renderer
    pinfo = renderer.pinfo
    des = pinfo.design
    print("\n" + "=" * 70)
    print("LAYOUT CHECKPOINT -- nothing has been solved")
    print("=" * 70)
    try:
        print(f"project / design : {pinfo.project_name} : {pinfo.design_name}")
        print(f"solution type    : {des.solution_type}")
    except Exception:
        pass

    print(f"\nobjects ({len(objs)}):")
    for o in sorted(objs):
        print(f"    {o}")

    # The junction is the whole experiment: it is a lumped RLC sheet across the
    # pad gap, not drawn metal. If it is missing or its Lj is wrong, the solve
    # is meaningless.
    print(f"\njunction:")
    print(f"    sheet {JJ_RECT!r}  {'FOUND' if JJ_RECT in objs else 'MISSING'}")
    print(f"    line  {JJ_LINE!r}  {'FOUND' if JJ_LINE in objs else 'MISSING'}")
    try:
        d = pinfo.design._design
        bm = d.GetModule("BoundarySetup")
        bnds = list(bm.GetBoundaries())
        print(f"    boundaries: {bnds}")
        ed = d.SetActiveEditor("3D Modeler")
        pts = [[float(c) for c in ed.GetVertexPosition(v)]
               for v in ed.GetVertexIDsFromObject(JJ_RECT)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        print(f"    sheet extent: x {min(xs):+.4f}..{max(xs):+.4f}  "
              f"y {min(ys):+.4f}..{max(ys):+.4f} mm  "
              f"({(max(xs)-min(xs))*1e3:.1f} x {(max(ys)-min(ys))*1e3:.1f} um)")
        print(f"    expected    : {DEFAULTS['inductor_width']} wide across the "
              f"{DEFAULTS['pad_gap']} pad gap")
    except Exception as exc:                            # noqa: BLE001
        print(f"    (could not read boundaries/extent: {exc})")

    print(f"\nvariables HFSS was given:")
    try:
        for k in ("Lj", "Cj"):
            print(f"    {k} = {des.get_variable_value(k)}")
    except Exception:
        print(f"    Lj = {LJ_NH} nH, Cj = {CJ_FF} fF (as set on the setup)")

    print(f"\neigenmode setup {SETUP_NAME!r}:")
    for k, v in SETUP.items():
        print(f"    {k:16} = {v}")

    print("\nWhat to check in AEDT:")
    print("  * two pad rectangles inside one ground pocket, no connection pads")
    print(f"  * the junction sheet bridging the {DEFAULTS['pad_gap']} gap, touching")
    print("    BOTH pads -- if it overlaps or misses, the junction is shorted or")
    print("    open and the participation ratio will come back near zero")
    print("  * a lumped RLC boundary on that sheet carrying Lj")
    print("  * the pads clear of the pocket wall (a pad touching ground shorts")
    print("    the island and the junction stops participating, with no error)")
    print("=" * 70)


def main():
    t0 = time.time()
    design, names = build_design()

    # An empty junction table fails silently: HFSS solves a passive structure
    # and p comes back ~1e-4 (gotcha 1).
    jj = design.qgeometry.tables["junction"]
    assert len(jj) > 0, "no junction geometry -- the renderer would make no lumped RLC"

    # Lj must go on the junction qgeometry. Setting only the `Lj` variable is
    # silently ignored: the renderer writes the table's literal (default 10nH)
    # into the lumped RLC (gotcha 32).
    # The HFSS renderer reads hfss_*; the aedt_* copies (SI floats) are set too
    # so no column is left disagreeing.
    for col, val in (("hfss_inductance", f"{LJ_NH}nH"),
                     ("hfss_capacitance", f"{CJ_FF}fF"),
                     ("q3d_inductance", f"{LJ_NH}nH"),
                     ("q3d_capacitance", f"{CJ_FF}fF"),
                     ("aedt_hfss_inductance", LJ_NH * 1e-9),
                     ("aedt_hfss_capacitance", CJ_FF * 1e-15)):
        if col in jj.columns:
            jj[col] = val
    assert str(LJ_NH) in str(jj["hfss_inductance"].iloc[0]), \
        f"junction Lj is {jj['hfss_inductance'].iloc[0]!r}, expected {LJ_NH} nH"
    print(f"Lj = {jj['hfss_inductance'].iloc[0]}, Cj = {CJ_FF} fF")

    from qiskit_metal.analyses.quantization import EPRanalysis
    eig = EPRanalysis(design, "hfss")
    s = eig.sim.setup
    s.name = SETUP_NAME
    for k, v in SETUP.items():
        s[k] = v
    s.vars = Dict(Lj=f"{LJ_NH} nH", Cj=f"{CJ_FF} fF")
    eig.setup.junctions = Dict(
        jj=Dict(Lj_variable="Lj", Cj_variable="Cj", rect=JJ_RECT, line=JJ_LINE))
    eig.setup.sweep_variable = "Lj"

    # ---- render --------------------------------------------------------------
    if not eig.sim.renderer_initialized:
        eig.sim._initialize_renderer()
    os.makedirs(PROJECT_DIR, exist_ok=True)
    try:
        eig.sim.renderer.pinfo.desktop.project_directory = PROJECT_DIR
    except Exception:                                   # noqa: BLE001
        print("could not set the AEDT project directory; using AEDT's default")

    # run_sim() is _render() followed by _analyze(). Calling them separately is
    # what lets the layout be inspected before committing to the solve -- which
    # matters because a wrong layout still solves happily and the first sign of
    # trouble is a participation ratio that makes no sense, twenty minutes in.
    print("rendering eigenmode design (not solving yet)...")
    eig.sim.clear_data()
    eig.sim._render(name="transmon_isolated", solution_type="eigenmode",
                    selection=[], open_pins=[], port_list=[],
                    jj_to_port=None, ignored_jjs=None, box_plus_buffer=True,
                    vars_to_initialize=eig.sim.setup.vars)

    objs = list(eig.sim.renderer.pinfo.get_all_object_names())
    missing = [n for n in (JJ_RECT, JJ_LINE) if n not in objs]
    if missing:
        raise RuntimeError(f"junction objects {missing} not in the HFSS model. "
                           f"JJ-like objects present: {[o for o in objs if 'JJ' in o]}")

    layout_checkpoint(eig, objs)
    if not SOLVE:
        print("\nStopping before the solve. Inspect the layout in AEDT, then:")
        print(f"    python {pathlib.Path(__file__).name} "
              f"{LJ_NH:g} --solve\n")
        return

    # ---- solve ---------------------------------------------------------------
    print("solving eigenmode...")
    eig.sim._analyze()

    ct = eig.sim.convergence_t
    n_pass = int(ct.index.max())
    dfreq = float(ct["Max Delta Freq. %"].dropna().iloc[-1])
    print(f"converged: {n_pass} passes, last delta f = {dfreq:.3f} %")

    # ---- EPR -----------------------------------------------------------------
    eig.run_epr()
    epra = eig.sim.renderer.epr_quantum_analysis   # lives on the renderer (gotcha 14)
    r = epra.results[list(epra.results)[-1]]

    f_lin = float(np.asarray(r["f_0"]).flatten()[0]) / 1e3        # GHz, linear (HFSS)
    f_01 = float(np.asarray(r["f_ND"]).flatten()[0]) / 1e3        # GHz, dressed
    alpha_nd = -abs(float(np.asarray(r["chi_ND"])[0, 0]))         # MHz
    alpha_o1 = -abs(float(np.asarray(r["chi_O1"])[0, 0]))         # MHz
    p = float(np.asarray(r["Pm_normed"]).flatten()[0])

    h, e = 6.62607015e-34, 1.602176634e-19
    phi0r = h / (2 * e) / (2 * np.pi)
    EJ = phi0r**2 / (LJ_NH * 1e-9) / h / 1e9                       # GHz
    EC = abs(alpha_nd) / 1e3                                       # GHz, transmon: EC ~ |alpha|
    alpha_formula = -(p**2) * (f_lin**2) / (8 * EJ) * 1e3          # MHz, first-order EPR

    warns = []
    if p < 0.5:
        warns.append(f"p = {p:.4f}: junction barely participating -- shorted "
                     "junction, pad touching the pocket, or empty junction table")
    if not 1.0 < f_lin < 20.0:
        warns.append(f"f = {f_lin:.3f} GHz outside 1-20 GHz: box mode, vacuum "
                     "substrate, or min_freq_ghz above the qubit")
    if n_pass >= SETUP["max_passes"]:
        warns.append("stopped at max_passes -- not the same as converged")

    result = dict(
        Lj_nH=LJ_NH, Cj_fF=CJ_FF, design=dict(DEFAULTS), setup=SETUP,
        n_passes=n_pass, max_delta_f_pct=dfreq,
        f_linear_GHz=f_lin, f01_GHz=f_01, p_junction=p,
        alpha_MHz=alpha_nd, alpha_O1_MHz=alpha_o1,
        alpha_formula_check_MHz=alpha_formula,
        EJ_GHz=EJ, EC_GHz=EC, EJ_over_EC=EJ / EC,
        warnings=warns, minutes=(time.time() - t0) / 60,
    )
    out = HERE / "output"
    out.mkdir(exist_ok=True)
    path = out / f"transmon_eigen_Lj{LJ_NH:g}nH.json"
    path.write_text(json.dumps({k: (str(v) if isinstance(v, Dict) else v)
                                for k, v in result.items()}, indent=2, default=str))

    print("\n" + "=" * 60)
    print(f" Lj = {LJ_NH:g} nH   (EJ = {EJ:.2f} GHz)")
    print(f" linear mode (HFSS)     f   = {f_lin:.4f} GHz")
    print(f" qubit 0->1 (pyEPR ND)  f01 = {f_01:.4f} GHz")
    print(f" anharmonicity          alpha = {alpha_nd:.1f} MHz   "
          f"(1st order {alpha_o1:.1f}, formula {alpha_formula:.1f})")
    print(f" junction participation p = {p:.4f}")
    print(f" EJ/EC = {EJ / EC:.1f}")
    for w in warns:
        print(" WARNING:", w)
    print(f" -> {path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
