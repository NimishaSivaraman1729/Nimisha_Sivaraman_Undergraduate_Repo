"""Mergemon part geometry: plain shapely, no qiskit-metal QComponent.

Same split as qdg_toolbox (geometry.py = pure shape functions,
components/assembly = the qiskit-metal side), so these can be tested and reused
without qiskit-metal.

Every builder takes `p`, any object with attribute access holding ALL knobs
already parsed to floats in design units (mm) -- e.g. a QComponent's `self.p`.
Shapes are in local coordinates: x from the left SQUID finger's axis, y from
the SQUID bar's top edge. Parts read each other's knobs where their geometry
depends on it (e.g. the meander's top run ends in the pocket edge); that is why
they all receive the full set of the CHOSEN parts' knobs -- and only those:
"ind_span" in p means the fixed-span meander was chosen (see pocket_right).
"""

import numpy as np
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

ARC_RES = 8       # segments per quarter circle
MEANDER_RES = 4   # fewer on the meander's ~140 turns (r = 3.3 um: < 0.1 um error)


class Attr(dict):
    """dict with attribute access (and still JSON/dict friendly)."""
    __getattr__ = dict.__getitem__


# ---------------------------------------------------------------- SQUID ----
def cell_axes(p):
    """The two junction cells: (side, finger-axis x). Right = left shifted."""
    return (("left", 0.0), ("right", p.cell_pitch))


def ext_bottom(p):
    """Underside of the top squid's extension = top of each junction gap."""
    return p.top_bar_bottom + (p.top_bar_height - p.ext_height) / 2


def squid_top(p):
    """Upper half of both cells: finger block (reaches into ground), finger,
    top bar, extension. Joins the ground conductor."""
    hw = p.finger_width / 2
    bar_top = p.top_bar_bottom + p.top_bar_height
    ye = ext_bottom(p)
    cells = []
    for _side, fx in cell_axes(p):
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


def squid_bottom(p):
    """Lower half: bar, two pads, an arm up from each pad ending in a tab.

    Returns (polygon, {junction_name: LineString}); each line spans one
    `jj_gap`, tab top -> extension underside, and is where the lumped RLC goes.
    """
    tab_top = ext_bottom(p) - p.jj_gap
    cells = cell_axes(p)
    parts = [box(cells[0][1] + p.pad_x, -p.bar_height,
                 cells[1][1] + p.pad_x + p.pad_width, 0.0)]
    junctions = {}
    for side, fx in cells:
        jx = fx + p.junction_x
        xc = jx + p.tab_width / 2
        parts += [
            box(fx + p.pad_x, 0, fx + p.pad_x + p.pad_width, p.pad_top),
            box(xc - p.arm_width / 2, p.pad_top, xc + p.arm_width / 2, p.arm_top),
            box(jx, p.arm_top, jx + p.tab_width, tab_top),
        ]
        junctions[f"jj_{side}"] = LineString([(xc, tab_top), (xc, ext_bottom(p))])
    return unary_union(parts), junctions


# ---------------------------------------------------- coupling capacitor ---
def t_coupler(p):
    """The T: rounded bar, stem of length t_length with a rounded tip, concave
    fillets. Hangs below the SQUID bar (joins the island)."""
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


def column_lanes(p, island):
    """The long part of the coupling capacitor: a column (centred on t_x) that
    narrows into two lanes either side of the coupler, with `cc_gap` kept
    round EVERYTHING on the island (so any coupler shape is carved
    correctly). Its foot meets the meander's first run."""
    m = meander_layout(p)
    x, hc = p.t_x, p.col_width / 2
    lane_half = p.t_stem_width / 2 + p.cc_gap + p.cc_lane_width   # lanes' outer edge
    col_bottom = m.y_bottom - p.ind_width / 2
    r = p.cc_lane_width / 2
    return unary_union([
        box(x - hc, col_bottom, x + hc, p.col_taper_bottom),
        Polygon([(x - hc, p.col_taper_bottom), (x + hc, p.col_taper_bottom),
                 (x + lane_half, p.col_taper_top), (x - lane_half, p.col_taper_top)]),
        box(x - lane_half, p.col_taper_top, x + lane_half, p.cc_lane_top - r),
        *[LineString([(x + s * (lane_half - r), p.col_taper_top),
                      (x + s * (lane_half - r), p.cc_lane_top - r)]
                     ).buffer(r, resolution=ARC_RES) for s in (-1, 1)],
    ]).difference(island.buffer(p.cc_gap, resolution=ARC_RES))


# ---------------------------------------------------- resonator capacitor --
def cap_wall(p):
    """x of the ground wall the capacitor needs: finger tips + cap_end_gap."""
    return p.t_x + p.col_width / 2 + p.cap_length + p.cap_end_gap


