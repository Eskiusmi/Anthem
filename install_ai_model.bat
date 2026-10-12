@echo off
cd /d "%~dp0"
echo Installing PyTorch and transformers for the CLAP AI model ...
echo This downloads a few hundred MB. On Windows the default PyTorch build runs on CPU.
echo NVIDIA GPU users can install a CUDA build of PyTorch instead - see README.
echo.
python -m pip install torch "transformers>=4.30"
echo.
echo Done. Restart start.bat. The CLAP model weights download automatically on the first analysis.
pause
