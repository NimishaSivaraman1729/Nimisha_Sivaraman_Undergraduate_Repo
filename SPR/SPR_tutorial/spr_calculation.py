"""
spr_calculation.py
==================

Surface participation ratios (SPR) and the dielectric-loss limit on Q / T1 for
the transmon designs in ``transmon_design.py``, following

    C. Wang, C. Axline, Y. Y. Gao, T. Brecht, Y. Chu, L. Frunzio, M. H. Devoret,
    and R. J. Schoelkopf, "Surface participation and dielectric loss in
    superconducting qubits", Appl. Phys. Lett. 107, 162601 (2015).

Method (as set up in transmon_spr.aedt)
---------------------------------------
1. HFSS solves the 3D field with the metal as a zero-thickness PEC sheet.
2. The Fields Calculator integrates |E|^2 over
      * the substrate volume and the vacuum volume -> total electric energy
      * sheets 1 nm above / below the metal (MA / MS) and on the bare substrate (SA)
   Each metal sheet is split into region A (interior, > 1 um from the edge) and
   region B (0.5-1 um from the edge).
3. The thin lossy layer is not in the 3D mesh, so the field *inside* it is
   obtained from the boundary conditions at the layer:
      MA  (field taken in vacuum,    normal to metal):  E_MA = E_vac / eps_MA
      MS  (field taken in substrate, normal to metal):  E_MS = eps_sub * E_sub / eps_MS
      SA  (field taken in vacuum just above the substrate):
            E_par is continuous, E_perp,SA = E_perp,vac / eps_SA
   Energy stored in a layer of thickness t:  U_i = (eps0 eps_i t / 2) * integral |E_i|^2 dA
4. Fields within 0.5 um of the edge (region C) are not resolved in 3D, so a 2D
   electrostatic cross-section gives F = (U_B + U_C) / U_B and the 3D region-B
   integral is multiplied by F.
5. p_i = U_i / U_total,   1/Q = sum_i p_i tan(delta_i),   T1 = Q / (2 pi f).

eps0 appears in every energy and cancels in p_i, so it is left out everywhere
(the same as in the Ansys calculator). All integrals use SI units straight
from Ansys: surface integrals in V^2, volume integrals in V^2 m.

Run ``python spr_calculation.py`` to check the prefactors against the
constants hard-coded in the Ansys project and to print results for any design
whose field integrals have been filled in below.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from transmon_design import (
    F_MA_FROM_PROJECT,
    F_MS_FROM_PROJECT,
    HFSS_DESIGNS,
    INTERFACE_LAYERS,
    TransmonDesign,
)


# ---------------------------------------------------------------------------
# Inputs: the |E|^2 integrals read from the HFSS Fields Calculator
# ---------------------------------------------------------------------------

@dataclass
class FieldIntegrals:
    """Integrals of |E|^2 from one HFSS solution (left + right electrodes summed).

    Use the *plain* integral of Mag_E^2. Do not include the 0.5 or eps_r factors;
    this file applies them.
    """
    substrate_volume: float          # integral over Box1                      [V^2 m]
    vacuum_volume: float             # integral over Region                    [V^2 m]
    MA_A: float                      # MA sheet, region A (interior)           [V^2]
    MS_A: float                      # MS sheet, region A (interior)           [V^2]
    MA_B: float = 0.0                # MA sheet, region B (0.5-1 um band)      [V^2]
    MS_B: float = 0.0                # MS sheet, region B (0.5-1 um band)      [V^2]
    # SA sheet 1 nm above the bare substrate: region A (> 1 um from metal) and
    # region B (0.5-1 um from metal). The *_perp values are integrals of |E_z|^2
    # (normal component); with them the SA layer is treated exactly, without them
    # the field is taken as all tangential (an upper bound).
    SA_A: Optional[float] = None
    SA_B: Optional[float] = None
    SA_A_perp: Optional[float] = None
    SA_B_perp: Optional[float] = None
    source: str = ""                 # where the numbers came from (sheet, date, mesh pass...)


# Fill these in from the Fields Calculator (or the SPR spreadsheet).
# The keys must match design names in transmon_design.HFSS_DESIGNS.
# Integrals extracted by transmon_ansys.py (data/ansys_<design>_field_integrals.json)
# are added automatically below, unless a design is already entered here by hand.
FIELD_INTEGRALS: dict[str, FieldIntegrals] = {
    # "designC": FieldIntegrals(
    #     substrate_volume=...,   # silicon_energy
    #     vacuum_volume=...,      # vacuum_energy
    #     MA_A=...,               # MA_energy_total
    #     MS_A=...,               # MS_total_energy
    #     MA_B=...,               # MA_perimeter_energy
    #     MS_B=...,               # MS_B_energy
    #     source="transmon_spr.aedt / designC / Setup1 : LastAdaptive",
    # ),
}


def load_ansys_integrals(folder: Path = Path(__file__).resolve().parent / "data") -> dict:
    """Read every data/ansys_<design>_field_integrals.json written by transmon_ansys.py."""
    found = {}
    for js in sorted(folder.glob("ansys_*_field_integrals.json")):
        d = json.loads(js.read_text())
        I = dict(d["integrals"])
        if "SA_total" in I:                      # older extraction: A and B summed, no edge split
            I["SA_A"], I["SA_A_perp"] = I.pop("SA_total"), I.pop("SA_perp")
            I.pop("SA_par", None)
        found[d["design"]] = FieldIntegrals(**I, source=d.get("source", js.name))
    return found


for _name, _I in load_ansys_integrals().items():
    FIELD_INTEGRALS.setdefault(_name, _I)


def load_edge_factors(path: Path = Path(__file__).resolve().parent / "data" / "ansys_edge2d_factors.json") -> dict:
    """Edge factors F. MS / MA default to the values used in transmon_spr.aedt; F_SA (never
    computed in the original project) comes from `transmon_ansys.py edge2d` if it has been run."""
    F = {"MS": F_MS_FROM_PROJECT, "MA": F_MA_FROM_PROJECT, "SA": None, "source": "transmon_spr.aedt"}
    if path.exists():
        d = json.loads(path.read_text())
        F["SA"] = d["F_SA"]
        F["rerun"] = {"MS": d["F_MS"], "MA": d["F_MA"], "SA": d["F_SA"]}
        F["source"] += f" (MS, MA) + {d.get('source', path.name)} (SA)"
    return F


EDGE_FACTORS = load_edge_factors()


# ---------------------------------------------------------------------------
# Loss tangents
# ---------------------------------------------------------------------------
# Wang et al. only constrain a combination of the surface loss tangents (main text):
#     tan_MS + 1.2 tan_SA + 0.1 tan_MA = (2.6 +- 0.1) e-3
# (each alone, as an upper bound: tan_MS 2.6e-3, tan_SA 2.2e-3, tan_MA 2.1e-2).
# Default here: all three equal, which satisfies that constraint -> 2.6e-3 / 2.3.
# Bulk: "tan delta < 1e-6 for bulk sapphire and silicon"; 1e-7 is an assumption.
# Replace with your own values before quoting Q or T1.
_TAN_SURFACE = 2.6e-3 / (1 + 1.2 + 0.1)
LOSS_TANGENTS = {
    "MA": _TAN_SURFACE,
    "MS": _TAN_SURFACE,
    "SA": _TAN_SURFACE,
    "substrate": 1e-7,
}


# ---------------------------------------------------------------------------
# Step 1: prefactors that turn an |E|^2 integral into a layer energy
# ---------------------------------------------------------------------------

def prefactor_MA(t_m: float, eps_MA: float) -> float:
    """Metal-air: field sampled in vacuum, normal to the metal.

    U_MA / eps0 = (eps_MA t / 2) * |E_vac / eps_MA|^2 = t / (2 eps_MA) * |E_vac|^2
    """
    return t_m / (2.0 * eps_MA)


def prefactor_MS(t_m: float, eps_MS: float, eps_sub: float) -> float:
    """Metal-substrate: field sampled in the substrate, normal to the metal.

    U_MS / eps0 = (eps_MS t / 2) * |eps_sub E_sub / eps_MS|^2 = t eps_sub^2 / (2 eps_MS) * |E_sub|^2
    """
    return t_m * eps_sub ** 2 / (2.0 * eps_MS)


def prefactors_SA(t_m: float, eps_SA: float) -> tuple[float, float]:
    """Substrate-air, field sampled in vacuum: (tangential, normal) prefactors.

    tangential:  (eps_SA t / 2) |E_par|^2
    normal:      (eps_SA t / 2) |E_perp / eps_SA|^2 = t / (2 eps_SA) |E_perp|^2
    """
    return t_m * eps_SA / 2.0, t_m / (2.0 * eps_SA)


def total_energy(I: FieldIntegrals, eps_sub: float) -> float:
    """U_total / eps0 = 1/2 * integral(eps_r |E|^2) over vacuum and substrate.

    Matches 'total_energy' in designC: 0.5 * vacuum_energy + 5.95 * silicon_energy.
    """
    return 0.5 * I.vacuum_volume + 0.5 * eps_sub * I.substrate_volume


# ---------------------------------------------------------------------------
# Step 2: participation ratios
# ---------------------------------------------------------------------------

@dataclass
class SPRResult:
    design: str
    U_total: float
    p: dict          # participation ratios
    Q: dict          # Q limit from each loss channel
    Q_total: float
    T1_us: float
    notes: list


def surface_participation(design: TransmonDesign, I: FieldIntegrals,
                          F_MA: float = None, F_MS: float = None, F_SA: float = None,
                          loss_tangents: dict = LOSS_TANGENTS) -> SPRResult:
    eps_sub = design.substrate.eps_r
    ma, ms, sa = INTERFACE_LAYERS["MA"], INTERFACE_LAYERS["MS"], INTERFACE_LAYERS["SA"]
    notes = []
    F_MA = EDGE_FACTORS["MA"] if F_MA is None else F_MA
    F_MS = EDGE_FACTORS["MS"] if F_MS is None else F_MS
    F_SA = EDGE_FACTORS["SA"] if F_SA is None else F_SA

    U = total_energy(I, eps_sub)

    k_MA = prefactor_MA(ma.thickness_nm * 1e-9, ma.eps_r)
    k_MS = prefactor_MS(ms.thickness_nm * 1e-9, ms.eps_r, eps_sub)

    p = {
        "MA_interior": k_MA * I.MA_A / U,
        "MA_perimeter": k_MA * F_MA * I.MA_B / U,
        "MS_interior": k_MS * I.MS_A / U,
        "MS_perimeter": k_MS * F_MS * I.MS_B / U,
    }
    if I.MA_B == 0.0 or I.MS_B == 0.0:
        notes.append("No region-B integral given: the edge contribution (usually the "
                     "largest part of p_MA) is missing.")
    p["MA"] = p["MA_interior"] + p["MA_perimeter"]
    p["MS"] = p["MS_interior"] + p["MS_perimeter"]

    k_SA_par, k_SA_perp = prefactors_SA(sa.thickness_nm * 1e-9, sa.eps_r)

    def sa_energy(total, perp):
        if total is None:
            return 0.0
        if perp is None:
            return k_SA_par * total                       # all tangential: upper bound
        return k_SA_par * (total - perp) + k_SA_perp * perp

    if I.SA_A is None and I.SA_B is None:
        notes.append("No SA integral given: p_SA left out of the loss budget.")
    else:
        if I.SA_A_perp is None and I.SA_A is not None:
            notes.append("SA from |E|^2 only: field treated as fully tangential (upper bound).")
        if I.SA_B is not None and F_SA is None:
            notes.append("No F_SA available (run `transmon_ansys.py edge2d`): SA region B used "
                         "without edge correction, so p_SA is underestimated.")
        p["SA_interior"] = sa_energy(I.SA_A, I.SA_A_perp) / U
        p["SA_perimeter"] = (F_SA or 1.0) * sa_energy(I.SA_B, I.SA_B_perp) / U
        p["SA"] = p["SA_interior"] + p["SA_perimeter"]

    p["substrate"] = 0.5 * eps_sub * I.substrate_volume / U
    p["vacuum"] = 0.5 * I.vacuum_volume / U

    # Step 3: dielectric loss  1/Q = sum p_i tan(delta_i)
    Q = {}
    for ch in ("MA", "MS", "SA", "substrate"):
        if ch in p and loss_tangents.get(ch, 0) > 0:
            Q[ch] = 1.0 / (p[ch] * loss_tangents[ch])
    Q_total = 1.0 / sum(1.0 / q for q in Q.values())
    T1_us = Q_total / (2 * math.pi * design.frequency_GHz * 1e9) * 1e6

    return SPRResult(design.name, U, p, Q, Q_total, T1_us, notes)


# ---------------------------------------------------------------------------
# Consistency check against the constants typed into the Ansys project
# ---------------------------------------------------------------------------

# designC's Fields Calculator expressions, with the prefactors written in as numbers
ANSYS_CONSTANTS = {
    "pMA_interior": 1.5e-10,
    "pMS_interior": 2.12415e-08,
    "pMA_perimeter": 2.498325e-08,
    "pMS_perimeter": 1.88856e-07,
    "silicon weight in total_energy": 5.95,
}


def check_against_ansys(design: TransmonDesign) -> list[tuple[str, float, float, bool]]:
    eps_sub = design.substrate.eps_r
    ma, ms = INTERFACE_LAYERS["MA"], INTERFACE_LAYERS["MS"]
    k_MA = prefactor_MA(ma.thickness_nm * 1e-9, ma.eps_r)
    k_MS = prefactor_MS(ms.thickness_nm * 1e-9, ms.eps_r, eps_sub)
    ours = {
        "pMA_interior": k_MA,
        "pMS_interior": k_MS,
        "pMA_perimeter": k_MA * F_MA_FROM_PROJECT,
        "pMS_perimeter": k_MS * F_MS_FROM_PROJECT,
        "silicon weight in total_energy": 0.5 * eps_sub,
    }
    return [(k, ANSYS_CONSTANTS[k], ours[k], math.isclose(ANSYS_CONSTANTS[k], ours[k], rel_tol=1e-9))
            for k in ANSYS_CONSTANTS]


# ---------------------------------------------------------------------------
# Command-line report
# ---------------------------------------------------------------------------

def print_result(r: SPRResult, loss_tangents: dict = LOSS_TANGENTS):
    print(f"\n=== {r.design} ===")
    print(f"U_total / eps0 = {r.U_total:.4e}")
    print(f"{'channel':14s} {'p':>11s} {'tan d':>9s} {'Q limit':>11s}")
    for k in ("MA_interior", "MA_perimeter", "MA", "MS_interior", "MS_perimeter", "MS",
              "SA_interior", "SA_perimeter", "SA", "substrate", "vacuum"):
        if k not in r.p:
            continue
        tan = loss_tangents.get(k, "")
        q = r.Q.get(k, "")
        print(f"{k:14s} {r.p[k]:11.3e} {tan if tan == '' else f'{tan:9.1e}':>9} "
              f"{q if q == '' else f'{q:11.3e}':>11}")
    print(f"Q_total = {r.Q_total:.3e}   T1 = {r.T1_us:.1f} us")
    for n in r.notes:
        print(f"  note: {n}")


if __name__ == "__main__":
    print("Prefactor check against transmon_spr.aedt / designC Fields Calculator")
    print(f"{'quantity':32s} {'Ansys':>12s} {'this code':>12s}")
    for name, ansys, ours, ok in check_against_ansys(HFSS_DESIGNS["designC"]):
        print(f"{name:32s} {ansys:12.6e} {ours:12.6e}  {'OK' if ok else 'MISMATCH'}")
    print(f"\nEdge factors used: F_MS = {EDGE_FACTORS['MS']:.4f}, F_MA = {EDGE_FACTORS['MA']:.3f}, "
          f"F_SA = {EDGE_FACTORS['SA'] if EDGE_FACTORS['SA'] is None else round(EDGE_FACTORS['SA'], 3)}"
          f"  [{EDGE_FACTORS['source']}]")
    if "rerun" in EDGE_FACTORS:
        r = EDGE_FACTORS["rerun"]
        print(f"2D model rerun (transmon_ansys.py edge2d): F_MS = {r['MS']:.4f}, F_MA = {r['MA']:.3f}, "
              f"F_SA = {r['SA']:.3f}")

    if not FIELD_INTEGRALS:
        print("\nNo field integrals filled in yet: add them to FIELD_INTEGRALS to get p_i, Q and T1.")
    for name, integrals in FIELD_INTEGRALS.items():
        print_result(surface_participation(HFSS_DESIGNS[name], integrals))
