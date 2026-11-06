@echo off
rem 공용: 프로젝트 가상환경(.venv)의 Python을 CHUNGMU_PYTHON에 설정한다.
rem .venv가 없으면 Python 3.11 이상으로 새로 만들고 requirements.txt를 설치한다.
set "CHUNGMU_PYTHON=%~dp0.venv\Scripts\python.exe"
if exist "%CHUNGMU_PYTHON%" exit /b 0
set "CHUNGMU_BASE="
for %%V in (3.15 3.14 3.13 3.12 3.11) do (
  if not defined CHUNGMU_BASE py -%%V -c "" >nul 2>&1 && set "CHUNGMU_BASE=py -%%V"
)
if not defined CHUNGMU_BASE (
  python -c "import sys;sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1 && set "CHUNGMU_BASE=python"
)
if not defined CHUNGMU_BASE (
  echo Python 3.11 이상을 찾지 못했습니다. https://www.python.org 에서 설치한 뒤 다시 실행하세요.
  exit /b 1
)
echo 처음 실행 준비: 가상환경을 만들고 필요한 패키지를 설치합니다...
%CHUNGMU_BASE% -m venv "%~dp0.venv" || exit /b 1
"%CHUNGMU_PYTHON%" -m pip install -q -r "%~dp0requirements.txt" || exit /b 1
exit /b 0
