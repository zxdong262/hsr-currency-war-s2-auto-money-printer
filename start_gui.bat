@echo off
rem Native GUI entry point. start.bat handles UAC and the virtual environment.
rem The GUI opens on the Settings tab so shortcuts / values can be edited first.
call "%~dp0start.bat" --settings
