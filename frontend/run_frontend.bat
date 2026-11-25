@echo off
rem Mergemon Designer: starts the local server and opens http://localhost:8765
rem The Python used is the env with qiskit-metal + pyEPR ("HFSS env"). Set
rem MERGEMON_PYTHON to its python.exe on another computer (default: QDG-WS1's).
cd /d "%~dp0"
if "%MERGEMON_PYTHON%"=="" set MERGEMON_PYTHON=C:\Users\quant\miniconda3\envs\hfss_env\python.exe
if not exist "%MERGEMON_PYTHON%" (
  echo Python not found: %MERGEMON_PYTHON%
  echo Set MERGEMON_PYTHON to the python.exe of the env with qiskit-metal + pyEPR, e.g.
  echo   set MERGEMON_PYTHON=C:\Users\you\miniconda3\envs\hfss_env\python.exe
  pause
  exit /b 1
)
for %%I in ("%MERGEMON_PYTHON%") do set E=%%~dpI
set PATH=%E%;%E%Library\bin;%E%Scripts;%PATH%
"%MERGEMON_PYTHON%" -B server.py
pause
