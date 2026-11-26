"""Show an AEDT project in the AEDT the automation drives (over COM).

    python -B frontend/aedt_open.py <path.aedt>

Brings the project to the front if it is open there, else opens it there --
not in a second AEDT, which would find the project locked. Run as its own
process by frontend/server.py: pyEPR cannot be imported from a server thread
(it installs signal handlers).
"""

import os
import sys


def main(path):
    from pyEPR.ansys import HfssApp
    desk = HfssApp().get_app_desktop()
    name = os.path.splitext(os.path.basename(path))[0]
    if name in desk.get_project_names():
        desk.set_active_project(name)
    else:
        desk.open_project(path)
    try:
        desk._desktop.RestoreWindow()
    except Exception:                                 # noqa: BLE001
        pass
    print(f"OPENED {name}")


if __name__ == "__main__":
    main(os.path.abspath(sys.argv[1]))
