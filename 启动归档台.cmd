@echo off
rem ==========================================================
rem  Typecho Archive Studio - launcher
rem  Drag a backup or a site archive onto this file, or just
rem  double-click it to pick one from a dialog.
rem
rem  RULES (learned the hard way, please keep them):
rem  1. Keep this file ASCII-only and CRLF-terminated. cmd
rem     reads batch files with the *current* console code page,
rem     so non-ASCII text here garbles on some machines. All
rem     Chinese output comes from Python instead.
rem  2. Never add "chcp 65001". Changing the code page makes cmd
rem     re-interpret its ANSI argument buffer as UTF-8 and
rem     silently corrupts non-ASCII paths dropped by Explorer.
rem  3. Never set PYTHONIOENCODING here: Python must follow the
rem     console code page to print Chinese correctly.
rem
rem  Interpreter lookup order:
rem     %TARCH_PYTHON%  ->  runtime\python.exe  ->  py -3  ->  python
rem ==========================================================
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0"
title Typecho Archive Studio
set "PYCMD="

if defined TARCH_PYTHON set "PYCMD="%TARCH_PYTHON%""

if not defined PYCMD if exist "%~dp0runtime\python.exe" set "PYCMD="%~dp0runtime\python.exe""

if not defined PYCMD where py >nul 2>nul
if not defined PYCMD if not errorlevel 1 set "PYCMD=py -3"

if not defined PYCMD where python >nul 2>nul
if not defined PYCMD if not errorlevel 1 set "PYCMD=python"

if not defined PYCMD goto no_python

rem Only pass a dropped path along if it really exists. If a path
rem was mangled somewhere, fall through to the file dialog.
set "GOODARGS="
if not "%~1"=="" if exist "%~1" set "GOODARGS=1"

if not defined GOODARGS goto run_pick
%PYCMD% -m typecho_archive serve %*
goto check

:run_pick
%PYCMD% -m typecho_archive serve

:check
if errorlevel 1 goto failed
goto end

:no_python
echo.
echo   Python was not found. Please install Python 3.10 or newer:
echo   https://www.python.org/downloads/
echo.
echo   Tick "Add Python to PATH" during setup.
echo.
if defined TARCH_QUIET goto end
pause
exit /b 1

:failed
echo.
echo   Startup failed. Run the command below in a terminal to see why:
echo   %PYCMD% -m typecho_archive info "your-backup.dat"
echo.
if defined TARCH_QUIET goto end
pause
exit /b 1

:end
endlocal
