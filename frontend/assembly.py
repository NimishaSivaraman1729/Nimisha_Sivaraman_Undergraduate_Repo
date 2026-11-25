"""Assemble chosen parts into the mergemon design (qiskit-metal side).

Same output as kb2/mergemon/mergemon_design.py, now built from the part
catalog: two conductors (three with a feedline), nothing subtracted, so it
renders into HFSS as non-overlapping PECs --

  ground        ground plane (with the etched pocket as a hole) + top squid +
                coupling column + resonator capacitor + meander, ONE polygon;
                a flux line is part of it (its gaps cut out, shorted above the
                SQUID)
  bottom_squid  the floating island (bottom squid + coupler) + the junction
                qgeometries (jj_left, jj_right) the HFSS renderer turns into
                lumped RLCs
  feedline      (optional) the readout feedline's centre conductor + coupling
                finger; names.terminations lists the 50 ohm sheets that
                simulate.run_point adds at the lines' ends

The chip is sized and centred to the ground rectangle, so renderers add no
ground of their own. Component names are kept from mergemon_design.py so HFSS
object names (JJ_rect_Lj_bottom_squid_jj_left, ...) are unchanged.

    from assembly import build_design
    design, names = build_design(cap_length="250um", ind_length="15mm")
    design, names = build_design(parts=dict(coupler="t"), t_length="40um")
"""

from parts import geometry as g
from parts.catalog import DEFAULT_PARTS, chosen, default_knobs, expected_conductors, full_choice
from qiskit_metal import Dict, designs, draw
from qiskit_metal.qlibrary.core import QComponent
from shapely.ops import unary_union

# Placement of the local origin (left finger axis, SQUID bar top) in the
# original MET_ver_1_81_simple.gds, and the layer everything is drawn on.
GLOBAL_KNOBS = dict(pos_x="-1606.1um", pos_y="-251.1um", layer="1")


def part_knobs(p, parts=None):
    """Only the chosen parts' knobs from `p` (the components' options carry
    every default part's knobs). Geometry relies on this: a knob's presence
    can select a layout (ind_span -> fixed-span meander)."""
    return g.Attr({k: p[k] for k in default_knobs(parts)})


def assemble_all(p, parts=None):
    """Every conductor and line part, in local coordinates.

    Returns Attr(ground, island, feed, junctions, terms, cut): ground has the
    lines' keepouts (`cut`) removed; feed is None without a feedline; terms is
    {name: (box, axis)} for the 50 ohm sheets. Island parts first, so ground
    parts get the island to keep clearance from.
    """
    p = part_knobs(p, parts)
    selected = chosen(parts)
    acc = dict(ground=[], island=[], feed=[], cut=[], junctions={}, terms={})

    def add(s):
        for k in ("ground", "island", "feed", "cut"):
            acc[k] += getattr(s, k)
        acc["junctions"].update(s.junctions)
        acc["terms"].update(s.terms)

    for part in [x for x in selected if x.build_first]:
        add(part.builder(p, {}))
    ctx = {"island": unary_union(acc["island"])}
    for part in [x for x in selected if not x.build_first]:
        add(part.builder(p, ctx))
    cut = unary_union(acc["cut"]) if acc["cut"] else None
    ground = unary_union(acc["ground"])
    if cut is not None:
        ground = ground.difference(cut)
    out = g.Attr(ground=ground, island=unary_union(acc["island"]),
                 feed=unary_union(acc["feed"]) if acc["feed"] else None,
                 junctions=acc["junctions"], terms=acc["terms"], cut=cut)
    for name in ("ground", "island", "feed"):
        geom = out[name]
        if geom is not None and geom.geom_type != "Polygon":
            raise ValueError(f"{name} conductor did not merge into one polygon: "
                             "some part does not touch the rest of it")
    return out


def assemble(p, parts=None):
    """(ground, island, junctions) -- see assemble_all for the line parts."""
    a = assemble_all(p, parts)
    return a.ground, a.island, a.junctions


def _place(p, geoms):
    geoms = draw.rotate(geoms, p.orientation, origin=(0, 0))
    return draw.translate(geoms, p.pos_x, p.pos_y)


def _parts_of(comp):
    # Part names are plain strings: read the raw options, not the parsed `p`.
    return {k: str(v) for k, v in dict(comp.options.parts).items()}


class GroundConductor(QComponent):
    """Ground plane + everything connected to it, as one polygon."""
    default_options = Dict(GLOBAL_KNOBS, parts=Dict(DEFAULT_PARTS), **default_knobs())
    component_metadata = Dict(short_name="gnd", _qgeometry_table_poly="True")

    def make(self):
        p = self.p
        ground, _, _ = assemble(p, _parts_of(self))
        self.add_qgeometry("poly", {"ground": _place(p, [ground])[0]}, layer=p.layer)


