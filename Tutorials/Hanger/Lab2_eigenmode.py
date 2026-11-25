"""Eigenmode cross-check of the 30 um design: lumped RLCs instead of ports.

    python Lab2_eigenmode.py              # build everything, STOP before solving
    python Lab2_eigenmode.py --solve      # build (if needed) and solve
    python Lab2_eigenmode.py --plots      # after solving: mesh + E-field plots

Why eigenmode is worth doing here
---------------------------------
Driven-modal adapts its mesh by watching Delta S at ONE frequency. Off
resonance |S21| ~ 1 whatever the mesh does, so the solver can report
"converged" while the resonator itself is still badly meshed -- which is why
f_r kept walking upward (6.79 -> 7.09 -> 7.12 GHz) every time the mesh was
re-adapted, with every run calling itself converged.

Eigenmode has no ports and no sweep. It solves the source-free modes directly
and its adaptive criterion is MaxDeltaFreq -- the change in the MODE FREQUENCY
per pass. It converges on the quantity we actually want, so it is an
independent check rather than another instance of the same method.

Ports -> lumped RLCs
--------------------
Eigenmode cannot have ports. The same two sheets that carried the 50 ohm lumped
ports get a 50 ohm LUMPED RLC instead: pad_gap x pad_width (200 x 120 um),
bridging each launchpad's back edge to the ground plane, current line ACROSS
that gap. Identical geometry, different boundary type.

Those resistors are the ONLY loss in the model, so the eigenvalue comes back
complex and Q = Re(f)/(2 Im(f)) is the loaded Q -- directly comparable with the
Q_l = Q_e extracted from the driven-modal fit.

The sheets must ABUT the pad and the ground plane without overlapping either.
A lumped element overlapping PerfE is silently shorted: no current flows
through it, nothing dissipates, HFSS decides the model is lossless, picks the
real eigensolver and hands back Q = infinity with no error anywhere. The
checkpoint below prints the abutment so it can be verified before solving.

Flags
-----
    --finger 30          finger_length in um
    --min-freq 6.5       eigenmode MinimumFrequency, GHz
    --modes 1            number of modes to solve
    --max-delta-freq 0.05   % change in mode frequency per pass (convergence)
    --max-passes 25      adaptive passes
    --ohm 50             resistance of each lumped RLC
    --solve              actually run the solve (default: stop at the checkpoint)
    --plots              create mesh and E-field plots (after a solve)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SETUP = "Setup"


# --------------------------------------------------------------------------
def add_lumped_rlcs(hfss, design, r_ohm=50.0):
    """Two 50 ohm lumped RLCs, pad back edge -> ground plane, line across the gap.

    Same sheets the driven-modal ports used. Geometry from
    LaunchpadWirebond.make(): the pad metal reaches pad_height + taper_height
    back from pos_x, and its ground pocket continues a further pad_gap, so the
    gap to bridge is [pos_x -+ (PH+TH), pos_x -+ (PH+TH+PG)], pad_width tall.
    """
    from qiskit_metal.toolbox_metal.parsing import parse_value

    modeler = hfss.modeler
    units = modeler.get_units()
    scale = {"mm": 1.0, "um": 1000.0, "meter": 1e-3, "m": 1e-3}.get(units, 1.0)

    def u(v):
        # Unit-TAGGED always: pyEPR's LENGTH_UNIT is 'meter', so a bare number
        # in the current line is read as metres.
        return f"{v * scale}{units}"

    made = []
    for comp in ("Pad1", "Pad2"):
        o = design.components[comp].options

        def P(k):
            return parse_value(o[k], design.variables)

        px, py, ori = P("pos_x"), P("pos_y"), float(o["orientation"])
        s_dir = -1 if abs(ori) < 1 else +1
        PW, PH, TH, PG = (P("pad_width"), P("pad_height"),
                          P("taper_height"), P("pad_gap"))
        pad_edge = px + s_dir * (PH + TH)
        gnd_edge = px + s_dir * (PH + TH + PG)
        x0, x1 = sorted([pad_edge, gnd_edge])
        y0, y1 = py - PW / 2, py + PW / 2
        ymid = (y0 + y1) / 2

        name = f"RLC_{comp}"
        modeler._modeler.CreateRectangle(
            ["NAME:RectangleParameters", "IsCovered:=", True,
             "XStart:=", u(x0), "YStart:=", u(y0), "ZStart:=", u(0.0),
             "Width:=", u(x1 - x0), "Height:=", u(y1 - y0), "WhichAxis:=", "Z"],
            modeler._attributes_array(name=name))
        modeler._make_lumped_rlc(
            r=f"{r_ohm}ohm", l=0, c=0,
            start=[u(gnd_edge), u(ymid), u(0.0)],   # ground (reference)
            end=[u(pad_edge), u(ymid), u(0.0)],     # pad (signal)
            obj_arr=["Objects:=", [name]],
            name=f"Lumped_{comp}")
        made.append((name, x0, x1, y0, y1, pad_edge, gnd_edge))
    return made


def make_eigen_setup(des, min_ghz, modes, max_delta_f, max_passes):
    am = des.GetModule("AnalysisSetup")
    for s in list(am.GetSetups()):
        am.DeleteSetups([s])
    am.InsertSetup("HfssEigen", [
        f"NAME:{SETUP}",
        "MinimumFrequency:=", f"{min_ghz}GHz",
        "NumModes:=", int(modes),
        # Percent change in the MODE FREQUENCY per pass. This is the whole
        # reason for using eigenmode: it converges on the quantity we want,
        # not on Delta S at some unrelated frequency.
        "MaxDeltaFreq:=", float(max_delta_f),
        "ConvergeOnRealFreq:=", True,
        "MaximumPasses:=", int(max_passes),
        "MinimumPasses:=", 1,
        "MinimumConvergedPasses:=", 2,
        "PercentRefinement:=", 30,
        "IsEnabled:=", True,
        "BasisOrder:=", -1,
    ])
    return am


def checkpoint(hfss, des, design, rlcs, args, name):
    """Everything worth eyeballing before committing to a solve."""
    ed = des.SetActiveEditor("3D Modeler")
    bm = des.GetModule("BoundarySetup")

    def ext(obj):
        pts = [[float(c) for c in ed.GetVertexPosition(v)]
               for v in ed.GetVertexIDsFromObject(obj)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), max(xs), min(ys), max(ys)

    print("\n" + "=" * 78)
    print("CHECKPOINT -- nothing has been solved")
    print("=" * 78)
    print(f"design        : {name}   solution type {des.GetSolutionType()}")
    objs = sorted(ed.GetMatchedObjectName("*"))
    print(f"objects       : {len(objs)}")
    print(f"                {', '.join(objs)}")
    try:
        bnds = list(bm.GetBoundaries())
    except Exception:
        bnds = []
    print(f"boundaries    : {bnds}")
    try:
        print(f"excitations   : {list(bm.GetExcitations()) or 'none (correct for eigenmode)'}")
    except Exception:
        print("excitations   : none (correct for eigenmode)")
    try:
        print(f"PerfE objects : {len(list(bm.GetBoundaryAssignment('PerfE')))}")
    except Exception:
        pass

    print("\nlumped RLCs (the only loss in the model):")
    for nm, x0, x1, y0, y1, pad_edge, gnd_edge in rlcs:
        print(f"  {nm}: x {x0:+.4f}..{x1:+.4f}  y {y0:+.4f}..{y1:+.4f}   "
              f"{(x1-x0)*1000:.0f} x {(y1-y0)*1000:.0f} um, {args.ohm:g} ohm")
        print(f"       current line  ground({gnd_edge:+.4f}) -> pad({pad_edge:+.4f})")

    print("\nabutment check (overlap into metal must be ZERO):")
    for comp, nm in (("Pad1", "RLC_Pad1"), ("Pad2", "RLC_Pad2")):
        try:
            rx0, rx1, ry0, ry1 = ext(nm)
            lx0, lx1, ly0, ly1 = ext(f"launch_pad_{comp}")
            gap = min(abs(rx1 - lx0), abs(rx0 - lx1))
            print(f"  {nm} vs launch_pad_{comp}: shared edge gap "
                  f"{gap*1000:.4f} um   y match "
                  f"{'YES' if abs(ry0-ly0) < 1e-9 and abs(ry1-ly1) < 1e-9 else 'NO'}")
        except Exception as e:
            print(f"  {nm}: could not measure ({e})")

    print(f"\neigenmode setup:")
    print(f"  MinimumFrequency = {args.min_freq} GHz")
    print(f"  NumModes         = {args.modes}")
    print(f"  MaxDeltaFreq     = {args.max_delta_freq} %   "
          f"(~{args.max_delta_freq/100*7.1*1e3:.1f} MHz at 7.1 GHz)")
    print(f"  MaximumPasses    = {args.max_passes}, MinimumConvergedPasses = 2")
    print("\nWhat to check in the GUI:")
    print("  * the two RLC_Pad* sheets sit in the etched gap BEHIND each")
    print("    launchpad, touching pad metal on one edge and ground on the")
    print("    other, overlapping neither")
    print("  * Boundaries lists two Lumped RLC entries and no ports")
    print("  * the resonator, capacitor and feedline look like the GDS")
    print("\nRe-run with --solve when it looks right.")
    print("=" * 78)


# --------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Eigenmode cross-check, 30 um design.")
    ap.add_argument("--finger", type=float, default=30.0)
    ap.add_argument("--min-freq", type=float, default=6.5)
    ap.add_argument("--modes", type=int, default=1)
    ap.add_argument("--max-delta-freq", type=float, default=0.05)
    ap.add_argument("--max-passes", type=int, default=25)
    ap.add_argument("--ohm", type=float, default=50.0)
    ap.add_argument("--design-name", default=None)
    ap.add_argument("--project", default=str(HERE / "Lab2_CPW.aedt"))
    ap.add_argument("--solve", action="store_true")
    ap.add_argument("--plots", action="store_true")
    ap.add_argument("--rebuild", action="store_true",
                    help="re-render even if the design already has geometry")
    args = ap.parse_args(argv)

    import Lab2_open_in_ansys as hm
    from Lab2 import build_design

    fl = args.finger
    name = args.design_name or f"Lab2_fl{fl:g}_eig"

    print("=" * 78)
    print(f"EIGENMODE cross-check — finger_length {fl:g} um — design {name!r}")
    print("=" * 78)

    design, _ = build_design(finger_length=f"{fl}um")
    hm.start_ansys()

    # Open the existing project rather than letting an empty one be created and
    # later saved over it.
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    desk = win32com.client.Dispatch(
        "AnsoftHfss.HfssScriptInterface").GetAppDesktop()
    proj_path = Path(args.project).resolve()
    if proj_path.stem not in list(desk.GetProjectList()):
        if proj_path.is_file():
            print(f"opening {proj_path}")
            desk.OpenProject(str(proj_path))

    hfss = design.renderers.hfss
    hfss.connect_ansys()
    if hfss.pinfo is None or hfss.pinfo.project is None:
        hfss.new_ansys_project()
        hfss.connect_ansys()
    hfss.activate_ansys_design(name, "eigenmode")
    hm.enforce_solution_type(hfss, "eigenmode")
    des = hfss.pinfo.design._design

    n_obj = len(list(des.SetActiveEditor("3D Modeler").GetMatchedObjectName("*")))
    rlcs = []
    if args.rebuild or n_obj == 0:
        print("\nrendering geometry (port_list=[] -- eigenmode has no ports) ...")
        hfss.clean_active_design()
        hfss.render_design(selection=[], open_pins=[], port_list=[],
                           jj_to_port=None, ignored_jjs=None,
                           box_plus_buffer=True)
        print(f"placing two {args.ohm:g} ohm lumped RLCs ...")
        rlcs = add_lumped_rlcs(hfss, design, args.ohm)
        make_eigen_setup(des, args.min_freq, args.modes,
                         args.max_delta_freq, args.max_passes)
        print("eigenmode setup created")
    else:
        print(f"\nreusing the {n_obj} objects already in {name!r} "
              f"(--rebuild to start over)")

    try:
        hfss.pinfo.project.save(str(proj_path))
        print(f"saved {proj_path}")
    except Exception as exc:
        print(f"could not save: {exc}")

    if not args.solve and not args.plots:
        checkpoint(hfss, des, design, rlcs, args, name)
        return 0

    if args.solve:
        t0 = time.time()
        print("\nsolving eigenmode ...", flush=True)
        des.Analyze(SETUP)
        print(f"solved in {time.time()-t0:.0f} s")
        try:
            hfss.pinfo.project.save(str(proj_path))
        except Exception:
            pass
        report_modes(hfss, name)

    if args.plots:
        make_plots(hfss, name, args)
    return 0


def make_plots(hfss, design_name, args):
    """Mesh and |E| on the substrate surface, exported as PNGs.

    Both are plotted on `main` -- the silicon chip's top surface -- because
    that is the plane the CPW and the resonator sit on, so it shows where the
    mode actually lives. Plotting on the air box would mostly show empty space.
    """
    hfss.connect_ansys(design_name=design_name)
    hfss.pinfo.connect_setup()
    out_mesh = HERE / f"eig_mesh_fl{args.finger:g}.png"
    out_efield = HERE / f"eig_Efield_fl{args.finger:g}.png"

    for quantity, folder, out, label in (
            ("Mesh", "Mesh", out_mesh, "mesh"),
            ("Mag_E", "E Field", out_efield, "|E|")):
        try:
            hfss.clear_fields(None)
        except Exception:
            pass
        try:
            print(f"\ncreating the {label} plot on 'main' ...")
            hfss.plot_fields("main", QuantityName=quantity, PlotFolder=folder,
                             IntrinsicVar="Phase='0deg'")
            shot = hfss.save_screenshot(str(out), show=False)
            msg = shot if shot else "screenshot FAILED (AEDT COM flakiness)"
            print(f"  {label}: {msg}")
        except Exception as exc:
            print(f"  {label} plot failed: {exc}")


def report_modes(hfss, design_name):
    """Mode frequencies and Q from the complex eigenvalue.

    With the two 50 ohm RLCs as the only loss, the eigenvalue is complex and
    Q = Re(f) / (2 Im(f)) is the LOADED Q -- the same quantity the driven-modal
    fit called Q_l. Q coming back infinite (or absurd) means no current is
    flowing through the resistors, i.e. they are shorted by overlapping metal.
    """
    print("\n" + "=" * 78)
    print("EIGENMODE RESULT")
    print("=" * 78)
    # Reconnect: pyEPR caches pinfo.setup, so reading after a design switch
    # reports the previous design's solution.
    hfss.connect_ansys(design_name=design_name)
    hfss.pinfo.connect_setup()
    # Read the RAW export rather than pyEPR's eigenmodes(), which returns
    # (freqs, kappa/2pi) -- NOT (freqs, Q). Printing that second value as "Q"
    # with one decimal shows 0.0 for a perfectly healthy kappa/2pi of
    # 0.0026 GHz, which looks exactly like a shorted resistor and is not.
    # The export file carries Re(f), Im(f) and Q explicitly.
    import re
    import tempfile
    try:
        setup = hfss.pinfo.setup
        fn = tempfile.mktemp()
        setup.get_solutions()._solutions.ExportEigenmodes(
            setup.solution_name, "", fn)
        rows = [ln for ln in open(fn).read().splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]
        for ln in rows:
            m = re.match(r"\s*(\d+)\s+([\d.eE+-]+)\s*\+\s*([\d.eE+-]+)\s*j\s+"
                         r"([\d.eE+-]+)", ln)
            if not m:
                print("   ", ln)
                continue
            mode, fr, fi, q = (int(m.group(1)), float(m.group(2)),
                               float(m.group(3)), float(m.group(4)))
            print(f"  mode {mode}:  f = {fr:.6f} GHz")
            print(f"            Im(f) = {fi*1e3:.4f} MHz   "
                  f"kappa/2pi = {2*fi*1e3:.4f} MHz")
            print(f"            Q = {q:,.2f}   "
                  f"(= Re(f) / (2 Im(f)) = {fr/(2*fi):,.2f})")
            if fi <= 0:
                print("            Im(f) <= 0 -- no dissipation. The RLCs are "
                      "shorted; check they abut rather than overlap the metal.")
    except Exception as exc:
        print(f"  could not read eigenmodes: {exc}")
    try:
        txt = hfss.pinfo.setup.get_convergence()[1]
        import re
        conv = re.search(r"Converged : (\w+)", txt)
        done = re.search(r"Completed : (\d+)", txt)
        mx = re.search(r"Maximum   : (\d+)", txt)
        print(f"\n  adaptive: {done.group(1)}/{mx.group(1)} passes, "
              f"converged: {conv.group(1)}")
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
