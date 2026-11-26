"""
underetch_calculation.py
========================

Surface participation at the mergemon junction against the underetch radius,
from the 2D cross-section in ``underetch_design.py``. The numbers are the
"2d junction simulation" section of the Mergemon tab in ``spr graphs.xlsx``
(data/underetch_spr.csv), evaluated in Maxwell 2D as

    p_i = Energy(layer i) / Energy(all objects)

    python underetch_calculation.py      # table + underetch_spr.png
"""

from __future__ import annotations

import csv
from pathlib import Path

from underetch_design import UnderetchCrossSection

HERE = Path(__file__).resolve().parent


def load(path: Path = HERE / "data" / "underetch_spr.csv") -> list[dict]:
    with open(path, newline="") as f:
        return [{k: float(v) for k, v in row.items()} for row in csv.DictReader(f)]


def plot(rows, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    r = [x["etch_radius_nm"] for x in rows]
    fig, ax = plt.subplots(figsize=(6, 4))
    for key, lab in (("MA_spr", "MA"), ("MS_spr", "MS"), ("SA_spr", "SA")):
        ys = [max(x[key], 1e-9) for x in rows]
        ax.semilogy(r, ys, "o-", label=lab)
    ax.axvline(UnderetchCrossSection().edge_x * 1e3, color="gray", ls=":", lw=1)
    ax.text(UnderetchCrossSection().edge_x * 1e3 + 3, 2e-6, "pit reaches\njunction centre", fontsize=7)
    ax.set_xlabel("etch radius (nm)")
    ax.set_ylabel("participation (2D, junction cross-section)")
    ax.grid(alpha=0.3, which="both")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=150)


if __name__ == "__main__":
    rows = load()
    print(f"{'etch r (nm)':>11s} {'p_MS':>10s} {'p_MA':>10s} {'p_SA':>10s} {'junction':>9s} {'MS width (nm)':>13s}")
    for x in rows:
        w = UnderetchCrossSection(etch_radius_um=x["etch_radius_nm"] / 1000).ms_width_um * 1e3
        print(f"{x['etch_radius_nm']:11.0f} {x['MS_spr']:10.2e} {x['MA_spr']:10.3e} {x['SA_spr']:10.2e} "
              f"{x['junction_epr']:9.4f} {w:13.0f}")
    plot(rows, HERE / "underetch_spr.png")
    print(f"saved {HERE / 'underetch_spr.png'}")
