@echo off
REM Build ORION.exe (dist\ORION) and, if Inno Setup is installed, the installer.
cd /d "%~dp0"
call .venv\Scripts\activate.bat || (echo Run SETUP.cmd first. & exit /b 1)
pip install -e ".[dev,docs]" || exit /b 1
where npm >nul 2>nul && (pushd ui && call npm ci && call npm run build && popd)
REM Refresh the Memory -> View brain map from the current code; the shipped map is kept if this fails.
pip install -e ".[brainmap]" && python scripts\build_brain_map.py || echo Keeping the existing brain map.
pyinstaller packaging\orion.spec --noconfirm || exit /b 1
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" packaging\installer.iss
echo Done. See the dist folder.
