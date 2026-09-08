@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv-beta\Scripts\pythonw.exe" (
  py -3.12 -m venv .venv-beta
  if errorlevel 1 goto failed
  ".venv-beta\Scripts\python.exe" -m pip install -e ".[navigation]"
  if errorlevel 1 goto failed
)
start "" ".venv-beta\Scripts\pythonw.exe" -I "%~dp0tools\run_beta_launcher.py"
exit /b 0
:failed
echo Beta launcher setup failed. No RuneLite input was started.
pause
exit /b 2
