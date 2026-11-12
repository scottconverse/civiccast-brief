@echo off
rem Installs CivicCast Brief for this user. See install.ps1 for the steps.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
if errorlevel 1 (echo. & echo The install stopped. The message above says why.)
pause
