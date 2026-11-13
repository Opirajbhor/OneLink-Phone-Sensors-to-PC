@echo off
rem OneLink PC client launcher.
rem Finds a Python 3.11+ that has tkinter + pillow + zeroconf, then runs the app.
rem Any arguments are forwarded, e.g.  run.bat --selftest
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PROBE=import tkinter, PIL, zeroconf"
set "PYEXE="

rem 1) plain "python" on PATH
python -c "%PROBE%" >nul 2>&1 && set "PYEXE=python"

rem 2) the Windows launcher
if not defined PYEXE (
  py -3 -c "%PROBE%" >nul 2>&1 && set "PYEXE=py -3"
)

rem 3) known install paths (edit if yours differs)
if not defined PYEXE (
  for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  ) do (
    if not defined PYEXE (
      if exist %%P (
        %%P -c "%PROBE%" >nul 2>&1 && set "PYEXE=%%~P"
      )
    )
  )
)

if not defined PYEXE (
  echo.
  echo [OneLink] No Python found with tkinter + pillow + zeroconf.
  echo           Install them with:
  echo               python -m pip install pillow zeroconf
  echo           or set the interpreter explicitly in this file ^(PYEXE^).
  echo.
  pause
  exit /b 1
)

%PYEXE% -m onelink %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [OneLink] exited with code %RC%
  pause
)
endlocal & exit /b %RC%
