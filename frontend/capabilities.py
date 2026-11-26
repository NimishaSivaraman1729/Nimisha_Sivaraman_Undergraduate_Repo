"""What this computer can do with the front end. Standard library only; nothing
is imported, so it is fast and safe to call from the server.

    python capabilities.py          # prints the report for this interpreter

The front end runs anywhere with numpy + shapely (design screen, checks,
results). Simulating (Optimize, sweeps, previews in AEDT) also needs
qiskit-metal, pyEPR, pywin32 and a registered AEDT; the fab GDS needs gdspy and
the group's MicrowaveDesign repo. Features whose pieces are missing are switched
off with a message saying what to install, instead of failing mid-run.
"""

from __future__ import annotations

import importlib.util
import os
import sys

MODULES = {
    "numpy": "pip install numpy",
    "shapely": "pip install shapely (>= 2.0)",
    "pandas": "pip install pandas",
    "matplotlib": "pip install matplotlib",
    "qiskit_metal": "pip install quantum-metal",
    "pyEPR": "pip install pyEPR-quantum (not 'pyEPR')",
    "win32com": "pip install pywin32",
    "gdspy": "conda install -c conda-forge gdspy",
}

FEATURES = {
    "design": dict(label="design screen + checks", needs=["numpy", "shapely"]),
    "simulate": dict(label="Optimize / sweeps / AEDT previews",
                     needs=["numpy", "shapely", "pandas", "matplotlib", "qiskit_metal", "pyEPR",
                            "win32com", "aedt"]),
    "plan": dict(label="dry runs + sweep tables", needs=["numpy", "shapely", "pandas", "qiskit_metal"]),
    "fab_gds": dict(label="fab GDS on delivery", needs=["gdspy", "qiskit_metal", "microwave_design"]),
}


def has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def aedt_installed() -> bool:
    """Ansys Electronics Desktop registered for COM (what pyEPR connects to)."""
    if any(k.upper().startswith("ANSYSEM_ROOT") for k in os.environ):
        return True
    if sys.platform != "win32":
        return False
    import winreg
    for key in ("Ansoft.ElectronicsDesktop", "AnsoftHfss.HfssScriptInterface"):
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, key))
            return True
        except OSError:
            continue
    return False


def microwave_design_found() -> bool:
    try:
        from site_config import MICROWAVE_DESIGN
    except Exception:                                   # noqa: BLE001
        return False
    return bool(MICROWAVE_DESIGN) and os.path.isdir(os.path.join(MICROWAVE_DESIGN, "qdg_toolbox"))


_HINTS = dict(MODULES, aedt="install + license Ansys Electronics Desktop (Windows)",
              microwave_design="set MICROWAVE_DESIGN to the group's MicrowaveDesign repo")
_CACHE = None


def report(refresh: bool = False) -> dict:
    """{have: {piece: bool}, features: {name: {ok, label, missing: [..], hint}}}."""
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    have = {m: has_module(m) for m in MODULES}
    have["aedt"] = aedt_installed()
    have["microwave_design"] = microwave_design_found()
    feats = {}
    for name, f in FEATURES.items():
        missing = [p for p in f["needs"] if not have.get(p)]
        feats[name] = dict(ok=not missing, label=f["label"], missing=missing,
                           hint="; ".join(_HINTS[p] for p in missing))
    _CACHE = dict(python=sys.executable, have=have, features=feats)
    return _CACHE


def why_not(feature: str) -> str | None:
    """None if the feature works here, else a one-line message for the page."""
    f = report()["features"][feature]
    if f["ok"]:
        return None
    return (f"{f['label']} is not available on this computer: missing "
            f"{', '.join(f['missing'])} ({f['hint']}). Python: {sys.executable}")


if __name__ == "__main__":
    r = report()
    print(f"interpreter: {r['python']}")
    for piece, ok in r["have"].items():
        print(f"  {'yes' if ok else 'no ':3s}  {piece}")
    for name, f in r["features"].items():
        print(f"{'OK  ' if f['ok'] else 'OFF '} {f['label']}" +
              ("" if f["ok"] else f"  (missing: {', '.join(f['missing'])})"))
