"""Parameterised mergemon layout (qiskit-metal), based on
MET_ver_1_81_simple.gds (cell mergemon_ver_1_801).

Same pattern as the tutorial's transmon_design.py: one `build_design()` holds
the geometry so the GDS check and any later simulation share it.

Dimensions are the original's rounded to 0.1 um (the rebuild only needs to be
within ~1 um of the GDS for HFSS), and arcs use few segments so the mesh stays
light. Layer 10/0 of the original is ignored (fill).

Everything is positive metal on ONE layer (`layer`, default 1), drawn as just
TWO polygons -- the two separate conductors -- so nothing overlaps when it is
rendered into HFSS as PEC:

  ground        one merged polygon:
                  ground plane  a rectangle `ground_margin` (300 um) beyond the
                                pocket on every side, with the pocket (the
                                etched rounded rectangle) as a hole
                  top squid     two identical cells, `cell_pitch` apart: a
                                5.5 um block (reaching up into the ground)
                                flaring into a 0.8 um finger, running down into
                                a 2.8 um-tall bar with a 2.7 x 1.2 um extension
                  resonator     the LC readout resonator: the long column (other
                                plate of the coupling capacitor, two lanes
                                either side of the T stem), the capacitor
                                fingers, and the meandering inductor from the
                                column's foot to the ground on the right.
                                `cap_length` (finger length) sets C;
                                `ind_length` (meander wire length) sets L.
  bottom_squid  the floating island: bar, two pads, an arm up from each pad
                ending in a 1.2 um tab, and the T-shaped coupling capacitor
                hanging below (stem length `t_length`)

There are no subtract shapes, on purpose: qiskit-metal's HFSS renderer only
makes its own chip ground plane a PEC when the chip has subtract shapes, so
this way the drawn ground is the only ground and nothing is doubled up. The
chip itself is sized and centred to the ground rectangle.

The pocket follows the design: right edge at `pocket_right()` (cap_length),
bottom below the column's foot (ind_length). The ground finger and the
meander's last run run `gnd_overlap` into the ground.

Each SQUID tab points up at the underside of its extension, `jj_gap` (1 um)
away. That gap is where the junction's lumped RLC goes: it is recorded as a
'junction' qgeometry (jj_left, jj_right), the same mechanism TransmonPocket
uses, so the HFSS renderer will place the RLC sheet there.

Local origin: the left finger's axis (x) and the SQUID bar's top edge (y).
`pos_x`, `pos_y` place that point where it sits in the original GDS. All
dimensions in um.
"""

import numpy as np
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from qiskit_metal import designs, draw, Dict
from qiskit_metal.qlibrary.core import QComponent

ARC_RES = 8       # segments per quarter circle
MEANDER_RES = 4   # fewer on the meander's ~140 turns (r = 3.3 um: < 0.1 um error)


