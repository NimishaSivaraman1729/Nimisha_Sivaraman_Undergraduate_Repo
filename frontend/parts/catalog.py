"""Part catalog: which geometries exist for each role, and what they own.

A design is a choice of one variant per ROLE. Each variant says which knobs it
owns (with defaults), which conductor its metal joins, and which circuit
quantity it mainly sets (documentation for now; the circuit-model layer will
use it later). To add an alternative geometry (roadmap step 3), write a builder
in geometry.py and register a new variant under the same role.

Builders return a PartShapes: metal for the `ground` conductor (ground plane,
top squid, resonator) and/or the `island` conductor (bottom squid + coupler),
plus any junction lines. Island parts are built first, so ground parts can
keep clearance from the island (see geometry.column_lanes). Lines to the
outside (feedline, flux line) add `cut` (keepouts carved from the ground),
`feed` (a third conductor) and `terms` (50 ohm termination sheets).

OPTIONAL_ROLES are not in DEFAULT_PARTS: a design has them only when asked
for (parts=dict(feedline="met181")), so every point solved without them keeps
its identity. A role set to "none" is the same as leaving it out.
"""

from dataclasses import dataclass, field
from typing import Callable, Dict

from . import geometry as g


@dataclass
class PartShapes:
    ground: list = field(default_factory=list)
    island: list = field(default_factory=list)
    junctions: dict = field(default_factory=dict)
    feed: list = field(default_factory=list)       # feedline conductor (its own)
    cut: list = field(default_factory=list)        # removed from the ground conductor
    terms: dict = field(default_factory=dict)      # {name: (box, axis)}: 50 ohm sheets


@dataclass
class Part:
    role: str
    variant: str
    builder: Callable            # (p, ctx) -> PartShapes
    knobs: Dict[str, str]        # owned knobs and their defaults (unit strings)
    sets: str                    # circuit quantity it mainly sets (doc)
    build_first: bool = False    # island parts: built before ground parts


def _squid(p, ctx):
    bottom, jj = g.squid_bottom(p)
    return PartShapes(ground=[g.squid_top(p)], island=[bottom], junctions=jj)


SQUID_KNOBS = dict(
    cell_pitch="18.7um",            # right finger axis - left finger axis
    # finger (top squid); x offsets are from each cell's finger axis
    finger_width="0.8um",
    flare_start="14.9um",           # where the R arcs leave the finger
    flare_radius="4.5um",
    block_width="5.5um",            # centred on the finger
    block_top="20.5um",             # reaches up through the pocket into ground
    # top bar + extension
    top_bar_bottom="9um",
    top_bar_height="2.8um",
    top_bar_right="3.6um",
    ext_width="2.7um",
    ext_height="1.2um",             # centred on the top bar
    # junction
    junction_x="-9.8um",            # left edge of tab and extension
    jj_gap="1um",                   # tab top -> extension underside: the lumped RLC
    tab_width="1.2um",              # also the RLC sheet width
    # bottom squid
    arm_width="2.8um",              # centred on the tab
    arm_top="8.4um",                # where the tab starts
    pad_x="-10.9um",
    pad_width="3.4um",
    pad_top="1.9um",
    bar_height="2.3um",             # spans left pad's left edge -> right pad's right edge
)

T_KNOBS = dict(
    t_x="0um",                      # stem axis, from the left finger axis
    t_bar_top="-0.4um",             # below the SQUID bar top: the T overlaps it
    t_bar_width="13um",             # rounded ends, radius t_bar_height / 2
    t_bar_height="3um",
    t_stem_width="3um",             # rounded tip, radius t_stem_width / 2
    t_length="34um",                # stem: T-bar underside -> tip   <-- C_g
    t_fillet="3.3um",
)

COLUMN_KNOBS = dict(
    cc_gap="1.5um",                 # coupler -> column/lanes, all round
    cc_lane_top="-5.4um",
    cc_lane_width="3um",
    col_width="15um",               # column, centred on t_x
    col_taper_bottom="-49um",
    col_taper_top="-41um",
)

CAP_KNOBS = dict(
    cap_length="302.5um",           # finger length                 <-- C_r
    cap_finger_width="2.5um",
    cap_finger_gap="3.8um",
    cap_end_gap="2.5um",            # finger tip -> opposite side
    cap_top_y="-54.2um",            # centre line of the top (column-side) finger
)

