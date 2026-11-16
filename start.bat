@echo off
setlocal
cd /d "%~dp0"
title Auto Money Printer

rem StarRail.exe runs as administrator. A non-elevated process cannot inject
rem clicks or global hotkeys into it (UIPI), so re-launch elevated.
net session >nul 2>&1
if errorlevel 1 (
  echo Requesting administrator rights...
  rem Start-Process rejects an empty -ArgumentList, so only pass it when needed
  if "%~1"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  ) else (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
  )
  if errorlevel 1 (
    echo.
    echo Elevation was cancelled or failed.
    echo This app must run as administrator, otherwise Windows blocks
    echo mouse clicks and hotkeys sent to the game.
    echo.
    pause
  )
  exit /b
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

echo.
echo ============================================================
echo  Auto Money Printer  -  HSR currency war S2
echo ============================================================
".venv\Scripts\python.exe" -c "import json;h=json.load(open('config.json',encoding='utf-8')).get('hotkeys',{});print('  Hotkeys in config.json:  ' + '   '.join(str(k).upper()+'='+str(v) for k,v in h.items()))" 2>nul
echo  They work globally, while the game window has focus.
echo.

".venv\Scripts\python.exe" main.py %*
if errorlevel 1 pause
