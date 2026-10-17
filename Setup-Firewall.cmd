@echo off
setlocal
cd /d "%~dp0"
py -3 -c "import sys" >nul 2>nul
if %errorlevel% equ 0 (
  py -3 setup_network.py --firewall %*
) else (
  python setup_network.py --firewall %*
)
set "citifox_result=%errorlevel%"
echo.
echo Press any key to close this window.
pause >nul
exit /b %citifox_result%
