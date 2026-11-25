"""One sweep point: build -> render -> HFSS eigenmode -> pyEPR -> one JSON record.

Port of kb2/mergemon/run_mergemon_eigenmode.py into a reusable `run_point()`,
following the hfss-transmon-sweep-tutorial's sweep_runner.run_point(). Same
figures of merit (f01, f_r, alpha, chi, g, Delta, p per junction, EJ/EC) plus:

  * a pyEPR Cj correction. pyEPR (0.8.5.7 and still 1.0.1) ignores
    Cj_variable and books every junction's capacitor energy at 2 fF
    (core_distributed_analysis.py, get_junctions_L_and_C). HFSS solves with the
    true Cj (it is in the RLC sheet), so the frequencies are right, but
    participations, alpha and chi are normalised against too little energy.
    The record keeps pyEPR's raw FoMs (suffix `_raw`) AND ones recomputed from
    pyEPR's stored per-mode energies with the true Cj (no suffix) -- use those.
  * a junction model: "split" (L-only + C-only sheet per junction, as in the
    old solid-Si run) or "single" (one L+C sheet), see JJ_MODEL.
  * an `soi` knob (default None = solid silicon). Not implemented yet: it will
    draw the device-Si / gap / handle stack after render.

Must run on the AEDT machine, in the qiskit-metal env (pywin32 post-install
done once). Copy the whole frontend folder there.

    from simulate import run_point
    rec = run_point(dict(t_length="40um"), lj_nH=36, cj_fF=45)
"""

from __future__ import annotations

import logging
import os
import time
import warnings

import numpy as np

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)

import store as st  # noqa: E402
from assembly import all_knobs, build_design  # noqa: E402
from parts.catalog import DEFAULT_PARTS, OPTIONAL_ROLES, full_choice  # noqa: E402,F401
from qiskit_metal import Dict  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(HERE, "output")         # keep off a nearly-full drive (gotcha 18)
PYEPR_DIR = os.path.join(HERE, "data-pyEPR")        # pyEPR's own pickles (default C:\data-pyEPR)
SETUP_NAME = "eigen_setup"                          # not "Setup" (gotcha 13)
JJ_COMPONENT = "bottom_squid"

# Junction model, as in the old solid-Si run (Mergemon/output/
# mergemon_Lj36nH_Cj45fF.json, project Mergemon/Project1.aedt):
#   "split"   each 1 um gap holds TWO lumped sheets side by side, an L-only
#             sheet and a C-only sheet, LC_SHEET_GAP_UM apart (L || C);
#   "single"  ONE sheet carrying both L and C (the renderer's own RLC sheet).
#             DO NOT USE: its parallel-LC pole 1/(2 pi sqrt(Lj Cj)) shows up as
#             a spurious eigenmode that displaces the resonator (36 nH / 45 fF:
#             a "mode" at 3.95 GHz instead of f_r 4.45 GHz, virial -154%).
#             Kept only to reproduce that; see checks/recreate_old_si.py.
# Sheets are meshed at JJ_MESH (renderer default 7um; the old run used 100nm).
JJ_MODEL = "split"
JJ_MESH = "100nm"
LC_SHEET_GAP_UM = 0.2

# pyEPR integrates E along a line across each junction gap to get its voltage.
# We draw that line ourselves, through the middle of the L-carrying sheet,
# running this far INTO the metal at both ends (E = 0 inside PEC, so it adds
# nothing). The old solid-Si run used 0 (line ends on the metal edges). The
# junction qgeometry itself is not lengthened: its RLC sheet would overlap metal
# and be shorted (gotcha 16).
LINE_EXTEND_UM = 0.0

# Feedline / flux line (optional parts): each ends in an open gap that a 50 ohm
# lumped sheet fills, abutting the centre conductor and the ground -- a SHUNT
# termination (transmon tutorial gotcha 16: qiskit-metal's port_list makes a
# shorted series element instead). It is the model's only loss, so the solve
# must be the COMPLEX eigenproblem (gotcha 15) and kappa = f_r / Q_r.
SHUNT_OHM = 50.0

