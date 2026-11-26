@echo off
rem Mergemon Designer: starts the local server and opens http://localhost:8765
rem Works on any Windows computer with Python 3.8+ and numpy + shapely. This file
rem only finds *a* Python to run launch.py; launch.py then picks the best one
rem (MERGEMON_PYTHON, PYTHON in local_settings.json, else the most capable env).
rem Without qiskit-metal / pyEPR / AEDT the design screen still works and the
rem page says which features are off.  See: launch.py --list
pushd "%~dp0"
set "BOOT="
if defined MERGEMON_PYTHON if exist "%MERGEMON_PYTHON%" set "BOOT=%MERGEMON_PYTHON%"
if not defined BOOT for %%P in (
  "%USERPROFILE%\miniconda3\python.exe" "%USERPROFILE%\anaconda3\python.exe"
  "%USERPROFILE%\miniforge3\python.exe" "%USERPROFILE%\mambaforge\python.exe"
  "%ProgramData%\miniconda3\python.exe" "%ProgramData%\anaconda3\python.exe"
  "%LOCALAPPDATA%\miniconda3\python.exe" "%LOCALAPPDATA%\anaconda3\python.exe"
) do if not defined BOOT if exist %%P set "BOOT=%%~P"
if not defined BOOT (
  py -3 -c "import sys" >nul 2>nul && set "BOOT=py"
)
if not defined BOOT (
  python -c "import sys" >nul 2>nul && set "BOOT=python"
)
if not defined BOOT (
  echo No Python found on this computer.
  echo Install Python 3.8+ or Miniconda, then:  pip install numpy shapely pandas matplotlib
  echo Or set MERGEMON_PYTHON to a python.exe that has them.
  pause
  popd
  exit /b 1
)
if "%BOOT%"=="py" (py -3 -B launch.py %*) else ("%BOOT%" -B launch.py %*)
pause
popd
