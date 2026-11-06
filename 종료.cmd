@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_python.cmd" || (pause & exit /b 1)
"%CHUNGMU_PYTHON%" -m automation.stop
pause
