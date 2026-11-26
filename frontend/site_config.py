"""Machine-specific settings: the only things to change on another computer.

Each setting is read, first hit wins, from
  1. an environment variable  MERGEMON_<NAME>       (e.g. MERGEMON_GEOMETRY_ENV=)
  2. local_settings.json next to this file, in its "machines" section under this
     computer's name (lets one shared folder hold several machines' settings)
  3. local_settings.json, top level
  4. the default below, which works on any machine

    python capabilities.py          # what the front end can do with this Python
    python launch.py --list         # every Python found and what it has

PYTHON is the interpreter run_frontend.bat starts the server with. Empty: the
launcher picks the most capable Python it finds (launch.py).

MICROWAVE_DESIGN defaults to the repository this folder sits in
(MicrowaveDesign/frontend), so it only needs setting when run from elsewhere.

GEOMETRY_ENV is a conda env that has gdspy (GDS export, conductor count, fab
GDS). Empty: this interpreter itself -- the simplest setup, one env with
qiskit-metal + pyEPR + gdspy. A machine whose gdspy lives in a second env sets
it (and CONDA_EXE, if conda is not found automatically); the code then reaches
that env through `conda run`.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MACHINE = (os.environ.get("COMPUTERNAME") or socket.gethostname()).upper()


def _find_conda():
    """conda.exe of the running conda install, or on PATH; "" if none."""
    cands = [os.environ.get("CONDA_EXE"), shutil.which("conda")]
    prefix = sys.prefix                       # <base> or <base>/envs/<name>
    base = os.path.dirname(os.path.dirname(prefix)) if os.path.basename(
        os.path.dirname(prefix)).lower() == "envs" else prefix
    cands += [os.path.join(base, "Scripts", "conda.exe"), os.path.join(base, "condabin", "conda.bat"),
              os.path.join(base, "bin", "conda")]
    return next((c for c in cands if c and os.path.exists(c)), "")


DEFAULTS = dict(
    PYTHON="",                                  # "" = launcher picks the best Python found
    GEOMETRY_ENV="",                            # "" = this interpreter
    CONDA_EXE=_find_conda(),                    # only used if GEOMETRY_ENV is set
    # the group's MicrowaveDesign repo (qdg_toolbox: QDGDesign.write_gds), read only:
    # the repository this frontend/ folder lives in
    MICROWAVE_DESIGN=os.path.dirname(HERE),
)

_LOCAL = os.path.join(HERE, "local_settings.json")


def _check_keys(data, where):
    unknown = set(data) - set(DEFAULTS) - {"_comment"}
    if unknown:
        raise KeyError(f"{_LOCAL} ({where}): unknown setting(s) {sorted(unknown)}; "
                       f"known: {sorted(DEFAULTS)}")


def _load_local():
    """(this machine's section, top-level settings) of local_settings.json."""
    if not os.path.exists(_LOCAL):
        return {}, {}
    with open(_LOCAL, encoding="utf-8") as fh:
        data = json.load(fh)
    machines = {k.upper(): v for k, v in (data.pop("machines", None) or {}).items()}
    mine = machines.get(MACHINE, {})
    _check_keys(data, "top level")
    _check_keys(mine, f"machines.{MACHINE}")
    return mine, data


def get(name):
    """(value, source) of one setting."""
    env = os.environ.get(f"MERGEMON_{name}")
    if env is not None:
        return env, f"env MERGEMON_{name}"
    mine, common = _load_local()
    if name in mine:
        return mine[name], f"local_settings.json (machines.{MACHINE})"
    if name in common:
        return common[name], "local_settings.json"
    return DEFAULTS[name], "default"


PYTHON = get("PYTHON")[0]
GEOMETRY_ENV = get("GEOMETRY_ENV")[0]
CONDA_EXE = get("CONDA_EXE")[0]
MICROWAVE_DESIGN = get("MICROWAVE_DESIGN")[0]


def has_gdspy():
    try:
        import gdspy  # noqa: F401
        return True
    except ImportError:
        return False


def geometry_python():
    """Command prefix that runs Python where gdspy is: this interpreter when it
    has gdspy (or GEOMETRY_ENV is empty), else `conda run -n GEOMETRY_ENV python`."""
    if not GEOMETRY_ENV or has_gdspy():
        return [sys.executable]
    return [CONDA_EXE, "run", "-n", GEOMETRY_ENV, "--no-capture-output", "python"]