class IslandConductor(QComponent):
    """The floating island and the junction gaps that join it to ground."""
    default_options = Dict(GLOBAL_KNOBS, parts=Dict(DEFAULT_PARTS), **default_knobs())
    component_metadata = Dict(short_name="bsq", _qgeometry_table_poly="True",
                              _qgeometry_table_junction="True")

    def make(self):
        p = self.p
        _, island, junctions = assemble(p, _parts_of(self))
        self.add_qgeometry("poly", {"base": _place(p, [island])[0]}, layer=p.layer)
        names = list(junctions)
        for n, line in zip(names, _place(p, [junctions[n] for n in names])):
            self.add_qgeometry("junction", {n: line}, width=p.tab_width, layer=p.layer)


class FeedConductor(QComponent):
    """The readout feedline's centre conductor + coupling finger (its own
    conductor; 50 ohm to ground at its far end, added at render time)."""
    default_options = Dict(GLOBAL_KNOBS, parts=Dict(DEFAULT_PARTS), **default_knobs())
    component_metadata = Dict(short_name="feed", _qgeometry_table_poly="True")

    def make(self):
        p = self.p
        feed = assemble_all(p, _parts_of(self)).feed
        self.add_qgeometry("poly", {"feed": _place(p, [feed])[0]}, layer=p.layer)


def all_knobs(parts=None):
    """Every knob a design with these parts accepts, with defaults."""
    return dict(GLOBAL_KNOBS, **default_knobs(parts))


def terminations(pp, choice):
    """[(name, (x0, y0, x1, y1) in mm, axis)] of the 50 ohm sheets, placed in the
    design frame (axis: the current direction across the open gap)."""
    a = assemble_all(pp, choice)
    out = []
    for name, (shape, axis) in sorted(a.terms.items()):
        placed = _place(pp, [shape])[0]
        if float(pp.orientation) % 180:
            axis = "y" if axis == "x" else "x"
        out.append((name, placed.bounds, axis))
    return out


def build_design(parts=None, **knobs):
    """Build the design.

    Args:
        parts: {role: variant}, e.g. dict(coupler="t"); defaults to DEFAULT_PARTS.
        **knobs: any knob of the chosen parts (see all_knobs()), as unit strings.

    Returns:
        (design, names): names has component names, junction names, the layer,
        the parts choice, the meander layout, and pocket/ground geometry.
    """
    choice = full_choice(parts)
    p = all_knobs(choice)
    unknown = set(knobs) - set(p)
    if unknown:
        raise KeyError(f"unknown knob(s): {sorted(unknown)}. Valid: {sorted(p)}")
    p.update(knobs)

    design = designs.DesignPlanar()
    design.overwrite_enabled = True
    opts = Dict(p, parts=Dict(choice))
    GroundConductor(design, "ground", options=opts)
    IslandConductor(design, "bottom_squid", options=opts)
    if "feedline" in choice:
        FeedConductor(design, "feedline", options=opts)
    design.rebuild()

    # Chip = the ground rectangle, so renderers don't add ground beyond it.
    pp = design.components["ground"].p
    gp = part_knobs(pp, choice)                  # parsed, chosen parts only
    x0, y0, x1, y1 = g.ground_bounds(gp)
    chip = design.chips.main.size
    chip.center_x = f"{pp.pos_x + (x0 + x1) / 2}mm"
    chip.center_y = f"{pp.pos_y + (y0 + y1) / 2}mm"
    chip.size_x = f"{x1 - x0}mm"
    chip.size_y = f"{y1 - y0}mm"
    design.chips.main.material = "silicon"      # a string: a list renders as vacuum (gotcha 30)

    _, _, junctions = assemble(pp, choice)
    return design, Dict(
        ground="ground", bottom_squid="bottom_squid", junctions=sorted(junctions),
        feedline="feedline" if "feedline" in choice else None,
        terminations=terminations(pp, choice), n_conductors=expected_conductors(choice),
        layer=int(pp.layer), parts=choice, knobs=p, geom=gp,
        meander=g.meander_layout(gp), pocket_right=g.pocket_right(gp),
        pocket_bounds=g.pocket_bounds(gp), ground_bounds=g.ground_bounds(gp),
        pocket=draw.translate(g.pocket(gp), pp.pos_x, pp.pos_y))


if __name__ == "__main__":
    d, n = build_design()
    print(d.qgeometry.tables["poly"][["component", "name", "layer"]])
    print(f"meander: {n.meander.n_runs} runs of {n.meander.run * 1e3:.1f} um")
