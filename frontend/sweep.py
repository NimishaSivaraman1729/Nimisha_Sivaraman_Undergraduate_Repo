"""Star sweeps: vary one knob at a time around a base design.

A star (not a grid) keeps the count additive: 4 knobs x 5 values = 20 points,
not 625 (tutorial README: "the cost of a grid is multiplicative"). Every point
goes through simulate.run_point, so it is stored once and resumable: re-running
a sweep skips points already in data/.

    python frontend/sweep.py my_sweep.py             # on the AEDT machine
    python frontend/sweep.py my_sweep.py --dry-run   # anywhere: build + check
                                                        # every point's geometry
    python frontend/sweep.py my_sweep.py --prepare [--project x.aedt]
                                    # render + set up every point in AEDT, no solve
    python frontend/sweep.py my_sweep.py --project x.aedt
                                    # solve into x.aedt, not output/mergemon_points.aedt

A config is a Python file defining:
    BASE   = dict(...)                  # knob overrides for the base design
    SWEEPS = {knob: [values, ...]}      # unit strings; or Lj_nH / Cj_fF: numbers
    LJ_NH, CJ_FF                        # per junction (the base's)
    PARTS  = dict(...)                  # optional part choice
    SETUP  = dict(...)                  # optional eigenmode setup overrides
    JJ     = dict(jj_model=, jj_mesh=)  # optional junction model (default split, 100nm)
    LABEL  = "..."                      # optional, cosmetic
"""

from __future__ import annotations

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def star_points(base: dict, sweeps: dict):
    """(knob, value, knobs) for each point; the base point once, first."""
    yield None, None, dict(base)
    for knob, values in sweeps.items():
        for v in values:
            knobs = dict(base, **{knob: v})
            if knobs == base:
                continue                     # that is the base point
            yield knob, v, knobs


def ground_clearance_um(p):
    """Smallest gap between ground-side parts that must NOT touch: resonator
    fingers vs meander, and the capacitor's two plates (column fingers vs ground
    finger / ground frame, ground finger vs column). These all belong to the
    ground conductor, so if they touch, the conductor check cannot tell -- the
    resonator capacitor is silently shorted. Default squid/coupler only."""
    from parts import geometry as g
    from shapely.ops import unary_union
    island = unary_union([g.squid_bottom(p)[0], g.t_coupler(p)])
    col_fingers, gnd_finger = g.finger_cap(p)
    meander, column, frame = g.meander(p), g.column_lanes(p, island), g.ground_frame(p)
    plate_a = unary_union([col_fingers, column])
    plate_b = unary_union([gnd_finger, frame])
    return 1e3 * min(meander.distance(unary_union([col_fingers, gnd_finger])),
                     plate_a.distance(plate_b))


def check_geometry(knobs, parts=None):
    """Build the design, count its conductors (names.n_conductors expected: 2,
    3 with a feedline) and check the ground-side clearance; no Ansys needed.
    Returns (n_conductors, clearance_um, names)."""
    import tempfile

    from assembly import build_design
    from gds_check import conductors, export_gds
    from parts.catalog import DEFAULT_PARTS, full_choice
    design, names = build_design(parts=parts, **knobs)
    out = os.path.join(HERE, "output")
    os.makedirs(out, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out) as tmp:          # keep files under this folder
        path = export_gds(design, os.path.join(tmp, "check.gds"))
        n = len(conductors(path, names.layer))
    choice = full_choice(parts)
    default = all(choice[r] == DEFAULT_PARTS[r] for r in ("squid", "coupler"))
    clear = ground_clearance_um(names.geom) if default else None
    return n, clear, names


JUNCTION_KNOBS = ("Lj_nH", "Cj_fF")     # sweepable like geometry knobs; numbers, per junction


def split_junction(knobs, lj_nH, cj_fF):
    """(geometry knobs, Lj, Cj): Lj_nH / Cj_fF in `knobs` override the config's."""
    geo = {k: v for k, v in knobs.items() if k not in JUNCTION_KNOBS}
    return geo, float(knobs.get("Lj_nH", lj_nH)), float(knobs.get("Cj_fF", cj_fF))