# The tutorial's DEFAULT_SETUP, except min_freq (must sit below f_q; gotcha 4).
DEFAULT_SETUP = dict(
    min_freq_ghz=1,
    n_modes=2,            # qubit + LC resonator (gotcha 5)
    max_passes=16,
    min_passes=2,
    max_delta_f=1.0,      # percent
    min_converged=2,
    pct_refinement=30,
    basis_order=1,
)
# With a line (50 ohm end, complex solve) the 1 % target is driven by the qubit's
# noise-level Im f and never converges: 16 passes ran 6 h / 172 GB and crashed.
# A fixed 10 passes instead (tutorial README gotcha 21); judge Q_r by its trend.
LINE_SETUP = dict(min_passes=10, max_passes=10)


def setup_for(parts=None, setup=None):
    """The full eigenmode setup of a point: DEFAULT_SETUP, then LINE_SETUP if
    the design has a feedline or flux line, then the caller's overrides."""
    lines = any(r in full_choice(parts) for r in OPTIONAL_ROLES)
    return {**DEFAULT_SETUP, **(LINE_SETUP if lines else {}), **(setup or {})}

H, E_CHARGE = 6.62607015e-34, 1.602176634e-19
PHI0_RED = H / (2 * E_CHARGE) / (2 * np.pi)


def ej_ghz(l_henry):
    return PHI0_RED**2 / l_henry / H / 1e9


# ---------------------------------------------------------------------------
# FoMs from participations
# ---------------------------------------------------------------------------
def foms(f_lin, f_nd, chi_nd, chi_o1, P, n_junctions, lj_nH):
    """Qubit/resonator FoMs. Modes are identified by participation, not
    frequency ("p identifies the modes", tutorial README)."""
    p_mode = P.sum(axis=1)
    q = int(np.argmax(p_mode))
    r = 1 - q
    f_q, f_r = float(f_nd[q]), float(f_nd[r])
    alpha = -abs(float(chi_nd[q, q]))
    chi = float(chi_nd[q, r])                       # full cross-Kerr (resonator shift)
    EJ = n_junctions * ej_ghz(lj_nH * 1e-9)         # parallel junctions, zero flux
    EC = abs(alpha) / 1e3
    delta = (f_q - f_r) * 1e3                       # MHz
    # Lab formula chi_qr = 2 alpha g^2 / (Delta (Delta + alpha)), chi = full
    # shift. Textbook approximation: g is rough.
    g = float(np.sqrt(abs(chi * delta * (delta + alpha) / (2 * alpha)))) if alpha else None
    return dict(
        f01_GHz=f_q, f01_linear_GHz=float(f_lin[q]),
        f_r_GHz=f_r, f_r_linear_GHz=float(f_lin[r]),
        alpha_MHz=alpha, alpha_O1_MHz=-abs(float(chi_o1[q, q])),
        chi_MHz=chi, chi_rr_MHz=float(chi_nd[r, r]),
        g_MHz=g, delta_GHz=delta / 1e3,
        p_q_total=float(p_mode[q]), p_r_total=float(p_mode[r]),
        **{f"p_q_{i}": float(v) for i, v in enumerate(P[q])},
        EJ_total_GHz=EJ, EC_GHz=EC, EJ_over_EC=EJ / EC if EC else None,
        qubit_mode=q,
    )


