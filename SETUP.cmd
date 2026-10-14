@echo off
REM One-time developer setup on Windows: creates .venv and installs ORION in editable mode.
cd /d "%~dp0"
where py >nul 2>nul || (echo Install Python 3.11+ from python.org first. & pause & exit /b 1)
py -3 -m venv .venv || goto :err
call .venv\Scripts\activate.bat
python -m pip install -U pip || goto :err
pip install -e ".[docs]" || goto :err
echo.
echo  Optional: "pip install openwakeword" enables the offline "Hey Jarvis" wake word.
echo  Optional: install Everything (voidtools.com) + es.exe for instant file search.
echo.
echo  Setup complete. Run ORION.cmd to start.
pause
exit /b 0
:err
echo Setup failed. See the messages above.
pause
exit /b 1
