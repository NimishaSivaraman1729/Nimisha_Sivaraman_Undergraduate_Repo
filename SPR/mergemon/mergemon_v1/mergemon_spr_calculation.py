"""
mergemon_spr_calculation.py
===========================

Surface participation of the mergemon coupling capacitor, estimated by
weighting a 2D cross-section SPR with the share of the 3D mode energy that sits
in the coupling capacitor. This reproduces the "3d mergemon simulation" section
of the Mergemon tab in ``spr graphs.xlsx``.

Why not Wang et al. (2015)
--------------------------
Wang et al. split every conductor into an interior region (more than x0 = 1 um
from an edge), resolved in 3D, and an edge band, corrected with a 2D model. The
method assumes x0 is much smaller than the conductor width and the gaps. The
mergemon coupling capacitor's stem is 3 um wide, with 1.5-2 um gaps, so almost
nothing is "interior" (the spreadsheet note: "the interior region is 3um"). This
file uses a cruder estimate that, for this device, agreed with experiment.

Method
------
1. 3D eigenmode (full_mergemon_resonator_design.aedt, HFSSDesign1, Lj = 36 nH,
   Cj = 45 fF per junction): electric energy in the substrate, the vacuum, and
   the junctions (lumped, 1/2 C V^2 with C = 2 x 45 fF).
2. Energy inside four boxes around the coupling capacitor (vacuum above, SOI
   device layer, etched vacuum gap, handle; see mergemon_energy_boxes.py),
   divided by the total -> fraction of the mode energy in the capacitor.
3. 2D electrostatic cross-section of the capacitor (2d_designs.aedt,
   coupling_capacitor) with 3 nm, eps = 10 MA / MS / SA layers meshed at 3 nm:
   p_i,2D = energy in layer i / total 2D energy.
4. p_i = p_i,2D x (capacitor energy fraction).

    python mergemon_spr_calculation.py
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# 1. 3D mode energies (spreadsheet cells C29:C31, E31). Arbitrary eigenmode units.
# ---------------------------------------------------------------------------
U_SUBSTRATE = 1.1471             # C29
U_VACUUM = 0.8336                # C30
C_JUNCTIONS_F = 90e-15           # 2 junctions x Cj = 45 fF
V_JUNCTION = 36284126.0          # E31 'V capacitor' (eigenmode units)

# ---------------------------------------------------------------------------
# 2. Energy in the coupling-capacitor boxes (B38:B41)
# ---------------------------------------------------------------------------
# The sheet halves the first two values (=0.5629/2, =0.9981/2) but not the last
# two. Why is not recorded; kept as written.
BOX_ENERGY = {
    "cc_vac_top": 0.5629 / 2,
    "cc_top_substrate": 0.9981 / 2,
    "cc_vac_below": 0.33,
    "cc_bottom_substrate": 0.0018,
}

# ---------------------------------------------------------------------------
# 3. 2D cross-section SPR of the coupling capacitor (B46:B48)
#    2d_designs.aedt / coupling_capacitor: stem 3 um, two 3 um lanes 2 um away,
#    50 nm metal, 0.22 um Si device layer, 3 um vacuum gap, Si handle.
# ---------------------------------------------------------------------------
SPR_2D = {
    "MA": 0.000513042,
    "MS": 0.0023835,
    "SA": 0.0114127,     # includes both faces of the etched vacuum gap (SA_top, SA_bottom)
}

# Same convention as ../../SPR_tutorial: Wang et al. constrain only
# tan_MS + 1.2 tan_SA + 0.1 tan_MA = 2.6e-3; equal tangents satisfy it.
# ILLUSTRATIVE: replace with your own values before quoting Q or T1.
TAN_SURFACE = 2.6e-3 / 2.3
QUBIT_FREQUENCY_GHZ = 3.66       # f01 from Z:/Nimisha/Mergemon/run_log_epr.txt (pyEPR rebuild)


def mode_energies():
    u_jj = 0.5 * C_JUNCTIONS_F * V_JUNCTION ** 2
    total = U_SUBSTRATE + U_VACUUM + u_jj
    return {"substrate": U_SUBSTRATE, "vacuum": U_VACUUM, "junctions": u_jj, "total": total}


def capacitor_fraction(U_total: float) -> float:
    return sum(BOX_ENERGY.values()) / U_total


def weighted_spr():
    U = mode_energies()
    f = capacitor_fraction(U["total"])
    return f, {k: v * f for k, v in SPR_2D.items()}


# Spreadsheet results, to check the transcription
SHEET = {
    "EPR substrate": 0.01873584084, "EPR vacuum": 0.01361537523, "EPR junctions": 0.9676487839,
    "U total": 61.22490098, "capacitor energy": 1.1123, "capacitor fraction": 0.01816744465,
    "MA": 9.32066214e-06, "MS": 4.330210433e-05, "SA": 0.0002073395956,
}


if __name__ == "__main__":
    U = mode_energies()
    f, p = weighted_spr()
    ours = {
        "EPR substrate": U["substrate"] / U["total"], "EPR vacuum": U["vacuum"] / U["total"],
        "EPR junctions": U["junctions"] / U["total"], "U total": U["total"],
        "capacitor energy": sum(BOX_ENERGY.values()), "capacitor fraction": f, **p,
    }
    print(f"{'quantity':20s} {'sheet':>14s} {'code':>14s}")
    for k, v in SHEET.items():
        ok = math.isclose(v, ours[k], rel_tol=1e-6)
        print(f"{k:20s} {v:14.6e} {ours[k]:14.6e}  {'OK' if ok else 'MISMATCH'}")

    print("\nweighted SPR of the coupling capacitor:")
    inv_q = 0.0
    for k in ("MA", "MS", "SA"):
        q = 1 / (p[k] * TAN_SURFACE)
        inv_q += 1 / q
        print(f"  p_{k} = {p[k]:.3e}   (2D {SPR_2D[k]:.3e} x {f:.4f})   Q_lim = {q:.3e}")
    Q = 1 / inv_q
    T1 = Q / (2 * math.pi * QUBIT_FREQUENCY_GHZ * 1e9) * 1e6
    print(f"  surface-limited Q = {Q:.3e}, T1 = {T1:.0f} us at {QUBIT_FREQUENCY_GHZ} GHz "
          f"(illustrative tan delta = {TAN_SURFACE:.2e})")
