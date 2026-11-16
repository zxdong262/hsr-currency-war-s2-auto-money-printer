@echo off
setlocal
cd /d "%~dp0"
title Build Auto Money Printer

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
".venv\Scripts\python.exe" -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
  echo Installing PyInstaller...
  ".venv\Scripts\python.exe" -m pip install pyinstaller
)

if exist build rmdir /s /q build
if exist "dist\AutoMoneyPrinter" rmdir /s /q "dist\AutoMoneyPrinter"
if exist "dist\AutoMoneyPrinter.zip" del /q "dist\AutoMoneyPrinter.zip"

echo.
echo Building native GUI app...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean build.spec
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)

echo.
echo Zipping dist\AutoMoneyPrinter ...
powershell -NoProfile -Command "Compress-Archive -Force -Path 'dist\AutoMoneyPrinter\*' -DestinationPath 'dist\AutoMoneyPrinter.zip'"
if errorlevel 1 (
  echo Zip failed; the folder dist\AutoMoneyPrinter is still usable.
) else (
  echo Done: dist\AutoMoneyPrinter.zip
)
