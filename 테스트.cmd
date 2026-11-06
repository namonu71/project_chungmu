@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "CHUNGMU_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
"%CHUNGMU_PYTHON%" -m unittest discover -s automation/tests -v
pause