def meander_wall(p):
    """x of the ground wall the meander's top run ends in.

    "meander" variant (no ind_span): the capacitor's wall, so cap_length also
    reshapes the meander. "meander_fixed": set by ind_span alone."""
    if "ind_span" not in p:
        return cap_wall(p)
    return p.t_x + p.col_width / 2 + 2 * p.ind_margin + p.ind_span


def pocket_right(p):
    """x of the pocket's rightmost edge (see pocket(): with the fixed-span
    meander, a cap longer than the meander's span gets a tongue)."""
    return max(cap_wall(p), meander_wall(p))


def tongue_bottom(p):
    """y of the tongue's lower edge: halfway between the lowest finger's
    underside and the meander top run's upper side."""
    pitch = p.cap_finger_width + p.cap_finger_gap
    finger_low = p.cap_top_y - 2 * pitch - p.cap_finger_width / 2
    return (finger_low + p.ind_top_y + p.ind_width / 2) / 2


def finger_cap(p):
    """Three fingers: top and bottom off the column (cap_length long), middle
    off the ground wall, its tip cap_end_gap from the column -- so cap_length
    sets the overlap (cap_length - cap_end_gap) whatever the pocket width.

    Returns (column_fingers, ground_finger)."""
    pitch = p.cap_finger_width + p.cap_finger_gap
    rf = p.cap_finger_width / 2
    x_col, x_gnd = p.t_x + p.col_width / 2, pocket_right(p)   # tongue wall, if any
    col_fingers, gnd_finger = [], None
    for i in range(3):
        y = p.cap_top_y - i * pitch
        if i % 2 == 0:      # from the column, rounded tip near the ground
            col_fingers.append(LineString([(x_col - rf, y), (x_col + p.cap_length - rf, y)])
                               .buffer(rf, resolution=ARC_RES))
        else:               # from the ground, rounded tip near the column
            x_end = x_gnd + p.gnd_overlap
            gnd_finger = LineString([(x_col + p.cap_end_gap + rf, y), (x_end, y)]
                                    ).buffer(rf, cap_style=1, resolution=ARC_RES
                                             ).intersection(box(x_col, y - 1, x_end, y + 1))
    return unary_union(col_fingers), gnd_finger


# ---------------------------------------------------- meandering inductor --
def meander_layout(p):
    """Rows and run length that give exactly `ind_length` of wire.

    The top run sits at ind_top_y and runs from the left turns into the ground;
    the bottom run comes off the column. Rows are added downward (the column
    grows to meet them); n_runs is odd so the top run ends on the right.
    Runs fill the space up to meander_wall (fixed-span variant: ind_span;
    old variant: the capacitor's wall).
    """
    col_right = p.t_x + p.col_width / 2
    x_left = col_right + p.ind_margin
    run_max = meander_wall(p) - p.ind_margin - x_left
    r = p.ind_pitch / 2
    fixed_top = meander_wall(p) - x_left                     # top run -> ground
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
    return Attr(n_runs=n, run=run, x_left=x_left, x_right=x_left + run, radius=r,
                y_bottom=p.ind_top_y - (n - 1) * p.ind_pitch, col_right=col_right)


def meander(p):
    """The meander wire: from the column's foot, back and forth, into ground."""
    m = meander_layout(p)
    pts = [(m.col_right - p.ind_width, m.y_bottom)]
    y = m.y_bottom
    for i in range(m.n_runs - 1):
        going_right = (i % 2 == 0)
        xt = m.x_right if going_right else m.x_left
        pts.append((xt, y))
        yc, s = y + m.radius, (1 if going_right else -1)
        for a in np.linspace(-np.pi / 2, np.pi / 2, 2 * MEANDER_RES + 1)[1:]:
            pts.append((xt + s * m.radius * np.cos(a), yc + m.radius * np.sin(a)))
        y += p.ind_pitch
    pts.append((meander_wall(p) + p.gnd_overlap, y))
    return LineString(pts).buffer(p.ind_width / 2, cap_style=2, join_style=2, mitre_limit=2.0)


# ------------------------------------------------- feedline and flux line --
# Both copied from MET_ver_1_81.gds, left-most device (local frame: origin at
# (-1606.1, -251.1) um there). Each is a straight CPW cut at its first bend and
# ended in an open gap that a 50 ohm lumped sheet fills (the simulation's
# matched termination: the only loss in the model, so kappa = f_r / Q_r).
# Terminations are (box, axis): axis is the current direction across the gap.
def pocket_wall_left(p):
    return p.t_x - p.col_width / 2 - p.pocket_margin_left


def feed_end_x(p):
    """x where the feedline's centre conductor ends (its 50 ohm gap follows)."""
    return pocket_wall_left(p) - p.feed_length


