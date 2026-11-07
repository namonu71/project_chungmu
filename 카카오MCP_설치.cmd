@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PIN=7b41dd9da473e7a9e2f4b07ec27bdf3289fcc5fb"
set "SRC=git+https://github.com/kronenz/kakaotalk-mcp@%PIN%"

echo ===== 카카오톡 MCP 설치 (검토한 커밋 %PIN:~0,7% 고정 + 창 자동 열기 kakao_auto) =====
echo.
echo [1/5] 필요한 도구 확인 (git, uv)
where git >nul 2>nul
if errorlevel 1 (
  echo git이 없습니다.
  choice /c YN /m "winget으로 git을 설치할까요"
  if errorlevel 2 goto :end
  winget install --id Git.Git -e
  echo git 설치 후 이 창을 닫고 다시 실행하세요.
  goto :end
)
set "UVEXE="
for /f "delims=" %%U in ('where uv 2^>nul') do if not defined UVEXE set "UVEXE=%%U"
if not defined UVEXE if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe" set "UVEXE=%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe"
if not defined UVEXE (
  echo uv가 없습니다.
  choice /c YN /m "winget으로 uv를 설치할까요"
  if errorlevel 2 goto :end
  winget install --id astral-sh.uv -e
  echo uv 설치 후 이 창을 닫고 다시 실행하세요.
  goto :end
)
echo   확인 완료. uv: %UVEXE%
echo.
echo [2/5] 카카오톡 PC 확인
set "KAKAO="
for %%P in ("%ProgramFiles(x86)%\Kakao\KakaoTalk\KakaoTalk.exe" "%ProgramFiles%\Kakao\KakaoTalk\KakaoTalk.exe" "%LOCALAPPDATA%\Kakao\KakaoTalk\KakaoTalk.exe") do if exist "%%~P" set "KAKAO=%%~P"
if not defined KAKAO (
  echo   카카오톡 PC가 설치되어 있지 않습니다.
  echo   https://www.kakaocorp.com/page/service/service/KakaoTalk 에서 Windows용을 설치하고 로그인하세요.
  echo   설치는 나중에 해도 되며, MCP 설정은 계속 진행합니다.
) else (
  tasklist /fi "imagename eq KakaoTalk.exe" 2>nul | find /i "KakaoTalk.exe" >nul
  if errorlevel 1 (
    echo   카카오톡이 설치되어 있지만 실행 중이 아닙니다. 사용 전에 실행하고 로그인하세요.
  ) else (
    echo   카카오톡 실행 중.
  )
)
echo.
echo [3/5] 연결 설정 파일(.mcp.json) 만들기
set "UVJ=%UVEXE:\=/%"
set "SERVER=%~dp0kakao_auto\server.py"
set "SERVERJ=%SERVER:\=/%"
if exist ".mcp.json" (
  find "kakao_auto" ".mcp.json" >nul
  if not errorlevel 1 (
    echo   이미 새 방식으로 설정되어 있습니다. 건너뜁니다.
    goto :test
  )
  echo   기존 .mcp.json이 있습니다. 예전 방식이거나 다른 설정이 들어 있을 수 있습니다.
  choice /c YN /m "  .mcp.json.bak으로 백업하고 새로 만들까요"
  if errorlevel 2 goto :test
  copy /y ".mcp.json" ".mcp.json.bak" >nul
)
> ".mcp.json" echo {
>> ".mcp.json" echo   "mcpServers": {
>> ".mcp.json" echo     "kakao": {
>> ".mcp.json" echo       "command": "%UVJ%",
>> ".mcp.json" echo       "args": ["run", "--no-project", "--python", ">=3.10", "--with", "%SRC%", "--with", "mcp<2", "python", "%SERVERJ%"]
>> ".mcp.json" echo     }
>> ".mcp.json" echo   }
>> ".mcp.json" echo }
echo   .mcp.json 생성 완료.
:test
echo.
echo [4/5] 프로그램 내려받기 및 불러오기 시험 (카카오톡 화면을 건드리거나 메시지를 보내지 않습니다)
"%UVEXE%" run --no-project --python ">=3.10" --with "%SRC%" --with "mcp<2" python -c "import sys; sys.path.insert(0, r'kakao_auto'); import server as s; print('  불러오기 성공, 카카오톡 상태:', s.kakao_status()['guide'])"
if errorlevel 1 (
  echo   실패했습니다. 위 오류 메시지를 Claude에게 알려주세요.
  goto :end
)
echo.
echo [5/5] 다음 단계
echo   1) 카카오톡 PC 앱에 로그인합니다. (채팅방 창은 열어두지 않아도 됩니다)
echo   2) 이 폴더에서 Claude Code를 열고 /mcp 에서 kakao를 승인합니다.
echo   3) "kakao_health_check 해줘" 로 상태 확인부터 합니다.
echo   4) 방 이름이 기억나지 않으면 "우체 비슷한 방 찾아줘"처럼 말하면 비슷한 방을 추천합니다.
:end
echo.
pause
