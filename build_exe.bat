@echo off
rem Build the Windows version of CS2 MusicBox (PyInstaller). Output: dist\CS2MusicBox\ and dist\CS2MusicBox-windows.zip
rem Needs Python 3.13 from https://www.python.org (tick "Add Python to PATH"). First run downloads ~300 MB of packages.
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3.13 from https://www.python.org and tick "Add Python to PATH".
  pause
  exit /b 1
)
if not exist ".venv-build\Scripts\python.exe" (
  echo Creating build environment .venv-build ...
  python -m venv .venv-build || goto :fail
)
set "PY=.venv-build\Scripts\python.exe"
echo Installing pinned packages from requirements-exe.txt ...
"%PY%" -m pip install --disable-pip-version-check -q -r requirements-exe.txt || goto :fail
echo Building ...
"%PY%" -m PyInstaller --noconfirm packaging\cs2musicbox.spec || goto :fail
echo Testing the built program ...
"%PY%" packaging\smoke_test.py dist\CS2MusicBox || goto :fail
echo Zipping ...
powershell -NoProfile -Command "Compress-Archive -Force -Path 'dist\CS2MusicBox' -DestinationPath 'dist\CS2MusicBox-windows.zip'" || goto :fail
echo.
echo Done.  Program: dist\CS2MusicBox\CS2MusicBox.exe     To share: dist\CS2MusicBox-windows.zip
pause
exit /b 0

:fail
echo.
echo Build FAILED - see the messages above.
pause
exit /b 1