def feedline(p):
    """Readout feedline: a CPW from the left (centre on feed_y) into the pocket,
    ending in a vertical coupling finger beside the resonator's column; the
    finger's length (feed_pad_length, top fixed) sets C_kappa. Its own
    conductor. Returns dict(feed=[metal], cut=[ground keepout], terms=...)."""
    x_r = p.t_x - p.col_width / 2 - p.feed_pad_gap            # finger's column side
    r = p.feed_pad_width / 2
    xc = x_r - r
    top, bottom = p.feed_pad_top, p.feed_pad_top - p.feed_pad_length
    y, hw = p.feed_y, p.feed_width / 2
    if bottom > y - hw or top < y + hw:
        raise ValueError(f"feed_pad_length {p.feed_pad_length * 1e3:.1f} um: the finger "
                         f"({bottom * 1e3:.1f} .. {top * 1e3:.1f} um) must span the feedline "
                         f"({(y - hw) * 1e3:.1f} .. {(y + hw) * 1e3:.1f} um)")
    finger = LineString([(xc, bottom + r), (xc, top - r)]).buffer(r, resolution=ARC_RES)
    if "feed_lp_length" in p:                   # met181_full: on to the launchpad
        return _feedline_full(p, finger, xc, pocket_wall_left(p))
    x_end, x_wall = feed_end_x(p), pocket_wall_left(p)
    centre = box(x_end, y - hw, xc, y + hw)
    keepout = box(x_end - p.feed_term_gap, y - hw - p.feed_gap,
                  x_wall + p.gnd_overlap, y + hw + p.feed_gap)
    term = box(x_end - p.feed_term_gap, y - hw, x_end, y + hw)
    return dict(feed=[unary_union([finger, centre])], cut=[keepout],
                terms={"feedline": (term, "x")})


BEND_RES = 16     # segments per quarter on the feedline's r = 160 um bends (< 0.2 um)


def feed_launch(p):
    """Key x positions of the full feedline's end (met181_full): the taper's
    start, the pad's inner and outer ends, and the chip edge behind the pad."""
    x_taper = p.feed_taper_x
    x_pad = x_taper - p.feed_taper_length
    x_pad_end = x_pad - p.feed_lp_length
    return x_taper, x_pad, x_pad_end, x_pad_end - p.feed_lp_back_gap


def _feed_route(p, x_start):
    """Centre line of the full feedline from x_start (on feed_y) to the taper:
    left, a bend down, the vertical run at feed_drop_x, a bend left, the lower
    run at feed_run2_y."""
    R, xd, y1, y2 = p.feed_bend_radius, p.feed_drop_x, p.feed_y, p.feed_run2_y

    def arc(cx, cy, a0, a1):
        a = np.linspace(a0, a1, BEND_RES + 1)
        return list(zip(cx + R * np.cos(a), cy + R * np.sin(a)))

    pts = [(x_start, y1)] + arc(xd + R, y1 - R, np.pi / 2, np.pi) \
        + arc(xd - R, y2 + R, 0.0, -np.pi / 2) + [(feed_launch(p)[0], y2)]
    return LineString(pts)


def _feedline_full(p, finger, xc, x_wall):
    """The whole MET v1.81 feedline: the coupling finger, the CPW with its two
    bends, a taper and the wirebond launchpad. In the GDS the etch behind the
    pad runs out to the chip edge; here the 50 ohm sheet fills it (pad end ->
    chip edge) and ground continues past the edge for the sheet to land on
    (ground_bounds), as the transmon tutorial terminates its launchpads."""
    hw, g, y2 = p.feed_width / 2, p.feed_gap, p.feed_run2_y
    wp, gp = p.feed_lp_width / 2, p.feed_lp_gap
    x_taper, x_pad, x_pad_end, x_edge = feed_launch(p)
    line = _feed_route(p, xc).buffer(hw, cap_style=2, join_style=2, mitre_limit=2.0)
    taper = Polygon([(x_taper, y2 - hw), (x_taper, y2 + hw), (x_pad, y2 + wp), (x_pad, y2 - wp)])
    pad = box(x_pad_end, y2 - wp, x_pad, y2 + wp)
    gaps = _feed_route(p, x_wall + p.gnd_overlap).buffer(hw + g, cap_style=2, join_style=2,
                                                         mitre_limit=2.0)
    taper_gap = Polygon([(x_taper, y2 - hw - g), (x_taper, y2 + hw + g),
                         (x_pad, y2 + wp + gp), (x_pad, y2 - wp - gp)])
    launch_gap = box(x_edge, y2 - wp - gp, x_pad, y2 + wp + gp)
    term = box(x_edge, y2 - wp, x_pad_end, y2 + wp)
    return dict(feed=[unary_union([finger, line, taper, pad])],
                cut=[unary_union([gaps, taper_gap, launch_gap])],
                terms={"feedline": (term, "x")})


