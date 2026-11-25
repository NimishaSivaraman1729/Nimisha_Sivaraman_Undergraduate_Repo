"""Tune pad_height until |alpha| hits a target. Secant search, not stepping.

    python search_pad_height.py                      # target |alpha| = 220 +- 10 MHz
    python search_pad_height.py --target 200 --tol 5

Keeps pad_width, pocket_width and Lj fixed and moves ONLY pad_height, because
height grows pad area without changing the pad-to-pad facing edge -- so it
trims E_C (and with it |alpha|) without making an already 6:1 pad any wider.

Why secant rather than fixed steps: each point is a ~3 minute eigenmode solve,
so guessing increments wastes solves. With two measured points the next height
comes from

    h_next = h1 + (target - a1) * (h1 - h0) / (a1 - a0)

which lands within tolerance in one or two solves once the local slope is
known. The slope is NOT constant -- capacitance grows sublinearly with pad
size, and the measured d|alpha|/dw already softened from 0.57 to 0.43 MHz/um
over the width sweep -- so the secant is re-fitted from the two most recent
points every iteration rather than trusting an old slope.

Every point is cached by sweep_pad_width.solve_point, so an interrupted search
resumes for free and already-solved heights cost nothing.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))

import sweep_pad_width as swp                                  # noqa: E402

OUT = HERE / "output"


def cached(width, pocket, lj, height):
    """Result for this exact configuration, if it has already been solved.

    Height 100 um is the design default, so it was solved before --pad-height
    existed and carries no _ph tag in its filename.
    """
    tag = f"_pk{pocket:g}"
    if lj != 12.0:
        tag += f"_Lj{lj:g}"
    names = [f"transmon_eigen_pw{width:g}um{tag}_ph{height:g}.json"]
    if height == 100.0:
        names.append(f"transmon_eigen_pw{width:g}um{tag}.json")
    for n in names:
        f = OUT / n
        if f.is_file():
            return json.loads(f.read_text())
    return None


def measure(width, pocket, lj, height):
    hit = cached(width, pocket, lj, height)
    if hit is not None:
        print(f"  pad_height {height:g}um: cached, alpha = {hit['alpha_MHz']:.1f} MHz")
        return hit
    swp.LJ_NH = lj
    return swp.solve_point(width, pocket, height)


def main():
    ap = argparse.ArgumentParser(description="Secant-search pad_height for a target |alpha|.")
    ap.add_argument("--target", type=float, default=220.0, help="|alpha| in MHz")
    ap.add_argument("--tol", type=float, default=10.0, help="MHz")
    ap.add_argument("--width", type=float, default=620.0)
    ap.add_argument("--pocket", type=float, default=815.0)
    ap.add_argument("--lj", type=float, default=10.0)
    ap.add_argument("--seeds", default="100,120", help="starting heights, um")
    ap.add_argument("--max-iter", type=int, default=2,
                    help="new solves per run; each is ~3 min, and the cache "
                         "makes re-running free")
    ap.add_argument("--max-step", type=float, default=80.0,
                    help="cap on a single secant jump, um")
    args = ap.parse_args()

    target = -abs(args.target)          # alpha is negative
    print("=" * 74)
    print(f"searching pad_height for |alpha| = {args.target:g} +- {args.tol:g} MHz")
    print(f"fixed: pad_width {args.width:g}um, pocket {args.pocket:g}um, "
          f"Lj {args.lj:g}nH")
    print("=" * 74)

    pts = []        # (height, alpha)
    for h in [float(v) for v in args.seeds.split(",") if v.strip()]:
        r = measure(args.width, args.pocket, args.lj, h)
        pts.append((h, r["alpha_MHz"]))

    # Pocket height is fixed, so the pad stack cannot grow without limit.
    gap = float(str(swp.DEFAULTS["pad_gap"]).rstrip("um"))
    pocket_h = float(str(swp.DEFAULTS["pocket_height"]).rstrip("um"))
    h_max = (pocket_h - gap) / 2.0 - 20.0        # keep >= 20 um clearance

    for it in range(args.max_iter):
        pts.sort(key=lambda t: t[0])
        best = min(pts, key=lambda t: abs(t[1] - target))
        if abs(best[1] - target) <= args.tol:
            break
        (h0, a0), (h1, a1) = pts[-2], pts[-1]
        if a1 == a0:
            print("  slope is zero -- cannot step")
            break
        slope = (a1 - a0) / (h1 - h0)
        step = (target - a1) / slope
        step = max(-args.max_step, min(args.max_step, step))
        h_next = round(h1 + step, 0)
        if h_next > h_max:
            print(f"  secant wants {h_next:g}um but the pocket caps it at "
                  f"{h_max:g}um -- clamping")
            h_next = h_max
        if any(abs(h_next - h) < 1 for h, _ in pts):
            print(f"  {h_next:g}um already measured; stopping")
            break
        print(f"\n  slope {slope:+.4f} MHz/um  ->  next pad_height {h_next:g}um")
        r = measure(args.width, args.pocket, args.lj, h_next)
        pts.append((h_next, r["alpha_MHz"]))

    pts.sort(key=lambda t: t[0])
    print("\n" + "=" * 74)
    print(f"{'pad_height':>11} {'alpha (MHz)':>13} {'|err| vs target':>17}")
    for h, a in pts:
        print(f"{h:>9.0f}um {a:>13.1f} {abs(abs(a)-args.target):>16.1f}")
    best_h, best_a = min(pts, key=lambda t: abs(t[1] - target))
    hit = abs(best_a - target) <= args.tol
    print(f"\nbest: pad_height {best_h:g}um -> alpha = {best_a:.1f} MHz   "
          f"{'WITHIN' if hit else 'OUTSIDE'} {args.tol:g} MHz of "
          f"{-args.target:g} MHz")
    if hit:
        r = cached(args.width, args.pocket, args.lj, best_h)
        if r:
            print(f"      f01 = {r['f01_GHz']:.4f} GHz   "
                  f"EC = {r['EC_GHz']*1e3:.1f} MHz   "
                  f"EJ/EC = {r['EJ_over_EC']:.1f}   p = {r['p_junction']:.4f}")
    else:
        print("      re-run to continue the search (measured points are cached)")
    print("=" * 74)


if __name__ == "__main__":
    main()