def plan(base, sweeps, lj_nH, cj_fF, parts=None, setup=None, soi=None, jj=None):
    """[(knob, value, knobs, point_id)] for a star sweep, deduped by content: a
    sweep value equal to the base's is the base point (knob None). `jj` is
    dict(jj_model=..., jj_mesh=...) overrides on simulate's defaults. `knobs`
    may hold Lj_nH / Cj_fF (see split_junction)."""
    from simulate import point_identity
    points, seen = [], set()
    for knob, value, knobs in star_points(base, sweeps):
        geo, lj, cj = split_junction(knobs, lj_nH, cj_fF)
        pid = point_identity(geo, parts, lj, cj, soi, setup, **(jj or {}))[0]
        if pid not in seen:
            seen.add(pid)
            points.append((knob, value, knobs, pid))
    return points


def config_plan(cfg):
    """plan() for a loaded config module."""
    return plan(getattr(cfg, "BASE", {}), cfg.SWEEPS, cfg.LJ_NH, cfg.CJ_FF,
                parts=getattr(cfg, "PARTS", None), setup=getattr(cfg, "SETUP", None),
                jj=getattr(cfg, "JJ", None))


def run_sweep(base, sweeps, lj_nH, cj_fF, parts=None, setup=None, soi=None, jj=None,
              data_dir=os.path.join(HERE, "data"), label=None, dry_run=False,
              prepare=False, project=None):
    """prepare: render + set up every point into `project` WITHOUT solving
    (simulate.run_point(prepare_only=True)), to look at the models in AEDT."""
    import store as st

    points = plan(base, sweeps, lj_nH, cj_fF, parts, setup, soi, jj)
    print(f"{len(points)} distinct points ({len(sweeps)} knobs around the base)")
    out = []
    for i, (knob, value, knobs, pid) in enumerate(points, 1):
        tag = f"{knob}={value}" if knob else "base"
        geo, lj, cj = split_junction(knobs, lj_nH, cj_fF)
        if dry_run:
            n, clear, names = check_geometry(geo, parts)
            done = "solved" if st.find_existing_run(data_dir, pid) else "to solve"
            m = names.meander
            ok = n == names.n_conductors and (clear is None or clear >= 1.0)
            cl = "--" if clear is None else f"{clear:.2f}um"
            dname = st.design_name(pid, st.design_tag(label, dict(knob=knob, value=value)))
            print(f"[{i}/{len(points)}] {tag:28s} {dname}  conductors={n} "
                  f"clearance={cl} {'OK' if ok else 'PROBLEM'}  "
                  f"meander {m.n_runs}x{m.run * 1e3:.0f}um  ({done})")
            out.append(dict(tag=tag, point_id=pid, conductors=n, clearance_um=clear))
            continue
        from simulate import run_point
        print(f"[{i}/{len(points)}] {tag}")
        extra = dict(prepare_only=True) if prepare else {}
        if project is not None:
            extra["project"] = project
        try:
            out.append(run_point(geo, parts=parts, lj_nH=lj, cj_fF=cj, soi=soi,
                                 setup=setup, data_dir=data_dir, label=label,
                                 sweep=dict(knob=knob, value=value), **(jj or {}), **extra))
        except Exception as err:                       # noqa: BLE001
            # One failed point must not lose the rest of the sweep.
            print(f"   FAILED: {type(err).__name__}: {err}")
    return out


def load_config(path):
    spec = importlib.util.spec_from_file_location("sweep_config", path)
    cfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cfg)
    return cfg


if __name__ == "__main__":
    argv = sys.argv[1:]
    project = argv[argv.index("--project") + 1] if "--project" in argv else None
    args = [a for a in argv if not a.startswith("--") and a != project]
    if not args:
        sys.exit(__doc__)
    cfg = load_config(args[0])
    run_sweep(getattr(cfg, "BASE", {}), cfg.SWEEPS, cfg.LJ_NH, cfg.CJ_FF,
              parts=getattr(cfg, "PARTS", None), setup=getattr(cfg, "SETUP", None),
              jj=getattr(cfg, "JJ", None), label=getattr(cfg, "LABEL", None),
              dry_run="--dry-run" in argv, prepare="--prepare" in argv, project=project)
