# restart_app_medium.ps1 — app.py(waitress)를 **반드시 일반 권한(Medium)** 으로 재시작한다.
#
# 언제 쓰나
#   관리자 PowerShell / 에이전트 셸처럼 **권한을 내릴 수 없는 자리**에서 app.py 를 재시작해야 할 때.
#   일반 사용자 셸이라면 그냥 평소 재시작 스크립트를 쓰면 된다 (이 스크립트는 우회로다).
#
# 왜 필요한가
#   관리자 셸에서 띄운 app.py 는 High 무결성(S-1-16-12288)이 된다.
#   05:51 일일 재시작 작업 · 워치독 작업은 둘 다 RunLevel=Limited(Medium)라
#   그 프로세스의 CommandLine 을 읽지 못하고(null), 일일 재시작 스크립트의
#   "opaque python 이 하나라도 있으면 아무것도 재시작하지 않는다" 가드에 걸려
#   05:51 에 **18개 프로세스 전부가 건너뛰어진다.**
#
# app.py 쪽 약속 (이 스크립트와 짝)
#   - app.py 는 이름 있는 뮤텍스로 한 번에 하나만 포트를 잡는다.
#   - `app.py --standby` 는 무거운 로딩을 끝낸 뒤 config\runtime\app_py_standby.json 을 쓰고 뮤텍스를 기다린다.
#   - /healthz 가 응답하면 기동 완료.
#
# 어떻게 내리나
#   RunLevel=Limited 인 일회용 예약작업을 만들어 그 안에서 띄운다. 예약작업이 Medium 토큰을
#   주므로 결과 프로세스가 Medium 이 된다.
#   ⚠ explorer.exe 우회는 쓰지 않는다 — 아무 것도 안 뜨고 조용히 실패한다(실측).
#     예약작업 경로가 실측으로 검증된 경로다.
#   ⚠ 작업 액션은 `cmd /c start ...` 로 **분리 실행**한다. 이렇게 해야 액션 프로세스가 즉시
#     끝나서 작업을 지워도 python 이 딸려 죽지 않는다.
#
# 실행 형태는 부팅 bat 의 launch 줄과 동일하게 맞춘다
#   (작업 디렉터리 = 앱 폴더 + **상대 경로** app.py).
#   전체 경로로 띄우면 커맨드라인이 달라져 일일 재시작 스크립트의 ` app.py` 매칭에서 빠진다.
#
#   사용: powershell -NoProfile -ExecutionPolicy Bypass -File <앱 폴더>\scripts\restart_app_medium.ps1
#   (이 파일은 <앱 폴더>\scripts\ 아래에 두는 것을 전제로 한다 - $BASE = 앱 폴더)

$ErrorActionPreference = "Stop"
# SSH 로 불리면 출력을 UTF-8 로 — 서버 콘솔 기본(CP949) 그대로면
# 받는 쪽(UTF-8 창)에서 한글이 깨진다. 서버 화면에서 직접 돌릴 땐 건드리지 않는다.
if ($env:SSH_CONNECTION) { try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { } }
$BASE = Split-Path -Parent $PSScriptRoot
$PORT = 80
$TASK = "TMP_AppMedium"
$HEALTH_TIMEOUT = 40

