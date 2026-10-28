# 윈도우 서버에서 프로세스를 "일반 권한"으로만 다루기

`windows-ops/` 스크립트들이 전제하는 운영 규칙이다.

## 왜

- 윈도우는 프로세스마다 무결성 수준(Low / **Medium** / **High** / SYSTEM)을 가진다. 관리자 셸에서 띄우면 High.
- 일반 권한(Medium)으로 도는 예약작업·워치독은 High 프로세스의 `CommandLine` 을 읽지 못한다(`null`).
- 일일 재시작 스크립트에 "정체를 모르는 python 이 하나라도 있으면 아무것도 재시작하지 않는다" 안전장치가 있었고, 그래서 High 하나 때문에 05:51 재시작이 **18개 전부** 건너뛰었다.
- 윈도 OpenSSH 로 들어온 셸은 **관리자 + 세션 0**(바탕화면이 없는 세션)이다. 거기서 띄운 프로세스는 High 이고 창도 안 보인다. 원격으로 "그냥 켜기"가 곧 사고다.

## 규칙

1. 서비스 프로세스는 전부 **Medium, 세션 1**(로그인한 바탕화면)에서 돈다.
2. 원격·에이전트 셸에서 재시작할 땐 직접 띄우지 않는다. 일회용 예약작업을 만들어 그 안에서 띄운다.
   - `New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited`
   - 작업 액션은 `cmd /c start ...` 로 **분리 실행** — 액션이 바로 끝나야 작업을 지워도 자식 python 이 같이 죽지 않는다.
3. 띄운 뒤엔 **토큰을 직접 읽어** Medium·세션 1 인지 확인한다(`nx_integrity.ps1`). "아마 됐겠지"로 끝내지 않는다.
4. 예약작업 XML 은 `LeastPrivilege`. `HighestAvailable` 과 `#Requires -RunAsAdministrator` 는 커밋 단계에서 막는다(`security_scan.py` S6).
5. 웹서버(app.py)는 따로: 대기 인계 재시작(`restart_app_medium.ps1`) + 앞문 Caddy.
6. 하루 한 번이 아니라 상시 감시: `integrity_audit.ps1 -Json` 을 워치독이 30분마다 돌려 `bad > 0` 이면 알린다.

## 실측으로 버린 방법

- `explorer.exe <bat>` 로 권한 내리기 — 스테이징에서 아무것도 안 뜨고 조용히 실패했다(종료 코드도 0).
- 전체 경로로 app.py 띄우기 — 명령줄 모양이 달라져 일일 재시작의 ` app.py` 매칭에서 빠졌다. 부팅 bat 과 같은 모양(작업 폴더 + 상대 경로)으로 띄운다.
- 화면 자동화 워커처럼 창이 필요한 프로세스를 SSH 에서 직접 띄우기 — 세션 0 이라 창이 안 보여 자동화가 멈췄다.
