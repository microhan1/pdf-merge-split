@echo off
rem Build pdf-merge-split.exe (single file, no console) with PyInstaller.
rem Usage: build.bat          -> dist\pdf-merge-split.exe
setlocal
cd /d "%~dp0"

python -m PyInstaller --version >nul 2>&1 || python -m pip install pyinstaller
python -m pip install -r requirements.txt

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name pdf-merge-split ^
  --add-data "lang;lang" ^
  --collect-data tkinterdnd2 ^
  main.py

if errorlevel 1 (
  echo Build failed.
  exit /b 1
)
echo.
echo Done: dist\pdf-merge-split.exe
endlocal
