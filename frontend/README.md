# Mergemon frontend

A local web app for designing a merged-element transmon ("mergemon") with a
lumped LC readout resonator. You pick the parts and enter target figures of
merit. The app then builds the layout with qiskit-metal, solves it in Ansys
HFSS (eigenmode + pyEPR) and optimizes the geometry toward the targets.
Writing a fab GDS is not supported yet (see [Not yet](#not-yet)).

## Requirements

- Windows with **Ansys Electronics Desktop** (tested on 2020 R2). Solves drive
  AEDT over COM.
- A Python 3.11 env with qiskit-metal, pyEPR-quantum and gdspy. See
  [`requirements.txt`](requirements.txt) for the install commands.

## Run it

```
frontend\run_frontend.bat
```

This starts the server and opens <http://localhost:8765>. The batch file uses
QDG-WS1's `hfss_env` by default. On another computer, first
`set MERGEMON_PYTHON=<path to your env's python.exe>`, or run
`python -B frontend\server.py` from the MicrowaveDesign folder.

### On a new computer

Copy `local_settings.example.json` to `local_settings.json` and edit it:

- **`GEOMETRY_ENV`**: set it to `""` if gdspy is in the same env. On WS1, gdspy
  lives in a second env (`pymetal23`), which the code reaches through
  `conda run`.
- **`CONDA_EXE`**: needed only when `GEOMETRY_ENV` is set.

## Using it

The tabs follow the design flow:

1. **Substrate:** the wafer. Solid silicon is simulated; SOI is planned.
2. **Design:** qubit, readout resonator, coupling capacitor and readout
   feedline. The layout preview updates as you pick.
3. **Figures of merit:** L_J, C_J, target f_r and χ, and optionally κ.
   **Optimize** runs `optimize_multi.py`, a joint step over several knobs
   (**Dry run** plans the first step without solving).
   When it converges, it writes a delivery folder
   (e.g. `fr_4_72GHz_chi_2MHz/`) containing a re-solved `.aedt` and
   `summary.png`.
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
| `deliver.py` | delivery folder: the design re-solved in its own `.aedt`, plus `summary.png` |
| `gds_check.py` | conductor count from a temporary GDS (the geometry check) |
| `store.py` | the database: one JSON record per solved point, keyed by a content hash |
| `site_config.py` | per-machine settings (env var, then `local_settings.json`, then the default) |
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

## Not yet

- **Fab GDS.** The older version wrote one with `qdg_toolbox`. It still has to
  be ported to `qdg_layout`'s step and layer convention
  ([docs/design-rules.md](../docs/design-rules.md)): which step the mergemon
  ground layer is, its tool, resist and process, and where the junctions go.