OPTIONS = Dict(
    layer="1",
    # ======================= SQUID ===================================
    cell_pitch="18.7um",            # right finger axis - left finger axis
    # --- finger (top squid); x offsets are from each cell's finger axis ---
    finger_width="0.8um",
    flare_start="14.9um",           # where the R arcs leave the finger
    flare_radius="4.5um",
    block_width="5.5um",            # centred on the finger
    block_top="20.5um",
    # --- top bar + extension (top squid) ---
    top_bar_bottom="9um",
    top_bar_height="2.8um",
    top_bar_right="3.6um",          # right end of the bar
    ext_width="2.7um",
    ext_height="1.2um",             # centred on the top bar
    # --- junction ---
    junction_x="-9.8um",            # left edge of tab and extension
    jj_gap="1um",                   # tab top -> extension bottom; the lumped RLC
    tab_width="1.2um",              # also the RLC sheet width
    # --- bottom squid ---
    arm_width="2.8um",              # centred on the tab
    arm_top="8.4um",                # where the tab starts
    pad_x="-10.9um",                # left edge of the pad
    pad_width="3.4um",
    pad_top="1.9um",
    bar_height="2.3um",             # spans left pad's left edge -> right pad's right edge
    # ======================= coupling capacitor ======================
    # --- the T (part of bottom squid) ---
    t_x="0um",                      # stem axis, from the left finger axis
    t_bar_top="-0.4um",             # below the bar top: the T overlaps the bar
    t_bar_width="13um",             # rounded ends, radius t_bar_height / 2
    t_bar_height="3um",
    t_stem_width="3um",             # rounded tip, radius t_stem_width / 2
    t_length="34um",                # stem: T-bar underside -> tip
    t_fillet="3.3um",               # concave fillets where the stem meets the T bar
    # --- the long part (resonator column + lanes round the T) ---
    cc_gap="1.5um",                 # T -> column/lanes, all round
    cc_lane_top="-5.4um",           # top of the two lanes beside the stem
    cc_lane_width="3um",
    col_width="15um",               # column, centred on t_x
    col_taper_bottom="-49um",       # column narrows from col_width ...
    col_taper_top="-41um",          # ... to the lanes' outer width here
    # ======================= resonator capacitor (C) =================
    cap_length="302.5um",           # finger length  <-- resonator C
    cap_finger_width="2.5um",
    cap_finger_gap="3.8um",         # between neighbouring fingers
    cap_end_gap="2.5um",            # finger tip -> opposite side
    cap_top_y="-54.2um",            # centre line of the top (column-side) finger
    # ======================= meandering inductor (L) =================
    ind_length="19.3mm",            # total wire length  <-- resonator L
    ind_width="2um",
    ind_pitch="6.62um",             # row to row (not rounded: 69 rows would drift 1.4 um);
                                    # turns are semicircles of radius pitch/2
    ind_top_y="-76.4um",            # centre line of the top run (it ends in ground)
    ind_margin="18um",              # turn centres -> column edge / pocket edge
    # ======================= ground plane ============================
    # The pocket is the etched rounded rectangle, a hole in the ground. Right
    # edge = pocket_right() (follows cap_length); bottom follows the column's
    # foot (ind_length); left follows the column; top is fixed below the SQUID
    # finger blocks, which reach up into the ground.
    ground_margin="300um",          # ground plane extends this far past the pocket
    pocket_top="17.6um",
    pocket_margin_left="44.7um",    # column's left edge -> pocket edge
    pocket_margin_bottom="44.5um",  # column's foot -> pocket edge
    pocket_radius="40um",           # corner radius
    gnd_overlap="1um",              # ground-side ends run this far into the ground
    # Split the ground into two polygons, 'inner' (everything within this
    # distance of the pocket: inner ground, top squid, resonator) and 'border'
    # (the rest), e.g. to mesh them differently. 0 = one 'ground' polygon.
    inner_ground_margin="0um",
)


def _cell_axes(p):
    return (("left", 0.0), ("right", p.cell_pitch))


def _ext_bottom(p):
    return p.top_bar_bottom + (p.top_bar_height - p.ext_height) / 2


def _place(comp, geoms):
    p = comp.p
    geoms = draw.rotate(geoms, p.orientation, origin=(0, 0))
    return draw.translate(geoms, p.pos_x, p.pos_y)


def _t_shape(p):
    """The T coupler: rounded bar, stem with rounded tip, concave fillets."""
    rb, rs, f = p.t_bar_height / 2, p.t_stem_width / 2, p.t_fillet
    x, top = p.t_x, p.t_bar_top
    y_bar = top - rb                       # T-bar centre line
    y_under = top - p.t_bar_height         # T-bar underside
    parts = [
        LineString([(x - p.t_bar_width / 2 + rb, y_bar),
                    (x + p.t_bar_width / 2 - rb, y_bar)]).buffer(rb, resolution=ARC_RES),
        LineString([(x, y_bar), (x, y_under - p.t_length + rs)]).buffer(rs, resolution=ARC_RES),
    ]
    for s in (-1, 1):
        edge = x + s * rs
        corner = box(min(edge, edge + s * f), y_under - f, max(edge, edge + s * f), y_under)
        parts.append(corner.difference(
            Point(edge + s * f, y_under - f).buffer(f, resolution=ARC_RES)))
    return unary_union(parts)


