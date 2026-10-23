@echo off
rem Pixel Arttirici - araclari ve modelleri kurar (tools\, models\). Ayrintilar: kurulum.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0kurulum.ps1" %*
if errorlevel 1 pause