def cj_corrected(epra, variation, cj_true_F, cos_trunc=8, fock_trunc=7):
    """Re-do pyEPR's participations, alpha and chi with the TRUE junction Cj.

    pyEPR stores, per mode, the field energies U_E, U_H and each junction's
    U_J_ind = Lj I^2/2 and U_J_cap = Cj_used V^2/2 (Cj_used = 2 fF). Scaling
    U_J_cap by Cj_true/Cj_used gives the true capacitor energy; the rest is
    pyEPR's own recipe (renormalisation, epr_to_zpf, numerical diagonalisation).

    Returns (f_lin, f_nd, chi_nd, chi_o1, P, virial_pct_per_mode).
    """
    from pyEPR import config
    from pyEPR.calcs.back_box_numeric import epr_numerical_diagonalization
    from pyEPR.calcs.basic import CalcsBasic

    energies = epra.ansys_energies[variation]
    modes = sorted(energies)
    cj_used = epra.Cjs[variation]
    ljs = epra.Ljs[variation]
    jn = list(cj_used.index)

    P = np.zeros((len(modes), len(jn)))
    virial = []
    for i, m in enumerate(modes):
        e = energies[m]
        u_ind = np.array([e["U_J_inds"][j] for j in jn], dtype=float)
        u_cap = np.array([e["U_J_caps"][j] * cj_true_F / float(cj_used[j]) for j in jn],
                         dtype=float)
        u_tot_cap = float(e["U_E"]) + u_cap.sum()
        u_tot_ind = float(e["U_H"]) + u_ind.sum()
        u_mode = (u_tot_cap + u_tot_ind) / 2
        p = u_ind / u_tot_cap
        # pyEPR's QuantumAnalysis renormalisation: 1/True rescales every p_mj
        # to the global inductive sum; 2 only those above 0.15.
        mode = config.epr.renorm_pj
        if mode:
            scale = abs((u_mode - float(e["U_H"])) / u_mode) / p.sum()
            mask = p > 0.15 if mode == 2 else np.ones_like(p, dtype=bool)
            p = np.where(mask, p * scale, p)
        P[i] = np.abs(p)
        virial.append(100 * (u_tot_cap - u_tot_ind) / u_mode)

    SJ = np.array(epra.SM[variation][[f"s_{j}" for j in jn]], dtype=float)
    f_lin = np.asarray(epra.freqs_hfss[variation], dtype=float)[modes]      # GHz
    Om = np.diagflat(f_lin)
    EJ = np.diagflat([ej_ghz(float(ljs[j])) for j in jn])
    chi_o1 = 0.25 * Om @ P @ np.linalg.inv(EJ) @ P.T @ Om * 1000.0         # MHz
    np.fill_diagonal(chi_o1, np.diag(chi_o1) / 2)                          # diag = alpha
    phi_zpf = CalcsBasic.epr_to_zpf(P, SJ, Om, EJ)
    if not hasattr(np, "float"):   # pyEPR 0.8.5.7 still calls np.float (removed in numpy 1.24)
        np.float = float
    f_nd, chi_nd = epr_numerical_diagonalization(
        f_lin, np.array([float(ljs[j]) for j in jn]), phi_zpf,
        cos_trunc=cos_trunc, fock_trunc=fock_trunc)
    # epr_numerical_diagonalization returns f in Hz (pyEPR's own results store
    # f_ND = f * 1e-6, MHz) and chi already in MHz.
    return f_lin, np.asarray(f_nd, float) * 1e-9, np.asarray(chi_nd, float), chi_o1, P, virial


