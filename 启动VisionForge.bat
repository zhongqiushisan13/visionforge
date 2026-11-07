@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting VisionForge ...
python run_app.py
if errorlevel 1 (
  echo python failed, trying py launcher ...
  py -3 run_app.py
)
if errorlevel 1 pause
