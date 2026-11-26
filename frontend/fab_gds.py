"""Fab GDS of one solved mergemon point, the MicrowaveDesign (qdg_toolbox) way.

The device is one QDGComponent in a QDGDesign, and QDGDesign.write_gds does
the export, so the file follows that toolbox's layer convention:

    layer  0  chip outline (= the simulated ground rectangle: chip = device)
    layer  1  metal inside the pocket: resonator (capacitor + meander), column,
              top SQUID, and the floating island (bottom SQUID + T coupler)
    layer 99  keepout: the etched pocket (subtract=True)
    layer 98  ground plane = chip - keepouts (write_gds derives it)
    layer 50  ANNOTATION, not fab: the junction gaps, tab_width x jj_gap each,
              where HFSS puts the lumped L_J / C_J (L_J = 36 nH per junction)

Metal = layer 1 + layer 98, which is checked to be exactly the two conductors
HFSS solved. The SQUID is drawn as simulated (no junction overlap / layer 2-5
fab geometry): the junctions themselves are for whoever prepares fab.

qdg_toolbox is imported read only (not installed) from the MicrowaveDesign repo
at site_config.MICROWAVE_DESIGN. Needs gdspy + qiskit-metal: deliver.py runs it
where gdspy is (site_config.geometry_python: this env, or GEOMETRY_ENV).

    python -B frontend/fab_gds.py <record.json> <out.gds>
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True          # nothing written into MicrowaveDesign
sys.path.insert(0, HERE)
from site_config import MICROWAVE_DESIGN  # noqa: E402

if not os.path.isdir(os.path.join(MICROWAVE_DESIGN, "qdg_toolbox")):
    sys.exit(f"MicrowaveDesign not found at {MICROWAVE_DESIGN!r}: set MICROWAVE_DESIGN in "
             "local_settings.json (see site_config.py)")
sys.path.insert(1, MICROWAVE_DESIGN)

from assembly import all_knobs, assemble_all, build_design, part_knobs  # noqa: E402
from parts import geometry as g  # noqa: E402
from parts.catalog import DEFAULT_PARTS, expected_conductors, full_choice  # noqa: E402
from qdg_toolbox import QDGDesign  # noqa: E402
from qdg_toolbox.base import QDGComponent  # noqa: E402
from qiskit_metal import Dict  # noqa: E402
from shapely.affinity import scale  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

METAL, KEEPOUT, GROUND, CHIP, JJ_ANNOTATION = 1, 99, 98, 0, 50


def _polys(geom):
    """The polygons of a geometry; drops the zero-area lines an intersection
    leaves where two shapes only share an edge (ground frame vs pocket)."""
    return [q for q in getattr(geom, "geoms", [geom])
            if q.geom_type == "Polygon" and q.area > 0]


class MergemonDevice(QDGComponent):
    """The mergemon as fab layers: metal (layer) inside the keepouts (the
    pocket + any feedline / flux-line gaps), plus the junction gaps on an
    annotation layer. The lines end where the simulation terminates them."""
    default_options = Dict(all_knobs(), parts=Dict(DEFAULT_PARTS), jj_layer=str(JJ_ANNOTATION))
    component_metadata = Dict(short_name="mgm", _qgeometry_table_poly="True")

    def make(self):
        p = self.p
        parts = {k: str(v) for k, v in dict(self.options.parts).items()}
        a = assemble_all(p, parts)
        keep = g.pocket(part_knobs(p, parts))
        if a.cut is not None:
            keep = unary_union([keep, a.cut])
        metal = _polys(unary_union([a.ground.intersection(keep), a.island]
                                   + ([a.feed] if a.feed is not None else [])))
        keeps = _polys(keep)
        names = sorted(a.junctions)
        jj = [a.junctions[n].buffer(p.tab_width / 2, cap_style=2) for n in names]
        placed = self.rearrange_polygons(metal + keeps + jj)
        metal, keeps, jj = (placed[:len(metal)], placed[len(metal):len(metal) + len(keeps)],
                            placed[len(metal) + len(keeps):])
        self.add_qgeometry("poly", {f"metal_{i}": q for i, q in enumerate(metal)}, layer=p.layer)
        self.add_qgeometry("poly", {f"keepout_{i}": q for i, q in enumerate(keeps)},
                           subtract=True, layer=p.keepout_layer)
        self.add_qgeometry("poly", dict(zip(names, jj)), layer=p.jj_layer)


def build(knobs, parts=None):
    """QDGDesign with the device; chip = the simulated ground rectangle."""
    choice = full_choice(parts)
    opts = dict(all_knobs(choice), **knobs)
    design = QDGDesign()
    design.overwrite_enabled = True
    MergemonDevice(design, "mergemon", options=Dict(opts, parts=Dict(choice)))
    pp = design.components["mergemon"].p
    x0, y0, x1, y1 = g.ground_bounds(part_knobs(pp, choice))
    chip = design.chips.main.size
    chip.center_x = f"{pp.pos_x + (x0 + x1) / 2}mm"
    chip.center_y = f"{pp.pos_y + (y0 + y1) / 2}mm"
    chip.size_x = f"{x1 - x0}mm"
    chip.size_y = f"{y1 - y0}mm"
    return design


def read_layers(path):
    import gdspy
    from shapely.geometry import Polygon
    lib = gdspy.GdsLibrary(infile=str(path), units="convert")          # -> um
    out = {}
    for cell in lib.top_level():
        for (lay, _dt), arr in cell.get_polygons(by_spec=True).items():
            out.setdefault(lay, []).extend(Polygon(q).buffer(0) for q in arr)
    return out


def verify(path, knobs, parts=None):
    """Metal (1 + 98) == the solved conductors (2, or 3 with a feedline); each
    junction mark bridges exactly two of them (island and ground)."""
    from gds_check import max_deviation
    lay = read_layers(path)
    metal = unary_union(lay.get(METAL, []) + lay.get(GROUND, []))
    conductors = sorted(_polys(metal), key=lambda q: -q.area)
    sim, _ = build_design(parts=parts, **knobs)
    t = sim.qgeometry.tables["poly"]
    solved = unary_union([scale(q, 1000, 1000, origin=(0, 0)) for q in t.geometry])
    jj = lay.get(JJ_ANNOTATION, [])
    bridges = bool(jj) and all(sum(j.intersects(c) for c in conductors) == 2 for j in jj)
    chip = unary_union(lay.get(CHIP, []))
    return dict(layers=sorted(int(k) for k in lay), n_conductors=len(conductors),
                expected_conductors=expected_conductors(parts),
                xor_um2=float(metal.symmetric_difference(solved).area),
                max_deviation_um=float(max_deviation(metal, solved)),
                n_junction_marks=len(jj), junction_marks_bridge=bool(bridges),
                inside_chip=bool(metal.difference(chip.buffer(1e-3)).is_empty),
                chip_um=[round(chip.bounds[2] - chip.bounds[0], 3),
                         round(chip.bounds[3] - chip.bounds[1], 3)])


def preview(path, png):
    """Full device + SQUID zoom, from the written GDS."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    lay = read_layers(path)
    ground = unary_union(lay.get(GROUND, []))
    metal = unary_union(lay.get(METAL, []))
    jj = lay.get(JJ_ANNOTATION, [])
    fig, axes = plt.subplots(1, 2, figsize=(11, 6.2), gridspec_kw={"width_ratios": [1, 1.1]})
    sq = unary_union(jj).bounds
    for ax, zoom in zip(axes, (None, (sq[0] - 22, sq[2] + 22, sq[1] - 28, sq[3] + 26))):
        for geom, fc in ((ground, "#9fb4cc"), (metal, "#2a78d6")):
            for q in _polys(geom):
                ax.fill(*q.exterior.xy, fc=fc, ec="none")
                for h in q.interiors:
                    ax.fill(*h.xy, fc="#fcfcfb", ec="none")
        for q in jj:
            ax.fill(*q.exterior.xy, fc="#e34948", ec="none")
        if zoom:
            ax.set_xlim(zoom[:2])
            ax.set_ylim(zoom[2:])
        ax.set_aspect("equal")
        ax.set_facecolor("#fcfcfb")
        ax.tick_params(labelsize=8, colors="#52514e")
        for s in ax.spines.values():
            s.set_color("#d9d8d4")
        ax.set_xlabel("x (um)", fontsize=8, color="#52514e")
    axes[0].set_title("device (chip = simulated ground rectangle)", fontsize=10, color="#0b0b0b")
    axes[1].set_title("SQUID + T coupler; red = junction gaps (layer 50, annotation)",
                      fontsize=10, color="#0b0b0b")
    fig.patch.set_facecolor("#fcfcfb")
    plt.tight_layout()
    plt.savefig(png, dpi=110, facecolor=fig.get_facecolor())
    plt.close(fig)


if __name__ == "__main__":
    rec_path, gds_path = sys.argv[1], sys.argv[2]
    with open(rec_path, encoding="utf-8") as fh:
        rec = json.load(fh)
    knobs, parts = rec["knobs"], rec.get("parts")
    design = build(knobs, parts)
    out_dir, name = os.path.split(os.path.abspath(gds_path))
    design.write_gds(out_dir, name)
    check = verify(gds_path, knobs, parts)
    png = os.path.splitext(gds_path)[0] + "_layout.png"
    preview(gds_path, png)
    print("GDS ->", gds_path)
    print("layout ->", png)
    print("VERIFY " + json.dumps(check))
