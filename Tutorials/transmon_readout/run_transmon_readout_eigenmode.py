"""Transmon + readout resonator: HFSS eigenmode (2 modes) -> pyEPR.

Follows the tutorial's sweep_runner.run_point(): same setup defaults, same
junction handling, modes identified by participation not frequency, same
derived quantities (alpha, chi, g, EJ/EC) and sanity checks. No feedline here,
so no 50 ohm terminations: the run is lossless (shunt_ohm = 0 in the
tutorial's terms) -- real eigensolver, no Q or kappa, but alpha and chi are
unaffected because they come from participation ratios, not from Q.

Must run on the machine with Ansys AEDT, with pywin32's post-install done once
(`python -m pywin32_postinstall -install`):

    conda activate hfss_env
    python run_transmon_readout_eigenmode.py            # render only, Lj = 10 nH
    python run_transmon_readout_eigenmode.py --solve    # render, then solve
    python run_transmon_readout_eigenmode.py 9.5 --solve

Writes output/transmon_readout_Lj<..>nH.json.

The env is `hfss_env`, NOT `qiskit-metal` -- no env of that name exists on this
machine (they are ads_env, hfss_env, metal, pymetal23, tim). hfss_env carries
quantum-metal 0.8.1 (the package formerly called Qiskit Metal; the import
namespace is still `qiskit_metal`), pyEPR and pywin32.

Rendering and solving are SEPARATE. A two-mode eigenmode solve of a wrong
layout still succeeds, and the symptoms are indirect -- a participation ratio
that identifies the wrong mode as the qubit, or a resonator frequency nowhere
near the design -- which only appear after the adaptive passes have run. The
default stops at a layout checkpoint; add --solve once it looks right.
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

from qiskit_metal import Dict                                  # noqa: E402
from transmon_readout_design import build_design, DEFAULTS     # noqa: E402

HERE = pathlib.Path(__file__).parent

_ARGS = [a for a in sys.argv[1:] if not a.startswith("-")]
SOLVE = "--solve" in sys.argv[1:]
LJ_NH = float(_ARGS[0]) if _ARGS else 10.0
CJ_FF = 0.0      # pad capacitance is in the field solve; Cj = 0 like the tutorial

# Renderer names for component "Q1", junction "rect_jj" (tutorial-verified;
# checked again after render).
JJ_RECT = "JJ_rect_Lj_Q1_rect_jj"
JJ_LINE = "JJ_Lj_Q1_rect_jj_"
SETUP_NAME = "eigen_setup"   # not "Setup" (tutorial gotcha 13)

# Where the .aedt project goes. Keep it off a nearly-full drive (gotcha 18).
PROJECT_DIR = str(HERE / "hfss_projects")

# The tutorial's DEFAULT_SETUP, unchanged.
SETUP = dict(
    min_freq_ghz=3,       # must sit below the expected f_q (gotcha 4)
    n_modes=2,            # qubit + resonator; do not over-request (gotcha 5)
    max_passes=30,
    min_passes=2,
    max_delta_f=0.1,      # percent
    min_converged=2,
    pct_refinement=30,
    basis_order=1,
)


def layout_checkpoint(eig, objs, design):
    """Everything worth eyeballing before a two-mode eigenmode solve.

    More can go wrong here than in the isolated-qubit case: there are now two
    modes, and the analysis decides which is the QUBIT by participation ratio,
    not by frequency. If the resonator is mis-built the modes can swap, or the
    second mode can be a box mode rather than the resonator at all -- and
    either way the solve succeeds and reports a confident chi.
    """
    pinfo = eig.sim.renderer.pinfo
    des = pinfo.design
    print("\n" + "=" * 72)
    print("LAYOUT CHECKPOINT -- nothing has been solved")
    print("=" * 72)
    try:
        print(f"project / design : {pinfo.project_name} : {pinfo.design_name}")
        print(f"solution type    : {des.solution_type}")
    except Exception:                                   # noqa: BLE001
        pass

    print(f"\nobjects ({len(objs)}):")
    for o in sorted(objs):
        print(f"    {o}")

    print("\njunction (the qubit mode lives here):")
    print(f"    sheet {JJ_RECT!r}  {'FOUND' if JJ_RECT in objs else 'MISSING'}")
    print(f"    line  {JJ_LINE!r}  {'FOUND' if JJ_LINE in objs else 'MISSING'}")
    try:
        d = des._design
        bm = d.GetModule("BoundarySetup")
        print(f"    boundaries: {list(bm.GetBoundaries())}")
        ed = d.SetActiveEditor("3D Modeler")
        pts = [[float(c) for c in ed.GetVertexPosition(v)]
               for v in ed.GetVertexIDsFromObject(JJ_RECT)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        print(f"    sheet {(max(xs)-min(xs))*1e3:.1f} x "
              f"{(max(ys)-min(ys))*1e3:.1f} um across the "
              f"{DEFAULTS['pad_gap']} pad gap")
    except Exception as exc:                            # noqa: BLE001
        print(f"    (could not read boundaries/extent: {exc})")

    # The resonator is the other half of the experiment and has no lumped
    # element to check, so verify its LENGTH -- that is what sets f_r.
    print("\nreadout resonator:")
    try:
        import numpy as _np
        pts = _np.asarray(design.components["readout_res"].get_points(),
                          dtype=float)
        seg = _np.linalg.norm(_np.diff(pts, axis=0), axis=1)
        print(f"    requested total_length = {DEFAULTS['res_total_length']}")
        print(f"    drawn polyline         = {seg.sum():.4f} mm "
              f"({len(pts)} vertices; fillets shorten this to the requested "
              f"length)")
        print(f"    shortest segment       = {seg.min()*1e3:.1f} um "
              f"(fillet {DEFAULTS['res_fillet']})")
        pin = design.components["Q1"].pins["readout"]
        print(f"    coupling-pad pin at    = ({pin.middle[0]:+.4f}, "
              f"{pin.middle[1]:+.4f}) mm")
    except Exception as exc:                            # noqa: BLE001
        print(f"    (could not measure: {exc})")

    print(f"\neigenmode setup {SETUP_NAME!r}:")
    for k, v in SETUP.items():
        print(f"    {k:16} = {v}")
    if SETUP["max_delta_f"] >= 1.0:
        print("    NOTE: max_delta_f = 1 % is ~50 MHz per pass at 5 GHz. The")
        print("          isolated-qubit run used 0.1 % and converged in 15-16")
        print("          passes. chi comes from participation ratios so it is")
        print("          less sensitive than f, but consider tightening.")

    print("\nWhat to check in AEDT:")
    print("  * the qubit pocket with ONE corner pad (top right) feeding a lead")
    print("    out of the pocket into the meander")
    print("  * the meander running to an open end, not touching ground or")
    print("    itself, and clear of the chip edge")
    print("  * the junction sheet bridging the pad gap, touching both pads")
    print("  * a lumped RLC on that sheet carrying Lj")
    print("=" * 72)


def main():
    t0 = time.time()
    design, names = build_design()

    # An empty junction table fails silently (gotcha 1).
    jj = design.qgeometry.tables["junction"]
    assert len(jj) > 0, "no junction geometry -- the renderer would make no lumped RLC"

    # Lj must go on the junction qgeometry; the `Lj` variable alone is ignored
    # by the renderer, which writes the table's literal into the RLC (gotcha 32).
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

    # ---- render + solve ------------------------------------------------------
    if not eig.sim.renderer_initialized:
        eig.sim._initialize_renderer()
    os.makedirs(PROJECT_DIR, exist_ok=True)
    try:
        eig.sim.renderer.pinfo.desktop.project_directory = PROJECT_DIR
    except Exception:                                   # noqa: BLE001
        print("could not set the AEDT project directory; using AEDT's default")

    # The resonator's far end is an OpenToGround component, so no open_pins
    # endcap is needed; port_list=[] because there is no feedline (gotcha 16).
    #
    # run_sim() is _render() then _analyze(). Splitting them is what lets the
    # layout be inspected first -- see the checkpoint note in the module
    # docstring.
    print("rendering eigenmode design (2 modes, not solving yet)...")
    eig.sim.clear_data()
    eig.sim._render(name="transmon_readout", solution_type="eigenmode",
                    selection=[], open_pins=[], port_list=[],
                    jj_to_port=None, ignored_jjs=None, box_plus_buffer=True,
                    vars_to_initialize=eig.sim.setup.vars)

    objs = list(eig.sim.renderer.pinfo.get_all_object_names())
    missing = [n for n in (JJ_RECT, JJ_LINE) if n not in objs]
    if missing:
        raise RuntimeError(f"junction objects {missing} not in the HFSS model. "
                           f"JJ-like objects present: {[o for o in objs if 'JJ' in o]}")

    # Create the eigenmode setup NOW, not at _analyze() time. Otherwise the
    # only setup in AEDT at the checkpoint is pyEPR's auto-created default
    # ('Setup', 1 GHz, 1 mode) and the checkpoint would describe a setup that
    # does not yet exist -- which is exactly what it did.
    eig.sim.sim_setup_name = eig.sim.renderer.initialize_eigenmode(
        **eig.sim.setup)
    print(f"created eigenmode setup {eig.sim.sim_setup_name!r} in AEDT")

    layout_checkpoint(eig, objs, design)
    if not SOLVE:
        print("\nStopping before the solve. Inspect the layout in AEDT, then:")
        print(f"    python {pathlib.Path(__file__).name} {LJ_NH:g} --solve\n")
        return

    print("solving eigenmode (2 modes)...")
    eig.sim._analyze()

    ct = eig.sim.convergence_t
    n_pass = int(ct.index.max())
    dfreq = float(ct["Max Delta Freq. %"].dropna().iloc[-1])
    print(f"converged: {n_pass} passes, last delta f = {dfreq:.3f} %")

    # ---- EPR -----------------------------------------------------------------
    eig.run_epr()
    epra = eig.sim.renderer.epr_quantum_analysis   # on the renderer (gotcha 14)
    r = epra.results[list(epra.results)[-1]]

    f_lin = np.asarray(r["f_0"], dtype=float).flatten() / 1e3     # GHz, HFSS linear
    f_nd = np.asarray(r["f_ND"], dtype=float).flatten() / 1e3     # GHz, dressed
    chi_nd = np.asarray(r["chi_ND"], dtype=float)                  # MHz
    chi_o1 = np.asarray(r["chi_O1"], dtype=float)                  # MHz
    P = np.asarray(r["Pm_normed"], dtype=float).flatten()

    # "p identifies the modes, not frequency" (tutorial README): the qubit is
    # the mode whose energy sits in the junction.
    q = int(np.argmax(P))
    rr = 1 - q
    f_q, f_r = float(f_nd[q]), float(f_nd[rr])
    alpha = -abs(float(chi_nd[q, q]))
    alpha_o1 = -abs(float(chi_o1[q, q]))
    chi = float(chi_nd[q, rr])
    chi_rr = float(chi_nd[rr, rr])

    h, e = 6.62607015e-34, 1.602176634e-19
    phi0r = h / (2 * e) / (2 * np.pi)
    EJ = phi0r**2 / (LJ_NH * 1e-9) / h / 1e9                       # GHz
    EC = abs(alpha) / 1e3                                          # GHz
    delta = (f_q - f_r) * 1e3                                      # MHz
    # g back-derived from the dispersive shift:
    #
    #     chi_qr = 2 alpha |g|^2 / (Delta (Delta + alpha))
    #  => |g|^2  = chi_qr Delta (Delta + alpha) / (2 alpha)
    #
    # The factor of 2 was missing before (the expression was
    # chi alpha / (Delta(Delta+alpha)) inverted without it), which inflated g
    # by sqrt(2) ~ 41%.
    g = float(np.sqrt(abs(chi * delta * (delta + alpha) / (2.0 * alpha))))  # MHz

    warns = []
    if P[q] < 0.5:
        warns.append(f"p_q = {P[q]:.4f}: junction barely participating -- shorted "
                     "junction, pad touching the pocket, or empty junction table")
    if not 1.0 < f_q < 20.0:
        warns.append(f"f_q = {f_q:.3f} GHz outside 1-20 GHz: box mode, vacuum "
                     "substrate, or min_freq_ghz above the qubit")
    if n_pass >= SETUP["max_passes"] and dfreq > SETUP["max_delta_f"]:
        warns.append("stopped at max_passes -- ran out of budget, which is not "
                     "the same thing as converging")

    result = dict(
        Lj_nH=LJ_NH, Cj_fF=CJ_FF, design=dict(DEFAULTS), setup=SETUP,
        n_passes=n_pass, max_delta_f_pct=dfreq,
        qubit=dict(f01_GHz=f_q, f_linear_GHz=float(f_lin[q]), p=float(P[q]),
                   alpha_MHz=alpha, alpha_O1_MHz=alpha_o1),
        resonator=dict(f_GHz=f_r, f_linear_GHz=float(f_lin[rr]), p=float(P[rr]),
                       self_kerr_MHz=chi_rr),
        chi_MHz=chi, delta_GHz=delta / 1e3, g_MHz=g,
        EJ_GHz=EJ, EC_GHz=EC, EJ_over_EC=EJ / EC,
        warnings=warns, minutes=(time.time() - t0) / 60,
    )
    out = HERE / "output"
    out.mkdir(exist_ok=True)
    path = out / f"transmon_readout_Lj{LJ_NH:g}nH.json"
    path.write_text(json.dumps(result, indent=2, default=str))

    print("\n" + "=" * 64)
    print(f" Lj = {LJ_NH:g} nH   (EJ = {EJ:.2f} GHz)")
    print(f" qubit      f01 = {f_q:.4f} GHz   (linear {f_lin[q]:.4f})   p = {P[q]:.4f}")
    print(f"            alpha = {alpha:.1f} MHz   (1st order {alpha_o1:.1f})   "
          f"EJ/EC = {EJ / EC:.1f}")
    print(f" resonator  f_r = {f_r:.4f} GHz   (linear {f_lin[rr]:.4f})   p = {P[rr]:.2e}")
    print(f" coupling   Delta = {delta / 1e3:.3f} GHz   chi = {chi:.3f} MHz   g = {g:.1f} MHz")
    for w in warns:
        print(" WARNING:", w)
    print(f" -> {path}")
    print("=" * 64)


if __name__ == "__main__":
    main()