# ---------------------------------------------------------------------------
# Rendering patches
# ---------------------------------------------------------------------------
def _draw_junctions(renderer, jj_model, line_extend_um):
    """Replace the renderer's junction drawing (one RLC sheet + its line) with
    `jj_model` sheets ("split": L-only + C-only side by side; "single": one
    L+C sheet) and pyEPR's voltage line `JJ_int_<junction>` through the middle
    of the L sheet. The L sheet keeps the renderer's name, JJ_rect_Lj_<comp>_<jj>;
    the C sheet is JJ_rect_Cj_<comp>_<jj>. Patches this renderer instance only.
    """
    from pyEPR.ansys import parse_units
    from qiskit_metal.renderers.renderer_ansys.ansys_renderer import get_clean_name

    def sheet(box, z, axis, name, rlc_name, **rlc):
        x0, x1, y0, y1 = box
        s = renderer.modeler.draw_rect_corner([x0, y0, z], x1 - x0, y1 - y0, 0,
                                              transparency=0.0)
        s.make_rlc_boundary(axis, name=rlc_name, **rlc)
        renderer.modeler.rename_obj(s, name)
        renderer.assign_mesh.append(name)

    def render_junction_inductor(qgeom, xmin, xmax, ymin, ymax, z, axis):
        jj = get_clean_name(qgeom["name"])
        name = f"{renderer.design._components[qgeom['component']].name}_{jj}"
        L = dict(l=qgeom["hfss_inductance"], r=qgeom["hfss_resistance"])
        C = dict(c=qgeom["hfss_capacitance"])
        lo, hi = (xmin, xmax) if axis == "y" else (ymin, ymax)    # across the current
        if jj_model == "single":
            sheets = [((lo, hi), f"JJ_rect_Lj_{name}", f"Lj_Lj_{name}", dict(L, **C))]
        elif jj_model == "split":
            g, mid = parse_units(f"{LC_SHEET_GAP_UM}um"), (lo + hi) / 2
            sheets = [((lo, mid - g / 2), f"JJ_rect_Lj_{name}", f"Lj_Lj_{name}", L),
                      ((mid + g / 2, hi), f"JJ_rect_Cj_{name}", f"Cj_{name}", C)]
        else:
            raise ValueError(f"jj_model must be 'split' or 'single', not {jj_model!r}")
        for (a, b), sname, rname, rlc in sheets:
            sheet((a, b, ymin, ymax) if axis == "y" else (xmin, xmax, a, b),
                  z, axis, sname, rname, **rlc)
        c = sum(sheets[0][0]) / 2
        e = parse_units(f"{line_extend_um}um")
        pts = ([[c, ymin - e, z], [c, ymax + e, z]] if axis == "y"
               else [[xmin - e, c, z], [xmax + e, c, z]])
        line = renderer.modeler.draw_polyline(pts, closed=False, color=(128, 0, 128))
        line.rename(f"JJ_int_{jj}")

    renderer.render_junction_inductor = render_junction_inductor


def _use_project(renderer, path):
    """Make the AEDT project at `path` the active one -- already open, opened
    from disk, or created and saved there -- and point the renderer at it, so
    no other open project is drawn into."""
    from pyEPR.ansys import HfssApp

    desktop = HfssApp().get_app_desktop()
    name = os.path.splitext(os.path.basename(path))[0]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if name in desktop.get_project_names():
        desktop.set_active_project(name)
    elif os.path.exists(path):
        desktop.open_project(path)
    else:
        desktop.new_project().save(path)
    # by name: AEDT's active project can be another one, e.g. a sweep preview the
    # user just opened from the front end
    renderer.connect_ansys(project_path=os.path.dirname(path), project_name=name)
    assert renderer.pinfo.project_name == name, \
        f"renderer is on project {renderer.pinfo.project_name!r}, expected {name!r}"


def _add_terminations(renderer, terms, r_ohm=SHUNT_OHM):
    """A 50 ohm lumped sheet over each line's open end (names.terminations:
    (name, (x0, y0, x1, y1) mm, axis)); the RLC line runs across the gap."""
    modeler = renderer.modeler
    units = modeler.get_units()
    scale = {"mm": 1.0, "um": 1000.0, "meter": 1e-3, "m": 1e-3}.get(units, 1.0)
    u = lambda v: f"{v * scale}{units}"              # noqa: E731  unit-tagged (pyEPR: metres)
    made = []
    for name, (x0, y0, x1, y1), axis in terms:
        sheet = f"Term_{name}"
        modeler._modeler.CreateRectangle(
            ["NAME:RectangleParameters", "IsCovered:=", True,
             "XStart:=", u(x0), "YStart:=", u(y0), "ZStart:=", u(0.0),
             "Width:=", u(x1 - x0), "Height:=", u(y1 - y0), "WhichAxis:=", "Z"],
            modeler._attributes_array(name=sheet))
        if axis == "x":
            start, end = [u(x0), u((y0 + y1) / 2), u(0.0)], [u(x1), u((y0 + y1) / 2), u(0.0)]
        else:
            start, end = [u((x0 + x1) / 2), u(y0), u(0.0)], [u((x0 + x1) / 2), u(y1), u(0.0)]
        modeler._make_lumped_rlc(r=f"{r_ohm}ohm", l=0, c=0, start=start, end=end,
                                 obj_arr=["Objects:=", [sheet]], name=f"RLC_{sheet}")
        made.append(sheet)
    return made


