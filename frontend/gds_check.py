"""GDS helpers behind the geometry checks: export a design to a temporary GDS
and read it back, to count its conductors (sweep.check_geometry, run before
every solve) and to measure how far the fab GDS sits from the solved geometry
(fab_gds.verify). Writes no GDS of its own; needs gdspy (site_config.GEOMETRY_ENV).
"""

import gdspy
from qiskit_metal import Dict
from shapely.geometry import Polygon
from shapely.ops import unary_union


def export_gds(design, path):
    gds = design.renderers.gds
    # ground_plane stays at its default 'True' ('False' writes nothing in this
    # qiskit-metal version). Nothing here subtracts, so no automatic ground
    # polygon is added and the two conductors export exactly as drawn.
    gds.options["cheese"]["view_in_file"] = Dict(main={1: False})
    gds.options["no_cheese"]["view_in_file"] = Dict(main={1: False})
    gds.export_to_gds(str(path))
    return path


def gds_polys(path, layers):
    lib = gdspy.GdsLibrary(infile=str(path), units="convert")   # um
    # buffer(0): a polygon with a hole is stored as one self-touching outline.
    return [Polygon(p).buffer(0) for cell in lib.top_level()
            for (lay, _dt), arr in cell.get_polygons(by_spec=True).items()
            if lay in layers for p in arr]


def max_deviation(a, b, hi=50.0):
    """Thickness (um) of the thickest sliver where a and b disagree: how far
    any edge of one sits from the other. Binary search on erosion of the XOR."""
    x = a.symmetric_difference(b)
    if x.is_empty:
        return 0.0
    lo = 0.0
    for _ in range(20):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if not x.buffer(-mid / 2).is_empty else (lo, mid)
    return lo


def conductors(path, layer):
    metal = unary_union(gds_polys(path, [layer]))
    return sorted(getattr(metal, "geoms", [metal]), key=lambda q: -q.area)
