@echo off
REM Build ORION.exe (dist\ORION) and, if Inno Setup is installed, the installer.
cd /d "%~dp0"
call .venv\Scripts\activate.bat || (echo Run SETUP.cmd first. & exit /b 1)
pip install -e ".[dev,docs]" || exit /b 1
where npm >nul 2>nul && (pushd ui && call npm ci && call npm run build && popd)
pyinstaller packaging\orion.spec --noconfirm || exit /b 1
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" packaging\installer.iss
echo Done. See the dist folder.
