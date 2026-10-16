@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 run.py --browser brave --fullscreen
) else (
  python run.py --browser brave --fullscreen
)
if errorlevel 1 pause