def _force_complex_freq(renderer, setup_name, s):
    """Rewrite the WHOLE setup with ConvergeOnRealFreq False. pyEPR hardcodes
    True; patching one field leaves HFSS on its real solver (Im f = 0, Q = inf)
    -- transmon tutorial gotcha 15. Only for designs with 50 ohm ends."""
    renderer.pinfo.design._setup_module.EditSetup(setup_name, [
        "NAME:" + setup_name,
        "MinimumFrequency:=", f"{s['min_freq_ghz']}GHz",
        "NumModes:=", int(s["n_modes"]),
        "MaxDeltaFreq:=", float(s["max_delta_f"]),
        "ConvergeOnRealFreq:=", False,
        "MaximumPasses:=", int(s["max_passes"]),
        "MinimumPasses:=", int(s["min_passes"]),
        "MinimumConvergedPasses:=", int(s["min_converged"]),
        "PercentRefinement:=", int(s["pct_refinement"]),
        "IsEnabled:=", True,
        "BasisOrder:=", int(s["basis_order"]),
    ])


def _drop_stray_setups(renderer, keep):
    """Delete any setup but ours (a default "Setup" can appear; pyEPR may take
    the empty one and return no variations -- tutorial gotcha 13)."""
    stray = [n for n in renderer.pinfo.design.get_setup_names() if n != keep]
    if stray:
        renderer.pinfo.design._setup_module.DeleteSetups(stray)
    return stray


def _quality_factors(renderer):
    """HFSS's (f GHz, Q) per mode for the solved variation, sorted by f."""
    import pyEPR as epr
    da = getattr(renderer, "epr_distributed_analysis", None) or epr.DistributedAnalysis(
        renderer.pinfo)
    df = da.get_ansys_frequencies_all()
    f = np.asarray(df["Freq. (GHz)"].values, float)
    q = np.asarray(df["Quality Factor"].values, float)
    order = np.argsort(f)
    return f[order], q[order]


# ---------------------------------------------------------------------------
# One point
# ---------------------------------------------------------------------------
def point_identity(knobs=None, parts=None, lj_nH=36.0, cj_fF=45.0, soi=None, setup=None,
                   jj_model=JJ_MODEL, jj_mesh=JJ_MESH):
    """(point_id, full knobs, part choice, component params, setup) -- the
    content hash that identifies a point, shared by run_point and sweep.py."""
    s_setup = setup_for(parts, setup)
    choice = full_choice(parts)
    full = dict(all_knobs(choice), **(knobs or {}))
    component = dict(Lj_nH=lj_nH, Cj_fF=cj_fF, soi=soi, jj_model=jj_model, jj_mesh=jj_mesh,
                     line_extend_um=LINE_EXTEND_UM,
                     lc_sheet_gap_um=LC_SHEET_GAP_UM if jj_model == "split" else None)
    if any(r in choice for r in OPTIONAL_ROLES):        # lines: 50 ohm ends, complex solve
        component.update(shunt_ohm=SHUNT_OHM, eigensolver="complex")
    pid = st.point_fingerprint(st.SCOPE, dict(full, parts=choice), component, s_setup)
    return pid, full, choice, component, s_setup


