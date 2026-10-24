@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Open Job Finder.ps1"
if errorlevel 1 (
  echo Job Finder could not start. Read the error above.
  pause
  exit /b 1
)
