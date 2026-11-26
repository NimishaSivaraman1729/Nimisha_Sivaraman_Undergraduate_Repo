# Mergemon frontend

A local web app for designing a merged-element transmon ("mergemon") with a
lumped LC readout resonator. You pick the parts and enter target figures of
merit. The app then builds the layout with qiskit-metal, solves it in Ansys
HFSS (eigenmode + pyEPR), optimizes the geometry toward the targets and
delivers a fab GDS.

## Requirements

The app itself runs on any computer with Python 3.8+ and `numpy` + `shapely`.
What else is installed decides what it can do:

| Feature | Needs |
|---|---|
| Design screen, checks, Results tab | numpy, shapely |
| Dry run, sweep tables | + pandas, qiskit-metal |
| Optimize, sweeps, AEDT previews, opening `.aedt` files | + matplotlib, pyEPR-quantum, pywin32, **Ansys Electronics Desktop** (Windows; tested on 2020 R2, driven over COM) |
| Fab GDS on delivery | gdspy, qiskit-metal, this MicrowaveDesign repo |

For everything, one Python 3.11 env with qiskit-metal, pyEPR-quantum and gdspy:
see [`requirements.txt`](requirements.txt) for the install commands.

## Run it

```
frontend\run_frontend.bat
```

This starts the server and opens <http://localhost:8765>. The batch file only
finds *a* Python and runs `launch.py`, which picks the interpreter for the
server, first hit wins:

1. the `MERGEMON_PYTHON` environment variable;
2. `PYTHON` in `local_settings.json` (see below);
3. the most capable Python it finds: conda envs, the usual install folders,
   `py` / `python` on PATH. It prefers one that can simulate, then one with gdspy.

`python frontend\launch.py --list` shows every candidate and what it has. You
can also run `python -B frontend\server.py` from the MicrowaveDesign folder
with any Python that has numpy + shapely.

When something is missing, the page shows a banner naming the features that are
off and what they need. Those buttons then answer with the same message instead
of failing mid-run. `python frontend\capabilities.py` prints the same report.

### Settings (optional)

The defaults work on any machine. To override one, copy
`local_settings.example.json` to `local_settings.json`. Keys under
`machines.<COMPUTERNAME>` apply on that computer only, so one shared folder can
hold several machines; top-level keys apply everywhere. An environment variable
`MERGEMON_<NAME>` overrides both.

- **`PYTHON`**: the interpreter for the server. Empty: picked automatically.
- **`GEOMETRY_ENV`**: a conda env that has gdspy, if it is not the server's own
  env. The code reaches it through `conda run`. Empty: the same env.
- **`CONDA_EXE`**: used only when `GEOMETRY_ENV` is set. Found automatically
  from the running conda install.
- **`MICROWAVE_DESIGN`**: `qdg_toolbox` (for the fab GDS) is found
  automatically from the repo this folder sits in. Set it only when running
  from elsewhere.

## Using it

The tabs follow the design flow:

1. **Substrate:** the wafer. Solid silicon is simulated; SOI is planned.
2. **Design:** qubit, readout resonator, coupling capacitor and readout
   feedline. The layout preview updates as you pick.
3. **Figures of merit:** L_J, C_J, target f_r and χ, and optionally κ.
   **Optimize** runs `optimize_multi.py`, a joint step over several knobs
   (**Dry run** plans the first step without solving).
   When it converges, it writes a delivery folder
   (e.g. `fr_4_72GHz_chi_2MHz/`) containing the fab GDS, a layout PNG, a
   re-solved `.aedt` and `summary.png`.
4. **Sweep:** sweep one knob around the current design. **Preview in AEDT**
   sets up every point without solving; **Run sweep** solves them.
5. **Results:** finished deliveries. **Open .aedt** opens a delivery's solved
   project in AEDT.

Options marked "planned" have no simulation behind them yet and block
Optimize.

A κ (linewidth) target sets the feedline coupling-finger length from a fit to
the solved whole-feedline points: κ = a (L − L0)². The fit is approximate;
the optimizer itself targets only f_r and χ.

## What's in here

| file | role |
|---|---|
| `server.py`, `static/`, `aedt_open.py` | the web app (standard-library server, plain HTML/JS) |
| `parts/`, `assembly.py` | part catalog, geometry and the qiskit-metal design |
| `simulate.py` | one HFSS eigenmode + pyEPR solve, stored as a record in `data/` |
| `sweep.py` | one-knob sweeps; the geometry check run before every solve |
| `optimize_multi.py`, `optimize_common.py` | multi-knob inverse design |
| `deliver.py`, `fab_gds.py`, `gds_check.py` | delivery folder, fab GDS (`qdg_toolbox` layers), GDS checks |
| `store.py` | the database: one JSON record per solved point, keyed by a content hash |
| `site_config.py` | per-machine settings (env var, then `local_settings.json`, then the default) |
| `run_frontend.bat`, `launch.py` | start the app with the most capable Python on the machine |
| `capabilities.py` | what this machine can run; switches features off with a message |
| `data/` | every solved point so far |

`data/` is committed on purpose. A design that was already solved is never
solved again, the optimizer starts from these points, and the κ fit needs
them.

## Not committed

`.gitignore` keeps the following out of git:

- `output/`: AEDT projects, logs and sweep configs (tens of GB)
- `data-pyEPR/`
- delivery folders
- all `.aedt` and `.gds` files
- `local_settings.json`

Solves take roughly 40–90 min per point, so keep `output/` on a drive with
room.
