"""
flipmon_spr_calculation.py
==========================

Surface participation (Wang et al., APL 107, 162601 (2015)) for the flipmon,
plus the EPR / capacitance sweeps from the "flipmon" tab of ``spr graphs.xlsx``.

The method is the same as in ../SPR_tutorial/spr_calculation.py:

    U_tot   = 1/2 * int(|E|^2) over vacuum + eps_Si/2 * int(|E|^2) over both silicon chips
    p_MA    = t/(2 eps_MA)            * [ I_MA,interior + F_MA * I_MA,perimeter ] / U_tot
    p_MS    = t eps_Si^2/(2 eps_MS)   * [ I_MS,interior + F_MS * I_MS,perimeter ] / U_tot
    p_SA    = t/(2 eps_SA)            * [ I_SA,interior + F_SA * I_SA,perimeter ] / U_tot
    1/Q     = sum_i p_i tan(delta_i),   T1 = Q / (2 pi f)

(eps0 cancels everywhere and is left out.) Each of MA / MS / SA is computed
separately for the bottom chip (carrier) and the top chip.

The SA prefactor t/(2 eps_SA) is the one written into the Ansys calculator
(1.5e-10 m). It assumes the field at the SA sheet is normal to the surface.
When flipmon_ansys.py also supplies the normal-component integral |E_z|^2, the
tangential part is weighted by t*eps_SA/2 instead (exact for a vacuum-side sheet).

Running this file
-----------------
1. Reproduces the 'calculation flipmon' tab cell by cell (to check the transcription).
2. Recomputes the same inputs with the formulas above, so you can see how much the
   two perimeter terms change (see SHEET_FORMULA_ISSUES).
3. Plots the EPR and capacitance sweeps from data/*.csv -> flipmon_sweeps.png.
4. If flipmon_ansys.py has written data/ansys_field_integrals.json, prints the SPR
   computed from those freshly extracted integrals too.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from flipmon_design import (
    EPS_INTERFACE,
    EPS_SILICON,
    F_FROM_ANSYS,
    F_FROM_SPREADSHEET,
    FLIPMON,
    INTERFACE_THICKNESS_NM,
)

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

@dataclass
class FlipmonIntegrals:
    """Plain integrals of |E|^2 (no 0.5 / eps factors), SI units, from the HFSS Fields Calculator."""
    substrate_volume: float            # carrier + chip                       [V^2 m]
    vacuum_volume: float               # Region (the vacuum gap)              [V^2 m]
    bottom_MA_interior: float          # [V^2]
    bottom_MA_perimeter: float
    bottom_MS_interior: float
    bottom_MS_perimeter: float
    top_MA_interior: float
    top_MA_perimeter: float
    top_MS_interior: float
    top_MS_perimeter: float
    bottom_SA_interior: Optional[float] = None
    bottom_SA_perimeter: Optional[float] = None
    top_SA_interior: Optional[float] = None
    top_SA_perimeter: Optional[float] = None
    # integral of |E_z|^2 (normal component) on each SA sheet, keyed like the SA fields above.
    # When given, the SA layer is treated exactly; otherwise the field is assumed normal.
    SA_perp: Optional[dict] = None
    source: str = ""


# Values typed into the 'calculation flipmon' tab of spr graphs.xlsx
# (cap_width 30, cap_gap 40, vacuum_gap 5, chip_radius 135 um).
SPREADSHEET_INPUTS = FlipmonIntegrals(
    substrate_volume=0.00227,      # B3  'substrate energy'
    vacuum_volume=0.0686,          # B4  'vacuum energy'
    bottom_MS_interior=28.71,      # E3
    bottom_MS_perimeter=7.401,     # E6  ('MS exterior energy bottom')
    bottom_MA_interior=12235.0,    # H3
    bottom_MA_perimeter=155.31,    # H6
    top_MS_interior=16.976,        # E9
    top_MS_perimeter=3.2551,       # E12
    top_MA_interior=12611.0,       # H9
    top_MA_perimeter=125.52,       # H12
    source="spr graphs.xlsx / calculation flipmon",
)

# The sheet's own results (cached cell values), used to check the reproduction.
SPREADSHEET_RESULTS = {
    "total_energy": 0.0478065,                 # B5
    "bottom_MS_interior": 1.275649682e-05,     # E4
    "top_MS_interior": 7.542817483e-06,        # E10
    "bottom_MS_perimeter": 1.94807157e-07,     # E7
    "top_MS_perimeter": 8.567987795e-08,       # E13
    "bottom_MA_interior": 3.838913119e-05,     # H4
    "top_MA_interior": 3.956888708e-05,        # H10
    "bottom_MA_perimeter": 0.000209288149,     # H7
    "top_MA_perimeter": 0.0001691446041,       # H13
    "MS_total": 2.057980133e-05,               # E16
    "MA_total": 0.0004563907713,               # H16
}

SHEET_FORMULA_ISSUES = [
    "MS perimeter (E7, E13) = Fms*t*E/(2*eps*U): the eps_Si^2 factor used for the MS "
    "interior (E4, E10) and in the Ansys coefficient 1.78196e-7 is missing (factor 141.6).",
    "MA perimeter (H7, H13) divides by the vacuum energy B4 instead of the total energy B5.",
    "Fma = 616.28 in the sheet (B21), but the Ansys coefficient 9.169215e-8 / 1.5e-10 = 611.28.",
    "Ansys 'bottom_MS_interior' uses 2.121415e-8; every other MS prefactor is 2.12415e-8.",
]

# ILLUSTRATIVE loss tangents: replace with your own values before quoting Q or T1.
# (Cell D19 of the sheet holds an unlabelled 0.0022, which may have been meant as tan delta.)
LOSS_TANGENTS = {"MA": 2e-3, "MS": 2e-3, "SA": 2e-3, "substrate": 1e-7}


# ---------------------------------------------------------------------------
# Prefactors (eps0 dropped)
# ---------------------------------------------------------------------------

T_M = INTERFACE_THICKNESS_NM * 1e-9
K_MA = T_M / (2 * EPS_INTERFACE)                       # 1.5e-10 m
K_MS = T_M * EPS_SILICON ** 2 / (2 * EPS_INTERFACE)    # 2.12415e-8 m
K_SA = T_M / (2 * EPS_INTERFACE)                       # 1.5e-10 m (as in the Ansys calculator)
K_SA_PAR = T_M * EPS_INTERFACE / 2                     # 1.5e-8 m, tangential field (continuous)


def total_energy(I: FlipmonIntegrals) -> float:
    return 0.5 * I.vacuum_volume + 0.5 * EPS_SILICON * I.substrate_volume


# ---------------------------------------------------------------------------
# 1. The spreadsheet, formula for formula
# ---------------------------------------------------------------------------

def spreadsheet_formulas(I: FlipmonIntegrals, F: dict = F_FROM_SPREADSHEET) -> dict:
    """Exact copy of the 'calculation flipmon' formulas, including the issues listed above."""
    t, es, ec = T_M, EPS_SILICON, EPS_INTERFACE
    U = es / 2 * I.substrate_volume + 0.5 * I.vacuum_volume                  # B5
    r = {"total_energy": U}
    for side in ("bottom", "top"):
        r[f"{side}_MS_interior"] = t * es ** 2 / (2 * ec * U) * getattr(I, f"{side}_MS_interior")
        r[f"{side}_MA_interior"] = t * getattr(I, f"{side}_MA_interior") / (2 * ec * U)
        r[f"{side}_MS_perimeter"] = F["MS"] * t * getattr(I, f"{side}_MS_perimeter") / (2 * ec * U)
        r[f"{side}_MA_perimeter"] = F["MA"] * t * getattr(I, f"{side}_MA_perimeter") / (2 * ec * I.vacuum_volume)
    r["MS_total"] = sum(r[f"{s}_MS_{k}"] for s in ("bottom", "top") for k in ("interior", "perimeter"))
    r["MA_total"] = sum(r[f"{s}_MA_{k}"] for s in ("bottom", "top") for k in ("interior", "perimeter"))
    return r


# ---------------------------------------------------------------------------
# 2. Consistent Wang et al. calculation
# ---------------------------------------------------------------------------

@dataclass
class FlipmonSPR:
    U_total: float
    p: dict
    Q: dict
    Q_total: float
    T1_us: float
    notes: list


def surface_participation(I: FlipmonIntegrals, F: dict = F_FROM_ANSYS,
                          loss_tangents: dict = LOSS_TANGENTS,
                          frequency_GHz: float = FLIPMON.frequency_GHz) -> FlipmonSPR:
    U = total_energy(I)
    k = {"MA": K_MA, "MS": K_MS, "SA": K_SA}
    p, notes = {}, []
    for iface in ("MA", "MS", "SA"):
        for side in ("bottom", "top"):
            a = getattr(I, f"{side}_{iface}_interior")
            b = getattr(I, f"{side}_{iface}_perimeter")
            if a is None:
                continue
            b = b or 0.0
            if iface == "SA" and I.SA_perp:
                # exact: tangential part t*eps/2, normal part t/(2 eps), field sampled in vacuum
                pa, pb = I.SA_perp[f"{side}_SA_interior"], I.SA_perp[f"{side}_SA_perimeter"]
                a = (K_SA_PAR * (a - pa) + K_SA * pa) / K_SA
                b = (K_SA_PAR * (b - pb) + K_SA * pb) / K_SA
            p[f"{side}_{iface}_interior"] = k[iface] * a / U
            p[f"{side}_{iface}_perimeter"] = k[iface] * F[iface] * b / U
            p[f"{side}_{iface}"] = p[f"{side}_{iface}_interior"] + p[f"{side}_{iface}_perimeter"]
        parts = [p[f"{s}_{iface}"] for s in ("bottom", "top") if f"{s}_{iface}" in p]
        if parts:
            p[iface] = sum(parts)
        elif iface == "SA":
            notes.append("No SA integrals given: p_SA left out of the loss budget.")
    p["substrate"] = 0.5 * EPS_SILICON * I.substrate_volume / U
    p["vacuum"] = 0.5 * I.vacuum_volume / U

    Q = {ch: 1 / (p[ch] * loss_tangents[ch]) for ch in ("MA", "MS", "SA", "substrate") if ch in p}
    Q_total = 1 / sum(1 / q for q in Q.values())
    T1_us = Q_total / (2 * math.pi * frequency_GHz * 1e9) * 1e6
    return FlipmonSPR(U, p, Q, Q_total, T1_us, notes)


# ---------------------------------------------------------------------------
# 3. Sweeps (EPR and capacitance)
# ---------------------------------------------------------------------------

def read_csv(name: str) -> list[dict]:
    with open(DATA / name, newline="") as f:
        return [{k: (float(v) if v not in ("", None) else None) for k, v in row.items()}
                for row in csv.DictReader(f)]


def total_capacitance(c1g: float, c2g: float, c12: float) -> float:
    """Capacitance across the junction for two floating pads: C12 + C1g*C2g/(C1g + C2g)."""
    return c12 + c1g * c2g / (c1g + c2g)


def plot_sweeps(path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 3, figsize=(14, 8))
    epr = [("epr_vs_vacuum_gap.csv", "vacuum_gap_um", "vacuum gap (um)"),
           ("epr_vs_chip_radius.csv", "chip_radius_um", "top-chip pad radius (um)"),
           ("epr_vs_cap_gap.csv", "cap_gap_um", "cap gap (um)"),
           ("epr_vs_cap_width.csv", "cap_width_um", "cap width (um)")]
    for ax, (fn, x, xl) in zip(axes.flat[:4], epr):
        rows = read_csv(fn)
        xs = [r[x] for r in rows]
        for key, lab in (("vacuum_epr", "vacuum"), ("top_chip_epr", "top chip (Si)"),
                         ("bottom_chip_epr", "bottom chip (Si)")):
            ax.plot(xs, [r[key] for r in rows], "o-", ms=3, label=lab)
        ax.set_xlabel(xl); ax.set_ylabel("energy participation"); ax.grid(alpha=0.3)
    axes.flat[0].legend(fontsize=8)

    for ax, (fn, x, xl) in zip(axes.flat[4:], [("capacitance_vs_vacuum_gap.csv", "vacuum_gap_um", "vacuum gap (um)"),
                                                ("capacitance_vs_chip_radius.csv", "chip_radius_um", "top-chip pad radius (um)")]):
        rows = read_csv(fn)
        xs = [r[x] for r in rows]
        ax.plot(xs, [total_capacitance(r["C_pad1_ground_fF"], r["C_pad2_ground_fF"], r["C_pad1_pad2_fF"])
                     for r in rows], "o-", ms=3, label="total (across junction)")
        ax.plot(xs, [r["C_pad1_pad2_fF"] for r in rows], "--", label="pad1-pad2")
        ax.plot(xs, [r["C_pad1_ground_fF"] for r in rows], ":", label="pad1-ground")
        ax.plot(xs, [r["C_pad2_ground_fF"] for r in rows], ":", label="pad2-ground")
        ax.set_xlabel(xl); ax.set_ylabel("capacitance (fF)"); ax.grid(alpha=0.3)
    axes.flat[4].legend(fontsize=8)
    fig.suptitle("flipmon sweeps (others fixed at cap_width 30, cap_gap 40, vacuum_gap 5, chip_radius 135 um)",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_spr(r: FlipmonSPR, title: str):
    print(f"\n--- {title} ---")
    print(f"U_total/eps0 = {r.U_total:.6g}")
    for k in ("bottom_MA_interior", "bottom_MA_perimeter", "top_MA_interior", "top_MA_perimeter", "MA",
              "bottom_MS_interior", "bottom_MS_perimeter", "top_MS_interior", "top_MS_perimeter", "MS",
              "bottom_SA", "top_SA", "SA", "substrate", "vacuum"):
        if k in r.p:
            q = f"   Q_lim = {r.Q[k]:.3e}" if k in r.Q else ""
            print(f"  {k:22s} {r.p[k]:.4e}{q}")
    print(f"  Q_total = {r.Q_total:.3e}, T1 = {r.T1_us:.2f} us  (illustrative loss tangents)")
    for n in r.notes:
        print(f"  note: {n}")


if __name__ == "__main__":
    print("1) Reproducing 'calculation flipmon' (spr graphs.xlsx)")
    sheet = spreadsheet_formulas(SPREADSHEET_INPUTS)
    for k, ref in SPREADSHEET_RESULTS.items():
        ok = math.isclose(sheet[k], ref, rel_tol=1e-8)
        print(f"  {k:22s} sheet {ref:.6e}  code {sheet[k]:.6e}  {'OK' if ok else 'MISMATCH'}")

    print("\n   Formula issues found in the sheet:")
    for s in SHEET_FORMULA_ISSUES:
        print("   -", s)

    print("\n2) Same inputs, consistent formulas")
    for label, F in (("F from Ansys calculator (Fma = 611.28)", F_FROM_ANSYS),
                     ("F from spreadsheet (Fma = 616.28)", F_FROM_SPREADSHEET)):
        print_spr(surface_participation(SPREADSHEET_INPUTS, F), label)
    fixed = surface_participation(SPREADSHEET_INPUTS, F_FROM_SPREADSHEET).p
    print("\n   sheet vs consistent (same F):")
    for k in ("bottom_MS_perimeter", "top_MS_perimeter", "bottom_MA_perimeter", "top_MA_perimeter"):
        print(f"  {k:22s} sheet {sheet[k]:.3e} -> {fixed[k]:.3e}")
    print(f"  {'MS total':22s} sheet {sheet['MS_total']:.3e} -> {fixed['MS']:.3e}")
    print(f"  {'MA total':22s} sheet {sheet['MA_total']:.3e} -> {fixed['MA']:.3e}")

    out = HERE / "flipmon_sweeps.png"
    plot_sweeps(out)
    print(f"\n3) sweeps plotted -> {out}")

    js = DATA / "ansys_field_integrals.json"
    if js.exists():
        d = json.loads(js.read_text())
        I = FlipmonIntegrals(**d["integrals"], SA_perp=d.get("SA_perp"), source=d.get("source", str(js)))
        print_spr(surface_participation(I), f"4) from Ansys extraction ({I.source})")
    else:
        print("\n4) no data/ansys_field_integrals.json yet (run flipmon_ansys.py to create it)")