# 앞문 Caddy (caddy_ctl.py):
#   config\.env 에 APP_PORT 가 있으면 app.py 는 127.0.0.1:그 포트에 있고 80 은 Caddy 다.
#   ⛔ 그때 80 을 죽이면 Caddy 가 죽어 사이트 전체가 내려간다 → 앱 포트를 죽인다.
#   Caddy 가 재시작 몇 초 동안 요청을 붙잡아서 이용자는 안 튕긴다(스테이징 실측 오류 18 -> 0).
#   .env 는 상위 폴더 config\.env 가 있으면 그것만, 없으면 앱 폴더 config\.env.
$nxEnv = Join-Path (Split-Path -Parent $BASE) "config\.env"
if (-not (Test-Path $nxEnv)) { $nxEnv = Join-Path $BASE "config\.env" }
if (Test-Path $nxEnv) {
    $nxM = Select-String -Path $nxEnv -Pattern '^\s*APP_PORT\s*=\s*["'']?(\d+)' | Select-Object -First 1
    if ($nxM) { $PORT = [int]$nxM.Matches[0].Groups[1].Value }
}
if ($PORT -ne 80) {
    $nxFront = (Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique
    $nxFrontName = if ($nxFront) { (Get-Process -Id $nxFront[0] -ErrorAction SilentlyContinue).ProcessName } else { "" }
    if ($nxFrontName -ne "caddy") {
        Write-Host "[!] .env 는 Caddy 모드(app :$PORT)인데 80 번 주인이 Caddy 가 아닙니다('$nxFrontName') - 재시작 중단." -ForegroundColor Red
        Write-Host "    전환이 덜 됐거나 Caddy 가 죽었습니다: python scripts\caddy_ctl.py status" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "   앞문 Caddy 가 :80 을 붙잡는 중 - 재시작 동안 이용자는 안 튕깁니다 (app :$PORT)"
}

Write-Host "=================================================="
Write-Host "  app.py 재시작 (일반 권한 강제)"
Write-Host "=================================================="

# 1) 구문 검증 — 실패하면 기존 서버를 살려둔 채 중단
#   앱 폴더 아래 .py 전부(build·node_modules·.venv 빼고, 약 500개 0.8초). 2026-10-27 되돌리기 리허설:
#   예전엔 app.py 한 파일만 봐서 app.py 가 import 하는 모듈의 구문 오류가 통과했다.
#   대기 기동이 실패해 옛 서버는 살았지만, 디스크엔 깨진 코드가 남아 05:51 재시작이 그걸로 띄운다.
Write-Host ">> 구문 검증 (앱 폴더 아래 .py 전부)..."
& python -m compileall -q -x '[\\/](build|node_modules|\.venv)[\\/]' $BASE
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] 구문 오류 - 재시작 중단, 기존 서버 유지. 위 'Error compiling' 파일을 고치거나 직전 배포로 되돌린다." -ForegroundColor Red
    Write-Host "    ⚠ 디스크의 코드가 깨진 상태다 - 05:51 일일 재시작 전에 고치지 않으면 그때 사이트가 내려간다." -ForegroundColor Yellow
    exit 1
}
Write-Host "   통과"

