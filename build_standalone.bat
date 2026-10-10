@echo off
setlocal
cd /d "%~dp0"
if not exist ".build-venv\Scripts\python.exe" (
    py -3.12 -m venv .build-venv
    if errorlevel 1 goto failed
)
".build-venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller==6.22.2
if errorlevel 1 goto failed
".build-venv\Scripts\python.exe" -m PyInstaller --noconfirm quasi_EMG_streamer.spec
if errorlevel 1 goto failed
".build-venv\Scripts\python.exe" -c "import shutil; shutil.make_archive('dist/quasi_EMG_streamer-Windows-x64', 'zip', 'dist', 'quasi_EMG_streamer')"
if errorlevel 1 goto failed
echo Ready: dist\quasi_EMG_streamer-Windows-x64.zip
exit /b 0
:failed
echo Standalone build failed.
exit /b 1