MEANDER_KNOBS = dict(
    ind_length="19.3mm",            # total wire length             <-- L_r
    ind_width="2um",
    ind_pitch="6.62um",             # row to row; turns are semicircles of radius pitch/2
    ind_top_y="-76.4um",            # centre line of the top run (it ends in ground)
    ind_margin="18um",              # turn centres -> column edge / pocket edge
)

# Same meander, but its runs fill a fixed span instead of the pocket, so
# cap_length no longer reshapes it: a shorter cap leaves pocket and meander
# alone, a longer one gets a pocket tongue around the fingers only
# (geometry.pocket). 269 um = the span at the nominal cap_length
# (302.5 + 2.5 end gap - 2 x 18 margin): same geometry there.
MEANDER_FIXED_KNOBS = dict(MEANDER_KNOBS, ind_span="269um")

GROUND_KNOBS = dict(
    ground_margin="100um",          # ground plane extends this far past the pocket
    pocket_top="17.6um",
    pocket_margin_left="44.7um",    # column's left edge -> pocket edge
    pocket_margin_bottom="44.5um",  # column's foot -> pocket edge
    pocket_radius="40um",
    gnd_overlap="1um",              # ground-side ends run this far into the ground
)

# MET_ver_1_81.gds, left-most device, measured from the layout (local frame).
FEEDLINE_KNOBS = dict(
    feed_length="1526.8um",         # straight CPW, pocket wall -> 50 ohm end (GDS: to its bend)
    feed_y="-128.9um",              # CPW centre line
    feed_width="35.3um",            # centre conductor
    feed_gap="3.7um",
    feed_pad_length="184.4um",      # coupling finger, top fixed       <-- C_kappa -> kappa
    feed_pad_top="-12.1um",
    feed_pad_width="15um",
    feed_pad_gap="7um",             # finger -> column
    feed_term_gap="3.7um",          # open end filled by the 50 ohm sheet
    feed_term_ground="50um",        # ground beyond the end (simulation region)
)

# The whole feedline of the same device (met181_full): the finger and CPW as
# above, then on past the bend, as in the GDS, to its wirebond launchpad at the
# chip edge (checks/feedline_route.py). The 50 ohm sheet fills the etch behind
# the pad, so there is no feed_length / feed_term_gap.
FEEDLINE_FULL_KNOBS = dict(
    {k: v for k, v in FEEDLINE_KNOBS.items() if k not in ("feed_length", "feed_term_gap")},
    feed_bend_radius="160um",       # both bends, centre line
    feed_drop_x="-1733.9um",        # vertical run's centre line
    feed_run2_y="-1748.9um",        # lower run's centre line
    feed_taper_x="-2492.9um",       # lower run ends, taper to the pad starts
    feed_taper_length="201.5um",
    feed_lp_length="397.6um",       # launchpad (centre conductor)
    feed_lp_width="300um",
    feed_lp_gap="200um",            # pad -> ground, each side
    feed_lp_back_gap="201.9um",     # pad end -> chip edge: the 50 ohm sheet
)

FLUX_KNOBS = dict(
    flux_length="813.2um",          # gaps' lower edge -> 50 ohm end (GDS: to its bend)
    flux_x="12.1um",                # CPW axis
    flux_bottom="22.9um",           # gaps' lower edge: 5.3 um above the pocket top
    flux_fork_height="32.5um",      # the gaps flare over this height ...
    flux_fork_reach="23.15um",      # ... out to this distance from the axis
    flux_fork_foot="5.7um",         # vertical outer edge at the bottom of the flare
    flux_tip_width="4um",           # centre conductor at the fork (shorted below it)
    flux_tip_gap="3.3um",
    flux_taper_length="395.7um",    # tip -> full CPW
    flux_width="35.3um",
    flux_gap="3.7um",
    flux_term_gap="3.7um",
    flux_term_ground="50um",
)