def run_point(knobs: dict | None = None, parts: dict | None = None,
              lj_nH: float = 36.0, cj_fF: float = 45.0, soi: dict | None = None,
              setup: dict | None = None, data_dir: str = os.path.join(HERE, "data"),
              label: str | None = None, sweep: dict | None = None,
              jj_model: str = JJ_MODEL, jj_mesh: str = JJ_MESH,
              project: str = os.path.join(PROJECT_DIR, "mergemon_points.aedt"),
              resume: bool = True, verbose: bool = True, prepare_only: bool = False) -> dict:
    """Build, render, solve and EPR-analyse ONE design point.

    Args:
        knobs: overrides on the assembly's knobs (unit strings), e.g.
            dict(t_length="40um", ind_length="15mm").
        parts: {role: variant} part choice (default: DEFAULT_PARTS).
        lj_nH, cj_fF: PER junction. The two SQUID junctions are in parallel:
            for a total L, C use 2L and C/2 each (36 nH / 45 fF <-> 18 nH / 90 fF).
        soi: None (solid Si). Planned: dict(device_um=0.22, gap_um=3.0,
            gap_material="vacuum") for the SOI stack. Not implemented yet.
        setup: eigenmode setup overrides on setup_for(parts) (DEFAULT_SETUP, plus
            LINE_SETUP's fixed 10 passes when a line is chosen).
        sweep: optional tag, e.g. dict(knob="t_length", value="40um"), stored
            with the record so trends.py can group points.
        jj_model: "split" (L-only + C-only sheet per junction, as in the old
            solid-Si run) or "single" (one L+C sheet per junction).
        jj_mesh: max mesh length on the junction sheets (old run: "100nm").
        project: the AEDT project every point is drawn into (one design each).
        resume: skip the solve if this exact point is already in data_dir.
        prepare_only: render + set up the solve, save, return; no solve, no record
            in data_dir (to inspect the model in AEDT first).

    Returns the record (also written to data_dir as one JSON).
    """
    if soi is not None:
        raise NotImplementedError("the SOI knob is planned but not implemented yet; "
                                  "run with soi=None (solid silicon)")
    t0 = time.time()
    pid, full, choice, component, s_setup = point_identity(knobs, parts, lj_nH, cj_fF,
                                                           soi, setup, jj_model, jj_mesh)

    if resume and not prepare_only:
        hit = st.find_existing_run(data_dir, pid)
        if hit is not None:
            if verbose:
                print(f"  [{pid}] already solved ({hit['run_name']}), skipping")
            return hit

    dname = st.design_name(pid, st.design_tag(label, sweep))
    record = dict(
        run_name=st.make_run_name("mergemon", label), point_id=pid,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"), scope=st.SCOPE,
        parts=choice, knobs=full, component_params=component, settings=s_setup,
        sweep=sweep, run_context=st.run_context(platform_tool="hfss", design_name=dname),
        fom={}, quality={}, warnings=[],
    )
    warns = record["warnings"]

    design, names = build_design(parts=choice, **(knobs or {}))
    record["meander"] = dict(names.meander)
    jj = design.qgeometry.tables["junction"]
    junctions = list(names.junctions)
    assert set(jj["name"]) >= set(junctions), "junction table incomplete (gotcha 1)"

    # Lj/Cj on the junction qgeometry, row by row (gotcha 32).
    for col, val in (("hfss_inductance", f"{lj_nH}nH"), ("hfss_capacitance", f"{cj_fF}fF"),
                     ("q3d_inductance", f"{lj_nH}nH"), ("q3d_capacitance", f"{cj_fF}fF"),
                     ("aedt_hfss_inductance", lj_nH * 1e-9),
                     ("aedt_hfss_capacitance", cj_fF * 1e-15)):
        if col in jj.columns:
            jj[col] = val

    from qiskit_metal.analyses.quantization import EPRanalysis
    eig = EPRanalysis(design, "hfss")
    s = eig.sim.setup
    s.name = SETUP_NAME
    for k, v in s_setup.items():
        s[k] = v
    s.vars = Dict({f"Lj_{j}": f"{lj_nH} nH" for j in junctions}
                  | {f"Cj_{j}": f"{cj_fF} fF" for j in junctions})
    eig.setup.junctions = Dict({
        j: Dict(Lj_variable=f"Lj_{j}", Cj_variable=f"Cj_{j}",
                rect=f"JJ_rect_Lj_{JJ_COMPONENT}_{j}", line=f"JJ_int_{j}")
        for j in junctions})
    eig.setup.sweep_variable = f"Lj_{junctions[0]}"

    # ---- render ----------------------------------------------------------
    if not eig.sim.renderer_initialized:
        eig.sim._initialize_renderer()
    renderer = eig.sim.renderer
    from pyEPR import config as epr_config
    epr_config.root_dir = PYEPR_DIR
    _use_project(renderer, project)
    _draw_junctions(renderer, jj_model, LINE_EXTEND_UM)
    renderer.options["max_mesh_length_jj"] = jj_mesh
    if verbose:
        print(f"  [{pid}] rendering {dname} into {project} ...", flush=True)
    # box_plus_buffer=False: substrate + vacuum box = the chip = the ground
    # rectangle, so the ground's edges lie on the box walls (really grounded).
    eig.sim._render(name=dname, solution_type="eigenmode", selection=[],
                    open_pins=[], port_list=[], jj_to_port=None, ignored_jjs=None,
                    box_plus_buffer=False, vars_to_initialize=eig.sim.setup.vars)

    objs = list(renderer.pinfo.get_all_object_names())
    want = [n for j in eig.setup.junctions.values() for n in (j.rect, j.line)]
    if jj_model == "split":
        want += [f"JJ_rect_Cj_{JJ_COMPONENT}_{j}" for j in junctions]
    missing = [n for n in want if n not in objs]
    if missing:
        raise RuntimeError(f"junction objects {missing} not in the HFSS model; "
                           f"JJ-like objects: {[o for o in objs if 'JJ' in o]}")
    lossy = bool(names.terminations)
    if lossy:
        record["terminations"] = _add_terminations(renderer, names.terminations)
        if verbose:
            print(f"  [{pid}] 50 ohm ends: {', '.join(record['terminations'])}", flush=True)

    # ---- solve -----------------------------------------------------------
    t_solve = time.time()
    sname = renderer.initialize_eigenmode(**eig.sim.setup)
    if lossy:
        _force_complex_freq(renderer, sname, s_setup)
    stray = _drop_stray_setups(renderer, sname)
    if stray:
        record["quality"]["stray_setups_deleted"] = stray
    eig.sim.sim_setup_name = sname
    if prepare_only:                     # rendered + set up, not solved: inspect in AEDT
        renderer.pinfo.project.save()
        d = renderer.pinfo.design
        record.update(prepared=True, project=project, aedt_design=f"{dname}_hfss",
                      setups=list(d.get_setup_names()),
                      boundaries=list(d._boundaries.GetBoundaries()[::2]),
                      objects=sorted(renderer.pinfo.get_all_object_names()))
        if verbose:
            print(f"  [{pid}] prepared (not solved): {dname}_hfss in {project}; "
                  f"setups {record['setups']}; boundaries {record['boundaries']}", flush=True)
        return record
    if verbose:
        print(f"  [{pid}] solving eigenmode{' (complex: Q, kappa)' if lossy else ''} ...",
              flush=True)
    renderer.analyze_setup(sname)
    renderer.pinfo.project.save()
    eig.sim.compute_convergences()
    record["quality"]["solve_s"] = time.time() - t_solve
    try:
        ct = eig.sim.convergence_t
        n_pass = int(ct.index.max())
        dfreq = float(ct["Max Delta Freq. %"].dropna().iloc[-1])
        record["quality"].update(n_passes=n_pass, max_delta_f_pct=dfreq,
                                 converged=dfreq < s_setup["max_delta_f"])
        if n_pass >= s_setup["max_passes"] and dfreq > s_setup["max_delta_f"]:
            warns.append("stopped at max_passes: out of budget, not converged")
    except Exception as err:                            # noqa: BLE001
        warns.append(f"convergence read failed: {type(err).__name__} {err}")

    # ---- EPR -------------------------------------------------------------
    eig.run_epr()
    epra = renderer.epr_quantum_analysis           # on the renderer (gotcha 14)
    var = list(epra.results)[-1]
    r = epra.results[var]
    f_lin = np.asarray(r["f_0"], dtype=float).flatten() / 1e3
    P_raw = np.asarray(r["Pm_normed"], dtype=float).reshape(len(f_lin), -1)
    raw = foms(f_lin, np.asarray(r["f_ND"], float).flatten() / 1e3,
               np.asarray(r["chi_ND"], float), np.asarray(r["chi_O1"], float),
               P_raw, len(junctions), lj_nH)
    record["fom"].update({f"{k}_raw": v for k, v in raw.items()})

    try:
        fl, fnd, cnd, co1, P, virial = cj_corrected(epra, var, cj_fF * 1e-15)
        record["fom"].update(foms(fl, fnd, cnd, co1, P, len(junctions), lj_nH))
        record["quality"]["virial_pct_per_mode_cjfix"] = virial
        bad = [f"mode {i}: {v:+.0f}%" for i, v in enumerate(virial) if abs(v) > 10]
        if bad:     # a physical mode has U_cap = U_ind; e.g. an RLC-sheet pole does not
            warns.append(f"virial off ({', '.join(bad)}): likely a spurious mode")
    except Exception as err:                            # noqa: BLE001
        warns.append(f"Cj correction failed ({type(err).__name__}: {err}); "
                     "only the raw pyEPR FoMs are valid")

    f = record["fom"] if "f01_GHz" in record["fom"] else raw
    if lossy:                                  # Q from HFSS's complex eigenfrequencies
        try:
            fq, Q = _quality_factors(renderer)
            qm = int(f["qubit_mode"])
            rm = 1 - qm
            record["fom"].update(
                Q_q=float(Q[qm]), Q_r=float(Q[rm]),
                kappa_MHz=float(fq[rm] * 1e3 / Q[rm]),            # kappa/2pi = f_r / Q_r
                T1_purcell_us=float(Q[qm] / (2 * np.pi * fq[qm] * 1e9) * 1e6))
            if not np.all(np.isfinite(Q)) or np.any(Q > 1e12):
                warns.append("Q infinite: the solve ran the real eigensolver (gotcha 15)")
        except Exception as err:                        # noqa: BLE001
            warns.append(f"Q / kappa read failed: {type(err).__name__}: {err}")
    if f["p_q_total"] < 0.5:
        warns.append(f"p_q = {f['p_q_total']:.4f}: junctions barely participating")
    pq = [f.get(f"p_q_{i}") for i in range(len(junctions))]
    if len(pq) == 2 and None not in pq and abs(pq[0] - pq[1]) > 0.1 * f["p_q_total"]:
        warns.append(f"junction participations differ: {pq[0]:.4f} vs {pq[1]:.4f}")
    if not 1.0 < f["f01_GHz"] < 30.0:
        warns.append(f"f01 = {f['f01_GHz']:.3f} GHz outside 1-30 GHz")

    record["quality"]["minutes"] = (time.time() - t0) / 60
    path = st.save_run(record, data_dir)
    if verbose:
        print(f"  [{pid}] f01 {f['f01_GHz']:.4f} GHz, f_r {f['f_r_GHz']:.4f} GHz, "
              f"alpha {f['alpha_MHz']:.1f} MHz, chi {f['chi_MHz']:.3f} MHz"
              + (f", kappa {record['fom']['kappa_MHz']:.3f} MHz (Q_r {record['fom']['Q_r']:.0f}), "
                 f"T1_Purcell {record['fom']['T1_purcell_us']:.1f} us"
                 if "kappa_MHz" in record["fom"] else "") + f" -> {path}")
        for w in warns:
            print("   WARNING:", w)
    return record
