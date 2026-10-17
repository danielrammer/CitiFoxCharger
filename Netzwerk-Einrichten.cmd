@echo off
setlocal
cd /d "%~dp0"
py -3 -c "import sys" >nul 2>nul
if %errorlevel% equ 0 (
  py -3 setup_network.py %*
) else (
  python setup_network.py %*
)
set "citifox_result=%errorlevel%"
echo.
echo Zum Schliessen eine Taste druecken.
pause >nul
exit /b %citifox_result%
