# Surface participation of superconducting qubits

Surface participation ratio (SPR) and dielectric-loss estimates for three qubit
designs, from Ansys simulations. The original work was done in Ansys
Electronics Desktop plus a spreadsheet (`spr graphs.xlsx`); here it is
rewritten as Python that reproduces the spreadsheet and can rebuild the
simulations through PyAEDT.

| Folder | Design | Method |
|---|---|---|
| [`SPR_tutorial/`](SPR_tutorial) | Planar transmon (two HFSS designs + 2D edge model) | Wang *et al.* 2015: 3D interior fields, 2D edge correction F |
| [`flipmon/`](flipmon) | Flip-chip transmon | Wang *et al.* 2015, separately for bottom and top chip |
| [`mergemon/`](mergemon) | Mergemon (SOI, 3 µm features) | 2D cross-section SPR weighted by the 3D energy fraction in the coupling capacitor; plus a 2D junction underetch study |

Reference: C. Wang *et al.*, "Surface participation and dielectric loss in
superconducting qubits", *Appl. Phys. Lett.* **107**, 162601 (2015),
[arXiv:1509.01854](https://arxiv.org/abs/1509.01854).

## Layout of each folder

* `*_design.py`: geometry, materials and simulation settings (plain Python, draws the layout)
* `*_calculation.py`: the SPR arithmetic, checked against the spreadsheet
* `*_ansys.py` (or `mergemon_energy_boxes.py`): builds the model in AEDT through PyAEDT
  and extracts the field integrals

## Requirements

* Design and calculation files: Python ≥ 3.10, `matplotlib` for plots.
* Ansys files: [PyAEDT](https://aedt.docs.pyansys.com/) (`pip install pyaedt`) and a
  local Ansys Electronics Desktop (written against AEDT 2024 R1). They open AEDT
  with its window visible by default; pass `--non-graphical` for batch runs.
  Generated projects are written outside the repository, to
  `../SPR_testing/<folder>/` next to this folder, because they are large.
