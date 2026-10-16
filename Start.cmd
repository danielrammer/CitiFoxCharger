@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 run.py --background --browser brave --fullscreen
) else (
  python run.py --background --browser brave --fullscreen
)
if errorlevel 1 pause
