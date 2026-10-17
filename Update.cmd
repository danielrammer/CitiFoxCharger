@echo off
setlocal
rem The block is parsed before Git can update this batch file itself.
(
  cd /d "%~dp0"
  py -3 -c "import sys" >nul 2>nul
  if errorlevel 1 (
    python update.py %*
  ) else (
    py -3 update.py %*
  )
  call set "citifox_result=%%errorlevel%%"
  echo.
  echo Press any key to close this window.
  pause >nul
  call exit /b %%citifox_result%%
)