CATALOG = {
    "squid": {
        "dc_squid": Part("squid", "dc_squid", _squid, SQUID_KNOBS,
                         "two lumped junctions (L_J, C_J) in parallel: E_J; island C", True),
    },
    "coupler": {
        "t": Part("coupler", "t", lambda p, ctx: PartShapes(island=[g.t_coupler(p)]),
                  T_KNOBS, "C_g (qubit-resonator coupling) -> g, chi", True),
    },
    "column": {
        "lanes": Part("column", "lanes",
                      lambda p, ctx: PartShapes(ground=[g.column_lanes(p, ctx["island"])]),
                      COLUMN_KNOBS, "other plate of C_g; part of C_r"),
    },
    "res_cap": {
        "three_finger": Part("res_cap", "three_finger",
                             lambda p, ctx: PartShapes(ground=list(g.finger_cap(p))),
                             CAP_KNOBS, "C_r (resonator capacitance to ground)"),
    },
    "inductor": {
        # Old layout: runs fill the pocket, which follows cap_length. Kept so
        # the points solved with it (e.g. configs/star_5knobs.py) stay valid.
        "meander": Part("inductor", "meander",
                        lambda p, ctx: PartShapes(ground=[g.meander(p)]),
                        MEANDER_KNOBS, "L_r (geometric; + kinetic later)"),
        "meander_fixed": Part("inductor", "meander_fixed",
                              lambda p, ctx: PartShapes(ground=[g.meander(p)]),
                              MEANDER_FIXED_KNOBS,
                              "L_r, layout independent of cap_length"),
    },
    "ground": {
        "pocket": Part("ground", "pocket",
                       lambda p, ctx: PartShapes(ground=[g.ground_frame(p)]),
                       GROUND_KNOBS, "ground plane + etched pocket"),
    },
    # optional roles (not in DEFAULT_PARTS)
    "feedline": {
        "met181": Part("feedline", "met181", lambda p, ctx: PartShapes(**g.feedline(p)),
                       FEEDLINE_KNOBS,
                       "C_kappa: readout feedline + coupling finger -> kappa (50 ohm end)"),
        "met181_full": Part("feedline", "met181_full",
                            lambda p, ctx: PartShapes(**g.feedline(p)), FEEDLINE_FULL_KNOBS,
                            "C_kappa; the whole feedline out to its launchpad, 50 ohm "
                            "behind the pad -> kappa"),
    },
    "flux_line": {
        "met181": Part("flux_line", "met181", lambda p, ctx: PartShapes(**g.flux_line(p)),
                       FLUX_KNOBS, "flux bias line, shorted above the SQUID (50 ohm end)"),
    },
}

DEFAULT_PARTS = dict(squid="dc_squid", coupler="t", column="lanes",
                     res_cap="three_finger", inductor="meander_fixed", ground="pocket")
LEGACY_PARTS = dict(DEFAULT_PARTS, inductor="meander")   # before 2026-11-04
OPTIONAL_ROLES = ("feedline", "flux_line")


def full_choice(parts=None):
    """{role: variant}: DEFAULT_PARTS + `parts`, optional roles set to "none"
    (or None) dropped. Everything that hashes or builds a design uses this."""
    return {r: v for r, v in dict(DEFAULT_PARTS, **(parts or {})).items()
            if v not in (None, "none")}


def expected_conductors(parts=None):
    """Ground + island, + the feedline's own conductor if chosen."""
    return 2 + ("feedline" in full_choice(parts))


def chosen(parts=None):
    """The Part objects for a choice {role: variant} (defaults filled in)."""
    choice = full_choice(parts)
    unknown_roles = set(choice) - set(CATALOG)
    if unknown_roles:
        raise KeyError(f"unknown role(s) {sorted(unknown_roles)}; roles: {sorted(CATALOG)}")
    out = []
    for role, variant in choice.items():
        if variant not in CATALOG[role]:
            raise KeyError(f"no variant {variant!r} for role {role!r}; "
                           f"available: {sorted(CATALOG[role])}")
        out.append(CATALOG[role][variant])
    return out


def default_knobs(parts=None):
    """All knobs (with defaults) owned by the chosen parts."""
    knobs = {}
    for part in chosen(parts):
        clash = set(knobs) & set(part.knobs)
        if clash:
            raise ValueError(f"knob(s) {sorted(clash)} owned by two parts")
        knobs.update(part.knobs)
    return knobs
