@echo off
setlocal
cd /d "%~dp0"
py -3 -c "import sys" >nul 2>nul
if %errorlevel% equ 0 (
  py -3 -m unittest discover -s tests -v
) else (
  python -m unittest discover -s tests -v
)
set "citifox_result=%errorlevel%"
pause
exit /b %citifox_result%