def pocket_right(p):
    """x of the ground edge the resonator's ground-side ends join."""
    return p.t_x + p.col_width / 2 + p.cap_length + p.cap_end_gap


def meander_layout(p):
    """Rows and run length that give the requested inductor length.

    The top run sits at ind_top_y and runs from the left turns to the ground
    edge; the bottom run comes off the column. Rows are added downward (the
    column grows to meet them); the run length is then set so the total wire
    length is exactly ind_length. n_runs is odd so the top run ends on the right.
    """
    col_right = p.t_x + p.col_width / 2
    x_left = col_right + p.ind_margin
    run_max = pocket_right(p) - p.ind_margin - x_left
    r = p.ind_pitch / 2
    fixed_top = pocket_right(p) - x_left                     # top run -> ground
    n = 3
    while True:
        # bottom lead (col_right -> x_right) = run + margin; n-2 middle runs;
        # n-1 semicircular turns; top run fixed
        run = (p.ind_length - fixed_top - (n - 1) * np.pi * r - p.ind_margin) / (n - 1)
        if run <= run_max:
            break
        n += 2
    if run <= 4 * r:
        raise ValueError(f"ind_length={p.ind_length * 1e3:.0f} um is too short for one meander")
    return Dict(n_runs=n, run=run, x_left=x_left, x_right=x_left + run, radius=r,
                y_bottom=p.ind_top_y - (n - 1) * p.ind_pitch, col_right=col_right)


def _top_squid(p):
    """Upper half of both junction cells: finger, top bar, extension."""
    hw = p.finger_width / 2
    bar_top = p.top_bar_bottom + p.top_bar_height
    ye = _ext_bottom(p)
    cells = []
    for _side, fx in _cell_axes(p):
        jx = fx + p.junction_x
        flare = box(fx - p.block_width / 2, p.flare_start,
                    fx + p.block_width / 2, p.block_top)
        for s in (-1, 1):
            flare = flare.difference(
                Point(fx + s * (hw + p.flare_radius), p.flare_start)
                .buffer(p.flare_radius, resolution=ARC_RES))
        cells.append(unary_union([
            flare,
            box(fx - hw, bar_top, fx + hw, p.flare_start),
            box(jx + p.ext_width, p.top_bar_bottom, fx + p.top_bar_right, bar_top),
            box(jx, ye, jx + p.ext_width, ye + p.ext_height),
        ]))
    return unary_union(cells)


class BottomSquid(QComponent):
    """Lower half: bar, pads, arms, tabs and the T coupler as one polygon;
    plus the two junction gaps."""

    default_options = Dict(OPTIONS)
    component_metadata = Dict(short_name="bsq", _qgeometry_table_poly="True",
                              _qgeometry_table_junction="True")

    def make(self):
        p = self.p
        tab_top = _ext_bottom(p) - p.jj_gap
        cells = _cell_axes(p)
        parts = [box(cells[0][1] + p.pad_x, -p.bar_height,
                     cells[1][1] + p.pad_x + p.pad_width, 0.0), _t_shape(p)]
        jj_names, jj_lines = [], []
        for side, fx in cells:
            jx = fx + p.junction_x
            xc = jx + p.tab_width / 2
            parts += [
                box(fx + p.pad_x, 0, fx + p.pad_x + p.pad_width, p.pad_top),
                box(xc - p.arm_width / 2, p.pad_top, xc + p.arm_width / 2, p.arm_top),
                box(jx, p.arm_top, jx + p.tab_width, tab_top),
            ]
            jj_names.append(f"jj_{side}")
            jj_lines.append(LineString([(xc, tab_top), (xc, _ext_bottom(p))]))

        base = _place(self, [unary_union(parts)])[0]
        self.add_qgeometry("poly", {"base": base}, layer=p.layer)
        for n, line in zip(jj_names, _place(self, jj_lines)):
            self.add_qgeometry("junction", {n: line}, width=p.tab_width,
                               layer=p.layer)


