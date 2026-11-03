@echo off
setlocal enabledelayedexpansion
title Update sync watcher - LOOK ONLY
echo ============================================================
echo    Look only -- nothing will be changed
echo    Prints which files differ between this folder and
echo    %USERPROFILE%\.workbuddy\sync-heal\
echo ============================================================
echo.

set PYEXE=
set PYARG=

if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PYEXE=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"

if not defined PYEXE (
  for /d %%d in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
    if exist "%%d\python.exe" set "PYEXE=%%d\python.exe"
  )
)

if not defined PYEXE (
  where py >nul 2>nul
  if !errorlevel! equ 0 (
    set "PYEXE=py"
    set "PYARG=-3"
  )
)

if not defined PYEXE (
  where python >nul 2>nul
  if !errorlevel! equ 0 set "PYEXE=python"
)

if not defined PYEXE (
  echo [X] Python interpreter not found on this machine.
  echo.
  pause
  exit /b 1
)

"%PYEXE%" %PYARG% "%~dp0update-watch.py" --check
set RC=!errorlevel!

echo.
echo Exit code = !RC!
echo.
echo Press any key to close this window ...
pause >nul