# 2~4) 밑장빼기 (2026-09-28): 새 app.py 를 `--standby` 로 **먼저** 띄워 무거운 로딩(~13초)을 끝내 놓고,
#   "대기 중" 표시(config\runtime\app_py_standby.json)가 생긴 **뒤에** 옛 app.py 를 끈다 → 새 것이 뮤텍스를 넘겨받아
#   바로 포트를 연다. 빈틈은 1초 미만(스테이징 0.34초, 운영 0.6초대 — 예전 방식은 최장 19.7초). 그 빈틈은 Caddy 가 붙잡는다.
#   ⭐ 새 코드가 로딩 중에 죽으면 표시가 안 생긴다 → 옛 app.py 를 **끄지 않고** 멈춘다(사이트 유지).
$owner = (Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique
$MARKER = Join-Path $BASE "config\runtime\app_py_standby.json"
Remove-Item $MARKER -ErrorAction SilentlyContinue
$appArgs = if ($owner) { "app.py --standby" } else { "app.py" }

Write-Host ">> app.py 기동 (RunLevel=Limited, $appArgs)..."
Unregister-ScheduledTask -TaskName $TASK -Confirm:$false -ErrorAction SilentlyContinue
# `cmd /c` (예전 /k): 나중에 이 app.py 가 밑장빼기로 꺼지면 창도 같이 닫힌다 — /k 면 빈 창이 재시작마다 쌓였다.
# 로딩 중에 죽어도 기록은 logs\app_console.log·센트리에 남는다.
$action = New-ScheduledTaskAction -Execute 'cmd.exe' `
    -Argument ('/c start "web server" /D "{0}" cmd /c C:\Windows\py.exe {1}' -f $BASE, $appArgs)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TASK -Action $action -Principal $principal | Out-Null
Start-ScheduledTask -TaskName $TASK

if ($owner) {
    Write-Host ">> 새 app.py 가 로딩을 끝내고 대기할 때까지 (최대 90초)..."
    $deadline = (Get-Date).AddSeconds(90)
    while (-not (Test-Path $MARKER) -and ((Get-Date) -lt $deadline)) { Start-Sleep -Milliseconds 200 }
    if (-not (Test-Path $MARKER)) {
        Unregister-ScheduledTask -TaskName $TASK -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "[!] 새 app.py 가 대기 상태에 못 들어왔다 (로딩 오류?) - 옛 서버를 끄지 않고 중단. logs\app_console.log 확인" -ForegroundColor Red
        Write-Host "    ⚠ 디스크의 코드로는 안 뜬다 - 05:51 일일 재시작 전에 고치거나 직전 배포로 되돌리지 않으면 그때 사이트가 내려간다." -ForegroundColor Yellow
        exit 1
    }
    Write-Host "   대기 확인: $(Get-Content $MARKER -Raw)"
    # 옛 app.py 를 감싼 창(py.exe 런처 · `cmd /k ... app.py`, 부팅 bat·05:51·예전 재시작이 띄운 것)도 같이 닫는다.
    # 부모를 위로 따라가며 **이름이 py/cmd 이고 명령줄에 app.py 가 있는 것만** — 창 호스트(WT·OpenConsole)나 남의 cmd 는 안 건드린다.
    # CommandLine 이 안 보이는(High) 프로세스는 매칭이 안 돼서 건드리지 않는다.
    $shells = @()
    foreach ($procId in $owner) {
        $cur = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
        for ($i = 0; $i -lt 3 -and $cur; $i++) {
            $par = Get-CimInstance Win32_Process -Filter "ProcessId=$($cur.ParentProcessId)" -ErrorAction SilentlyContinue
            if (-not $par -or @('py.exe', 'cmd.exe') -notcontains $par.Name -or "$($par.CommandLine)" -notmatch 'app\.py') { break }
            $shells += $par.ProcessId
            $cur = $par
        }
    }
    $tKill = Get-Date
    foreach ($procId in $owner) {
        Write-Host "   옛 app.py 종료: PID $procId"
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
    foreach ($procId in $shells) {
        Write-Host "   옛 창 닫기: PID $procId"
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
}

# 5) 새 app.py 가 포트를 열 때까지 (0.1초 간격)
Write-Host ">> 부팅 확인 (최대 $HEALTH_TIMEOUT 초)..."
$ok = $false
$deadline = (Get-Date).AddSeconds($HEALTH_TIMEOUT)
while ((Get-Date) -lt $deadline) {
    $now = (Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique
    if ($now -and -not ($owner -and ($now | Where-Object { $owner -contains $_ }))) {
        try {
            Invoke-WebRequest -Uri "http://127.0.0.1:$PORT/healthz" -UseBasicParsing -TimeoutSec 5 | Out-Null
            $ok = $true; break
        } catch {
            if ($_.Exception.Response) { $ok = $true; break }
        }
    }
    Start-Sleep -Milliseconds 100
}
if ($owner -and $tKill) { Write-Host ("   이어받기: 옛 것 종료 후 {0:N2}초" -f ((Get-Date) - $tKill).TotalSeconds) }

# 일회용 작업 제거 (액션은 이미 끝났으므로 python 은 살아남는다)
Unregister-ScheduledTask -TaskName $TASK -Confirm:$false -ErrorAction SilentlyContinue

if (-not $ok) {
    Write-Host "[!] 서버 응답 없음 - 새로 뜬 콘솔 창의 로그를 확인하라" -ForegroundColor Red
    exit 1
}

$newPid = (Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique
Write-Host ""
Write-Host "완료! 포트 $PORT  PID: $newPid" -ForegroundColor Green
Write-Host "무결성 확인은 Get-CimInstance 로 CommandLine 이 보이는지로 갈음한다 (Medium 이면 보인다)."
