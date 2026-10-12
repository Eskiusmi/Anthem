@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Please install Python 3.9+ from https://www.python.org and tick "Add Python to PATH".
  pause
  exit /b 1
)
python -c "import pygame" >nul 2>nul
if errorlevel 1 (
  echo Installing dependency pygame-ce ...
  python -m pip install "pygame-ce>=2.4"
)
python -c "import psutil" >nul 2>nul
if errorlevel 1 (
  echo Installing dependency psutil - used to detect when CS2 starts and exits ...
  python -m pip install "psutil>=5.9"
)
python -c "import sounddevice" >nul 2>nul
if errorlevel 1 (
  echo Installing sounddevice - used to send your mic and music to teammates ...
  python -m pip install "sounddevice>=0.4.6"
)
python -c "import librosa" >nul 2>nul
if errorlevel 1 (
  echo Installing librosa for AI analysis - first time only, may take a few minutes ...
  python -m pip install "librosa>=0.10"
)
python main.py
pause
