"""Start the front end with the best Python on this computer.

    python launch.py [--no-browser] [--list]      (run_frontend.bat calls this)

Any Python 3.8+ can run this file (standard library only). It picks the
interpreter for server.py, first hit wins:
  1. MERGEMON_PYTHON (environment variable)
  2. PYTHON in local_settings.json (this machine's section, see site_config.py)
  3. the most capable Python found: this one, conda envs (conda env list and the
     usual install folders), and `py` / `python` on PATH. "Most capable" means it
     can simulate (qiskit-metal + pyEPR + pywin32), then has gdspy, then pandas +
     matplotlib. It needs at least numpy + shapely (the design screen).
--list prints every candidate and what it has, and exits.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PROBE = ("import importlib.util as u, importlib.metadata as md, json, sys\n"
         "def d(n):\n"
         "    try: return md.version(n)\n"
         "    except Exception: return None\n"
         "print('PROBE' + json.dumps(dict(v=sys.version_info[:2], qm=d('quantum-metal'), "
         "m={m: u.find_spec(m) is not None for m in %r})))")
MODS = ["numpy", "shapely", "pandas", "matplotlib", "qiskit_metal", "pyEPR", "win32com", "gdspy"]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def env_path(python):
    """PATH with a conda env's DLL folders first (needed when its python.exe is
    called without `conda activate`)."""
    d = os.path.dirname(os.path.abspath(python))
    extra = [d, os.path.join(d, "Library", "bin"), os.path.join(d, "Scripts")]
    return os.pathsep.join([p for p in extra if os.path.isdir(p)] + [os.environ.get("PATH", "")])


def probe(python):
    """{v: (major, minor), m: {module: bool}} or None if it does not run."""
    try:
        out = subprocess.run([python, "-I", "-c", PROBE % MODS], capture_output=True, text=True,
                             timeout=60, env=dict(os.environ, PATH=env_path(python)),
                             creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    line = next((s for s in out.stdout.splitlines() if s.startswith("PROBE")), None)
    return json.loads(line[5:]) if line else None


def score(info):
    m = info["m"]
    if tuple(info["v"]) < (3, 8) or not (m["numpy"] and m["shapely"]):
        return -1
    sim = m["qiskit_metal"] and m["pyEPR"] and m["win32com"]
    # quantum-metal (the maintained qiskit-metal, 0.8.x) is what the automation was tested on
    return (100 * sim + 10 * m["gdspy"] + 5 * (m["pandas"] and m["matplotlib"])
            + 2 * bool(info.get("qm")) + m["qiskit_metal"])


def conda_bases():
    home = os.path.expanduser("~")
    roots = [home, os.environ.get("ProgramData", r"C:\ProgramData"),
             os.environ.get("LOCALAPPDATA", ""), r"C:\\"]
    names = ["anaconda3", "miniconda3", "miniforge3", "mambaforge", "Anaconda3", "Miniconda3"]
    found = [os.path.join(r, n) for r in roots if r for n in names]
    conda = os.environ.get("CONDA_EXE") or shutil.which("conda")
    if conda:
        found.append(os.path.dirname(os.path.dirname(os.path.abspath(conda))))
    return [b for b in dict.fromkeys(found) if os.path.isdir(b)]


def python_of(prefix):
    for p in (os.path.join(prefix, "python.exe"), os.path.join(prefix, "bin", "python")):
        if os.path.isfile(p):
            return p
    return None


def candidates():
    out = [sys.executable]
    for base in conda_bases():
        out.append(python_of(base))
        out += [python_of(e) for e in sorted(glob.glob(os.path.join(base, "envs", "*")))]
    conda = os.environ.get("CONDA_EXE") or shutil.which("conda")
    if conda:
        try:
            envs = json.loads(subprocess.run([conda, "env", "list", "--json"], capture_output=True,
                                             text=True, timeout=60, creationflags=NO_WINDOW).stdout)
            out += [python_of(e) for e in envs.get("envs", [])]
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    for name in ("python", "python3"):
        p = shutil.which(name)
        if p and "WindowsApps" not in p:              # skip the Microsoft Store stub
            out.append(p)
    seen, uniq = set(), []
    for p in out:
        if p and os.path.isfile(p):
            key = os.path.normcase(os.path.realpath(p))
            if key not in seen:
                seen.add(key)
                uniq.append(p)
    return uniq


def describe(info):
    m = info["m"]
    have = [k for k in MODS if m[k]]
    qm = f" (quantum-metal {info['qm']})" if info.get("qm") else ""
    return f"Python {info['v'][0]}.{info['v'][1]}; has {', '.join(have) or 'none of the packages'}{qm}"


def choose(verbose=False):
    """(python, why) or (None, message)."""
    try:
        from site_config import get
        fixed, source = get("PYTHON")
    except Exception as err:                           # noqa: BLE001
        fixed, source = "", f"(site_config not readable: {err})"
    if os.environ.get("MERGEMON_PYTHON"):
        fixed, source = os.environ["MERGEMON_PYTHON"], "env MERGEMON_PYTHON"
    if fixed:
        if not os.path.isfile(fixed):
            return None, (f"{source} points to {fixed}, which does not exist on this computer. "
                          "Fix or remove it to let the launcher pick a Python.")
        info = probe(fixed)
        if info is None or score(info) < 0:
            return None, (f"{source} = {fixed} cannot run the front end "
                          f"({describe(info) if info else 'does not start'}): it needs numpy + shapely")
        return fixed, f"{source}; {describe(info)}"
    best = None
    for p in candidates():
        info = probe(p)
        s = score(info) if info else -1
        if verbose:
            print(f"  [{s:4d}] {p}\n         {describe(info) if info else 'does not start'}")
        if s >= 0 and (best is None or s > best[0]):
            best = (s, p, info)
    if best is None:
        return None, ("no Python with numpy + shapely found. Install Python 3.8+ (or Miniconda) "
                      "and run:  pip install numpy shapely pandas matplotlib")
    return best[1], f"picked automatically; {describe(best[2])}"


def main():
    if "--list" in sys.argv:
        print("Python interpreters found (score: higher = more features):")
        py, why = choose(verbose=True)
        print(f"\nwould use: {py or 'none'}  ({why})")
        return 0
    py, why = choose()
    if py is None:
        print("Cannot start the mergemon front end:", why)
        return 1
    print(f"Python: {py}\n        ({why})")
    args = [a for a in sys.argv[1:] if a == "--no-browser"]
    env = dict(os.environ, PATH=env_path(py), MERGEMON_PYTHON=py)
    return subprocess.call([py, "-B", os.path.join(HERE, "server.py"), *args], cwd=HERE, env=env)


if __name__ == "__main__":
    sys.exit(main())