def _resonator(p):
    """Readout resonator: coupling-capacitor column, finger capacitor (C) and
    meandering inductor (L). Every part of it joins the ground."""
    m = meander_layout(p)
    x, hc = p.t_x, p.col_width / 2
    lane_half = p.t_stem_width / 2 + p.cc_gap + p.cc_lane_width   # lanes' outer edge
    col_bottom = m.y_bottom - p.ind_width / 2

    # --- column + lanes, with the T (plus cc_gap) carved out ---
    lanes_top_r = p.cc_lane_width / 2
    column = unary_union([
        box(x - hc, col_bottom, x + hc, p.col_taper_bottom),
        draw.Polygon([(x - hc, p.col_taper_bottom), (x + hc, p.col_taper_bottom),
                      (x + lane_half, p.col_taper_top), (x - lane_half, p.col_taper_top)]),
        box(x - lane_half, p.col_taper_top, x + lane_half, p.cc_lane_top - lanes_top_r),
        *[LineString([(x + s * (lane_half - lanes_top_r), p.col_taper_top),
                      (x + s * (lane_half - lanes_top_r), p.cc_lane_top - lanes_top_r)]
                     ).buffer(lanes_top_r, resolution=ARC_RES) for s in (-1, 1)],
    ]).difference(_t_shape(p).buffer(p.cc_gap, resolution=ARC_RES))

    # --- resonator capacitor: fingers from the column and from ground ---
    pitch = p.cap_finger_width + p.cap_finger_gap
    rf = p.cap_finger_width / 2
    x_col, x_gnd = x + hc, pocket_right(p)
    col_fingers, gnd_finger = [], None
    for i in range(3):
        y = p.cap_top_y - i * pitch
        if i % 2 == 0:      # from the column, rounded tip near the ground
            col_fingers.append(LineString([(x_col - rf, y), (x_col + p.cap_length - rf, y)])
                               .buffer(rf, resolution=ARC_RES))
        else:               # from the ground, rounded tip near the column
            x_end = x_gnd + p.gnd_overlap
            gnd_finger = LineString([(x_gnd - p.cap_length + rf, y), (x_end, y)]
                                    ).buffer(rf, cap_style=1, resolution=ARC_RES
                                             ).intersection(box(x_gnd - p.cap_length - 1, y - 1,
                                                                x_end, y + 1))

    # --- meandering inductor: centre line, then buffer to ind_width ---
    pts = [(x_col - p.ind_width, m.y_bottom)]
    y = m.y_bottom
    for i in range(m.n_runs - 1):
        going_right = (i % 2 == 0)
        xt = m.x_right if going_right else m.x_left
        pts.append((xt, y))
        yc, s = y + m.radius, (1 if going_right else -1)
        for a in np.linspace(-np.pi / 2, np.pi / 2, 2 * MEANDER_RES + 1)[1:]:
            pts.append((xt + s * m.radius * np.cos(a), yc + m.radius * np.sin(a)))
        y += p.ind_pitch
    pts.append((x_gnd + p.gnd_overlap, y))
    inductor = LineString(pts).buffer(p.ind_width / 2, cap_style=2,
                                      join_style=2, mitre_limit=2.0)

    return unary_union([column, *col_fingers, inductor, gnd_finger])


