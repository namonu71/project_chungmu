@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "CHUNGMU_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%CHUNGMU_PYTHON%" (
  echo Python 실행 파일을 찾지 못했습니다.
  echo Python 3.11 이상과 requirements.txt의 패키지를 준비한 뒤 실행하세요.
  echo 명령: python -m automation.launch
  pause
  exit /b 1
)
"%CHUNGMU_PYTHON%" -m automation.launch
if errorlevel 1 pause