def flux_end_y(p):
    return p.flux_bottom + p.flux_length


def flux_line(p):
    """Flux line: a CPW down onto the SQUID, tapering to a 4 um centre that is
    shorted to ground just above the SQUID loop through a fork (the two gaps
    flare out). Its centre conductor is ground; only the gaps are cut, plus
    the open end the 50 ohm sheet fills. Returns dict(cut=[...], terms=...)."""
    cx, yb = p.flux_x, p.flux_bottom
    yf = yb + p.flux_fork_height                # top of the fork
    yt = yf + p.flux_taper_length               # end of the taper
    ye = flux_end_y(p)
    if ye <= yt:
        raise ValueError(f"flux_length {p.flux_length * 1e3:.1f} um ends inside the fork + taper "
                         f"({(yt - yb) * 1e3:.1f} um)")
    tw, tg, w, g = p.flux_tip_width / 2, p.flux_tip_gap, p.flux_width / 2, p.flux_gap
    gaps = []
    for s in (-1, 1):                           # left (s=-1) and right gap
        X = lambda dx, s=s: cx + s * dx         # noqa: E731
        fork = Polygon([(X(tw), yb), (X(p.flux_fork_reach), yb),
                        (X(p.flux_fork_reach), yb + p.flux_fork_foot),
                        (X(tw + tg), yf), (X(tw), yf)])
        taper = Polygon([(X(tw), yf), (X(tw + tg), yf), (X(w + g), yt), (X(w), yt)])
        straight = box(min(X(w), X(w + g)), yt, max(X(w), X(w + g)), ye)
        gaps += [fork, taper, straight]
    end = box(cx - w - g, ye, cx + w + g, ye + p.flux_term_gap)
    term = box(cx - w, ye, cx + w, ye + p.flux_term_gap)
    return dict(cut=[unary_union(gaps + [end])], terms={"flux_line": (term, "y")})


# ---------------------------------------------------------------- ground ---
def pocket_bounds(p):
    """(left, bottom, right, top) of the etched pocket (tongue included)."""
    m = meander_layout(p)
    return (p.t_x - p.col_width / 2 - p.pocket_margin_left,
            m.y_bottom - p.ind_width / 2 - p.pocket_margin_bottom,
            pocket_right(p), p.pocket_top)


def ground_bounds(p):
    """(left, bottom, right, top) of the ground plane: pocket + ground_margin,
    stretched to hold a chosen feedline / flux line out to its termination
    plus *_term_ground of ground beyond it ("feed_length" / "flux_length" in p
    means the part was chosen)."""
    x0, y0, x1, y1 = pocket_bounds(p)
    g = p.ground_margin
    x0, y0, x1, y1 = x0 - g, y0 - g, x1 + g, y1 + g
    if "feed_length" in p:
        x0 = min(x0, feed_end_x(p) - p.feed_term_gap - p.feed_term_ground)
    if "feed_lp_length" in p:                   # full feedline: past the chip edge
        x0 = min(x0, feed_launch(p)[3] - p.feed_term_ground)
        y0 = min(y0, p.feed_run2_y - p.feed_lp_width / 2 - p.feed_lp_gap - p.feed_term_ground)
    if "flux_length" in p:
        y1 = max(y1, flux_end_y(p) + p.flux_term_gap + p.flux_term_ground)
    return (x0, y0, x1, y1)


def _rounded_box(x0, y0, x1, y1, r):
    r = min(r, (x1 - x0) / 2 * 0.999, (y1 - y0) / 2 * 0.999)
    return box(x0 + r, y0 + r, x1 - r, y1 - r).buffer(r, resolution=ARC_RES)


def pocket(p):
    """Rounded rectangle out to meander_wall. If the capacitor reaches past
    it, a tongue (same top, down to tongue_bottom) carries the fingers out to
    cap_wall -- the meander's surroundings stay as they were."""
    x0, y0, _, y1 = pocket_bounds(p)
    main = _rounded_box(x0, y0, meander_wall(p), y1, p.pocket_radius)
    if cap_wall(p) <= meander_wall(p):
        return main
    # Rounded at the top like the main pocket; cut square at tongue_bottom so
    # the wall stays straight past the lowest finger's tip.
    tongue = _rounded_box(x0, y0, cap_wall(p), y1, p.pocket_radius).intersection(
        box(x0, tongue_bottom(p), cap_wall(p), y1))
    return unary_union([main, tongue])


def ground_frame(p):
    """Ground plane rectangle with the pocket as a hole."""
    return box(*ground_bounds(p)).difference(pocket(p))