def pocket_bounds(p):
    """(left, bottom, right, top) of the etched pocket, in local coordinates."""
    m = meander_layout(p)
    return (p.t_x - p.col_width / 2 - p.pocket_margin_left,
            m.y_bottom - p.ind_width / 2 - p.pocket_margin_bottom,
            pocket_right(p), p.pocket_top)


def ground_bounds(p):
    """(left, bottom, right, top) of the ground plane, in local coordinates."""
    x0, y0, x1, y1 = pocket_bounds(p)
    g = p.ground_margin
    return (x0 - g, y0 - g, x1 + g, y1 + g)


def _pocket(p):
    x0, y0, x1, y1 = pocket_bounds(p)
    r = p.pocket_radius
    return box(x0 + r, y0 + r, x1 - r, y1 - r).buffer(r, resolution=ARC_RES)


class GroundPlane(QComponent):
    """Ground plane with the pocket as a hole, merged with everything that is
    connected to ground (top squid, resonator) into ONE polygon."""

    default_options = Dict(OPTIONS)
    component_metadata = Dict(short_name="gnd", _qgeometry_table_poly="True")

    def make(self):
        p = self.p
        frame = box(*ground_bounds(p)).difference(_pocket(p))
        ground = unary_union([frame, _top_squid(p), _resonator(p)])
        if ground.geom_type != "Polygon":
            raise ValueError("ground did not merge into one polygon: some part "
                             "of the top squid or resonator does not reach the ground")
        m = p.inner_ground_margin
        if m <= 0:
            self.add_qgeometry("poly", {"ground": _place(self, [ground])[0]}, layer=p.layer)
            return
        x0, y0, x1, y1 = pocket_bounds(p)
        inner_box = box(x0 - m, y0 - m, x1 + m, y1 + m)
        parts = dict(inner=ground.intersection(inner_box), border=ground.difference(inner_box))
        for name, g in parts.items():
            if g.geom_type != "Polygon":
                raise ValueError(f"ground '{name}' is a {g.geom_type}, not one polygon: "
                                 "check inner_ground_margin against ground_margin")
        self.add_qgeometry("poly", dict(zip(parts, _place(self, list(parts.values())))),
                           layer=p.layer)


# --------------------------------------------------------------------------
# Design parameters: the original's dimensions rounded to 0.1 um, with the
# right SQUID cell an exact copy of the left and 1 um junction gaps.
# --------------------------------------------------------------------------
DEFAULTS = Dict(
    # placement of the local origin in the original GDS (the chip is sized and
    # centred to the ground plane automatically)
    pos_x="-1606.1um",
    pos_y="-251.1um",
    **OPTIONS,
)


def build_design(**overrides):
    """Build the mergemon layout: `ground` (ground plane + top squid +
    resonator, one polygon) and `bottom_squid` (the floating island).

    Args:
        **overrides: any key of DEFAULTS, e.g.
            build_design(cap_length='250um', ind_length='15mm').

    Returns:
        (design, names) where `names` maps role -> component / qgeometry name,
        plus the meander layout that ind_length produced.
    """
    p = Dict(DEFAULTS)
    unknown = set(overrides) - set(p)
    if unknown:
        raise KeyError(f"unknown design parameter(s): {sorted(unknown)}. "
                       f"Valid keys: {sorted(p)}")
    p.update(overrides)

    design = designs.DesignPlanar()
    design.overwrite_enabled = True

    opts = {k: p[k] for k in OPTIONS}
    for cls, name in ((GroundPlane, "ground"), (BottomSquid, "bottom_squid")):
        cls(design, name, options=Dict(pos_x=p.pos_x, pos_y=p.pos_y, **opts))
    design.rebuild()

    # Chip = the ground rectangle, so renderers don't add ground beyond it.
    pp = design.components["ground"].p
    x0, y0, x1, y1 = ground_bounds(pp)
    chip = design.chips.main.size
    chip.center_x = f"{pp.pos_x + (x0 + x1) / 2}mm"
    chip.center_y = f"{pp.pos_y + (y0 + y1) / 2}mm"
    chip.size_x = f"{x1 - x0}mm"
    chip.size_y = f"{y1 - y0}mm"

    pocket = draw.translate(_pocket(pp), pp.pos_x, pp.pos_y)
    return design, Dict(ground="ground", bottom_squid="bottom_squid",
                        junctions=["jj_left", "jj_right"],
                        layer=int(p.layer), meander=meander_layout(pp),
                        pocket_right=pocket_right(pp), pocket_bounds=pocket_bounds(pp),
                        ground_bounds=ground_bounds(pp), pocket=pocket)


