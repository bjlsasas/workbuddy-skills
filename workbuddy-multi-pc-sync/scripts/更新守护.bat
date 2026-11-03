@echo off
setlocal enabledelayedexpansion
title Update sync watcher - run on the LAPTOP
echo ============================================================
echo    Update sync watcher
echo.
echo    Copies the three programs from THIS folder into
echo        %USERPROFILE%\.workbuddy\sync-heal\
echo    and restarts the watcher.
echo.
echo    After you run this once, the watcher updates itself
echo    from this folder automatically -- no more clicking.
echo    Nothing on disk is deleted; old files are kept as .bak
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
  echo     Please send this whole window to the assistant.
  echo.
  pause
  exit /b 1
)

echo Python : %PYEXE% %PYARG%
echo Script : %~dp0update-watch.py
echo.
echo Running, please wait (about 20 seconds) ...
echo.

"%PYEXE%" %PYARG% "%~dp0update-watch.py"
set RC=!errorlevel!

echo.
echo ============================================================
echo   Exit code = !RC!
echo    0 = updated and watcher restarted
echo    2 = distribution folder incomplete (send this window back)
echo ============================================================
echo.
echo Press any key to close this window ...
pause >nul
