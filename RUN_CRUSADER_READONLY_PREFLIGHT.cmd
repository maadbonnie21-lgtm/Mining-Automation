@echo off
setlocal
cd /d "%~dp0"

set "CR_PY="
if exist ".venv\Scripts\python.exe" set "CR_PY=.venv\Scripts\python.exe"
if not defined CR_PY where py >nul 2>nul && set "CR_PY=py -3"
if not defined CR_PY where python >nul 2>nul && set "CR_PY=python"

if not defined CR_PY (
  echo.
  echo [CRUSADER ROW] STOPPED: Python was not found.
  echo Install/setup is not attempted automatically by this read-only check.
  echo.
  pause
  exit /b 1
)

echo.
echo ============================================================
echo  CRUSADER ROW - READ-ONLY RUNELITE CAPTURE PREFLIGHT
echo ============================================================
echo This check sends NO mouse or keyboard input.
echo Required mining capture envelope: 1005x1078 BGRA, DPI 96.
echo RuneLite must be visible and not minimized.
echo.

%CR_PY% tools\windows_capture_check.py --title RuneLite --frames 3 --interval 0.25 --require-width 1005 --require-height 1078 --require-dpi 96 --require-all-successful
set "CR_RC=%ERRORLEVEL%"

echo.
if "%CR_RC%"=="0" (
  echo [CRUSADER ROW] PASS: controlled read-only capture envelope is available.
  echo This does NOT prove the supported mining view or authorize any input.
) else (
  echo [CRUSADER ROW] STOP: capture preflight did not meet the exact envelope.
  echo No input was sent. Leave the failure visible for diagnosis.
)
echo.
pause
exit /b %CR_RC%