# --------------------------------------------------------------------------
# Figure: python mergemon_design.py -> mergemon_layout.png
# --------------------------------------------------------------------------
def _add_shape(ax, geom, **kw):
    """Draw a shapely (Multi)Polygon, holes included."""
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path as MplPath

    polys = getattr(geom, "geoms", [geom])
    for g in polys:
        verts, codes = [], []
        for ring in [g.exterior, *g.interiors]:
            xy = np.asarray(ring.coords)
            verts += xy.tolist()
            codes += [MplPath.MOVETO] + [MplPath.LINETO] * (len(xy) - 2) + [MplPath.CLOSEPOLY]
        ax.add_patch(PathPatch(MplPath(verts, codes), **kw))


def plot_layout(path=None):
    """Circuit layout, a zoom into the coupling capacitor (with the energy-box
    footprint of mergemon_energy_boxes.py) and the final SPR table."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from shapely import affinity

    import mergemon_energy_boxes as eb
    import mergemon_spr_calculation as calc

    design, names = build_design()
    pp = design.components["ground"].p
    ox, oy = pp.pos_x * 1e3, pp.pos_y * 1e3          # local origin, um

    def local_um(g):                                  # design units (mm) -> local um
        return affinity.translate(affinity.scale(g, 1e3, 1e3, origin=(0, 0)), -ox, -oy)

    poly = design.qgeometry.tables["poly"]
    shapes = {r["name"]: local_um(r["geometry"]) for _, r in poly.iterrows()}
    jjs = [local_um(r["geometry"]) for _, r in design.qgeometry.tables["junction"].iterrows()]
    island = shapes.pop("base")                       # bottom_squid: the floating island

    metal, isl, si = "#c9a227", "#d9822b", "#e8e8e8"
    fig, axd = plt.subplot_mosaic([["full", "zoom"], ["full", "table"]], figsize=(14, 10),
                                  gridspec_kw={"width_ratios": [1, 1.15], "height_ratios": [1.5, 1]})

    x0, y0, x1, y1 = (v * 1e3 for v in pocket_bounds(pp))   # pp is in mm
    for key in ("full", "zoom"):
        ax = axd[key]
        ax.set_facecolor(si)
        for g in shapes.values():
            _add_shape(ax, g, fc=metal, ec="k", lw=0.3)
        _add_shape(ax, island, fc=isl, ec="k", lw=0.3)
        for j in jjs:
            xs, ys = j.xy
            ax.plot(xs, ys, color="r", lw=2.5 if key == "zoom" else 1.5, solid_capstyle="butt")
        ax.set_aspect("equal")
        ax.set_xlabel("x (um)")
        ax.set_ylabel("y (um)")

    ax = axd["full"]
    m = 25
    ax.set_xlim(x0 - m, x1 + m)
    ax.set_ylim(y0 - m, y1 + m)
    zx0, zx1, zy0, zy1 = -22, 30, -50, 24
    ax.add_patch(Rectangle((zx0, zy0), zx1 - zx0, zy1 - zy0, fc="none", ec="#1f5fbf", lw=1.2))
    ax.annotate("coupling capacitor\n+ SQUIDs (zoom)", (zx1, (zy0 + zy1) / 2), (55, -10),
                fontsize=8, color="#1f5fbf", arrowprops=dict(arrowstyle="->", color="#1f5fbf"))
    ax.annotate("finger capacitor (C)", (240, pp.cap_top_y * 1e3), (215, -15),
                fontsize=8, ha="center", arrowprops=dict(arrowstyle="->", lw=0.6))
    ax.annotate("meander inductor (L)", (150, (y0 + pp.ind_top_y * 1e3) / 2), (150, (y0 + pp.ind_top_y * 1e3) / 2),
                fontsize=8, ha="center", bbox=dict(fc="white", ec="none", alpha=0.8))
    ax.set_title("mergemon circuit (etched pocket; gold = ground + resonator, orange = island,\n"
                 "red = junctions)", fontsize=9)

    ax = axd["zoom"]
    ax.set_xlim(zx0, zx1)
    ax.set_ylim(zy0, zy1)
    bx0, bx1 = pp.t_x * 1e3 - eb.BOX_HALF_WIDTH, pp.t_x * 1e3 + eb.BOX_HALF_WIDTH
    by0, by1 = eb.COL_TAPER_TOP, eb.T_BAR_TOP
    ax.add_patch(Rectangle((bx0, by0), bx1 - bx0, by1 - by0, fc="#1f5fbf", alpha=0.12,
                           ec="#1f5fbf", lw=1.2, ls="--", label="energy-box footprint"))
    ax.annotate("T coupler (island)", (pp.t_x * 1e3, -20), (16, -14), fontsize=8,
                arrowprops=dict(arrowstyle="->", lw=0.6))
    ax.annotate("lanes (resonator)", (pp.t_x * 1e3 - 4.5, -30), (-20, -46), fontsize=8,
                arrowprops=dict(arrowstyle="->", lw=0.6))
    ax.annotate("junctions", (jjs[1].centroid.x, jjs[1].centroid.y), (17, 13), fontsize=8,
                color="r", arrowprops=dict(arrowstyle="->", lw=0.6, color="r"))
    ax.legend(loc="lower right", fontsize=8)
    ax.set_title(f"coupling capacitor: {pp.t_stem_width * 1e3:g} um stem, {pp.cc_gap * 1e3:g} um gap, "
                 f"{pp.cc_lane_width * 1e3:g} um lanes", fontsize=9)

    # --- table of the final values --------------------------------------
    f, p = calc.weighted_spr()
    ax = axd["table"]
    ax.axis("off")
    cells = [[f"{calc.SPR_2D[k]:.2e}", f"{f * 100:.2f} %", f"{p[k]:.2e}"] for k in ("MS", "MA", "SA")]
    tb = ax.table(cellText=cells, rowLabels=["p_MS", "p_MA", "p_SA"], loc="upper center", cellLoc="center",
                  colLabels=["2D cross-section", "x 3D energy in\ncoupling capacitor", "final (3D)"])
    tb.auto_set_font_size(False)
    tb.set_fontsize(9)
    tb.scale(1.0, 2.4)
    for (r, c), cell in tb.get_celld().items():
        if r == 0:
            cell.set_text_props(weight="bold")
            cell.set_height(cell.get_height() * 1.3)
        if c == 2 and r > 0:
            cell.set_text_props(weight="bold")
            cell.set_facecolor("#fff4d6")
    ax.set_title("Surface participation ratios (mergemon_spr_calculation.py)", fontsize=9)

    fig.tight_layout()
    if path:
        fig.savefig(path, dpi=150, bbox_inches="tight")
    return fig


if __name__ == "__main__":
    from pathlib import Path

    d, n = build_design()
    print(d.qgeometry.tables["poly"][["component", "name", "layer"]])
    m = n.meander
    print(f"meander: {m.n_runs} runs of {m.run * 1e3:.1f} um")
    out = Path(__file__).with_name("mergemon_layout.png")
    plot_layout(out)
    print(f"saved {out}")
