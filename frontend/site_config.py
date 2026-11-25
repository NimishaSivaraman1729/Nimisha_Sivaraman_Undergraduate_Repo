"""Machine-specific settings: the only things to change on another computer.

Each setting is read, first hit wins, from
  1. an environment variable  MERGEMON_<NAME>       (e.g. MERGEMON_GEOMETRY_ENV=)
  2. local_settings.json next to this file           (per machine; copy
     local_settings.example.json and edit it)
  3. the default below (GEOMETRY_ENV, CONDA_EXE: QDG-WS1's)

GEOMETRY_ENV is the conda env that has gdspy (the conductor check exports a
temporary GDS). Empty: this interpreter itself -- the simplest setup, one env with
qiskit-metal + pyEPR + gdspy. On WS1 gdspy lives in a second env (pymetal23),
reached through `conda run` (its python.exe called directly dies in the export).
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULTS = dict(
    GEOMETRY_ENV="pymetal23",                                   # "" = this interpreter
    CONDA_EXE=r"C:\Users\quant\anaconda3\Scripts\conda.exe",    # only if GEOMETRY_ENV is set
)

_LOCAL = os.path.join(HERE, "local_settings.json")


def _load_local():
    if not os.path.exists(_LOCAL):
        return {}
    with open(_LOCAL, encoding="utf-8") as fh:
        data = json.load(fh)
    unknown = set(data) - set(DEFAULTS) - {"_comment"}
    if unknown:
        raise KeyError(f"{_LOCAL}: unknown setting(s) {sorted(unknown)}; known: {sorted(DEFAULTS)}")
    return data


def get(name):
    """(value, source) of one setting."""
    env = os.environ.get(f"MERGEMON_{name}")
    if env is not None:
        return env, f"env MERGEMON_{name}"
    local = _load_local()
    if name in local:
        return local[name], "local_settings.json"
    return DEFAULTS[name], "default"


GEOMETRY_ENV = get("GEOMETRY_ENV")[0]
CONDA_EXE = get("CONDA_EXE")[0]


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
