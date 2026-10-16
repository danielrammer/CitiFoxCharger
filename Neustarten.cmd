@echo off
setlocal
cd /d "%~dp0"
py -3 -c "import sys" >nul 2>nul
if %errorlevel% equ 0 (
  py -3 manage.py restart %*
) else (
  python manage.py restart %*
)
set "citifox_result=%errorlevel%"
if not "%citifox_result%"=="0" pause
exit /b %citifox_result%
