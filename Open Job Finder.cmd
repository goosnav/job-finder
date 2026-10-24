@echo off
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Open Job Finder.ps1" -NoPause
if errorlevel 1 (
  echo Job Finder could not start. Read the error above.
  pause
  exit /b 1
)
