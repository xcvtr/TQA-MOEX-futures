@echo off
setlocal enableextensions enabledelayedexpansion
setlocal enabledelayedexpansion

rem python qsh_table_cleanup.py

set YEAR=2025

set MON=1

if %MON% LSS 10 (set ZEROM=0) else (set ZEROM=)
set FMON=%ZEROM%%MON%


for /l %%i in (1,1,3) do (
  if %%i LSS 10 (set ZEROD=0) else (set ZEROD=)
  set FDAY=!ZEROD!%%i
  start /b /low parse.bat %YEAR%-%FMON%-!FDAY!
)

call :wait_python

exit /b

:wait_python
ping 127.0.0.1 -n 2 > nul
tasklist | find "python" > nul
if %errorlevel% EQU 0 goto wait_python
exit /b
