@echo off
REM Start ORION from source. Use the installer build for everyday use.
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (echo Run SETUP.cmd first. & pause & exit /b 1)
start "" .venv\Scripts\pythonw.exe -m orion %*
