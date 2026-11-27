@echo off
setlocal
title SwarmLink - Setup new robot
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\setup-new-robot.py" %*
) else (
  python "scripts\setup-new-robot.py" %*
)
echo.
pause
