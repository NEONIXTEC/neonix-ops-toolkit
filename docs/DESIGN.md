# 설계 노트

README 의 카드 세 장을 자세히 풀어 쓴 문서다. 각 절은 문제 → 해결 → 결과 순서다.

- [1. 윈도우 서버 무중단 재시작과 권한 격리](#1-윈도우-서버-무중단-재시작과-권한-격리)
- [2. 공개키 암호화 백업](#2-공개키-암호화-백업)
- [3. AI 에이전트 협업 거버넌스](#3-ai-에이전트-협업-거버넌스)
- [4. 그 밖에](#4-그-밖에)

---

## 1. 윈도우 서버 무중단 재시작과 권한 격리

### 문제
- 서버는 매일 05:51 에 웹서버·수집기·워커·봇 18개를 다시 띄우고, 이 재시작 작업은 일반 권한(Medium)으로 돈다.
- 윈도우 무결성 격리 때문에 Medium 작업은 **관리자 셸에서 뜬 High 프로세스의 명령줄을 읽지 못한다**(`null`). 재시작 스크립트의 "정체를 모르는 python 이 하나라도 있으면 아무것도 건드리지 않는다" 안전장치가 걸려, High 프로세스 하나가 18개 전체의 재시작을 멈춘다.
- 윈도 OpenSSH 셸은 늘 관리자 + 세션 0 이라 원격으로 무언가를 켜는 순간 이 상태가 된다.
- app.py 는 단일 인스턴스 뮤텍스라 "죽이고 다시 켜기"밖에 안 되고, 그 몇 초 동안 :80 이 빈다.

### 해결
- [`restart_app_medium.ps1`](../windows-ops/restart_app_medium.ps1)
  - 관리자 셸에서 불려도 결과 프로세스는 반드시 Medium. 일회용 예약작업(`RunLevel=Limited`, `LogonType=Interactive`)으로 바탕화면 세션에서 띄운다. `explorer.exe` 우회는 실측에서 조용히 실패해서 버렸다.
  - **대기 인계(standby handover)**: 새 app.py 를 `--standby` 로 먼저 띄워 무거운 로딩을 끝내 두고, "대기 중" 표시가 생긴 **뒤에만** 옛 것을 끈다. 새 코드가 로딩 중에 죽으면 옛 서버를 끄지 않고 멈춘다.
  - 끄기 전에 앱 폴더 전체를 `compileall` 로 검사한다. app.py 한 파일만 보면 import 되는 모듈의 구문 오류가 빠진다.
- 앞문 Caddy ([`Caddyfile`](../windows-ops/Caddyfile), [`caddy_ctl.py`](../windows-ops/caddy_ctl.py)) — 재시작 몇 초 동안 요청을 붙잡았다가 새 app.py 로 넘긴다. 실험에서 찾은 함정(호스트 지정 시 빈 200, X-Forwarded-For 와 내부 요청 판정, 루프백 바인드, 재시도 45초)을 주석에 남겼다.
- [`restart_one_medium.ps1`](../windows-ops/restart_one_medium.ps1) — 웹서버가 아닌 프로세스도 같은 방식으로 하나씩. 끝나면 새 프로세스가 **Medium·세션 1** 인지 토큰을 직접 읽어 확인한다([`nx_integrity.ps1`](../windows-ops/nx_integrity.ps1)).
- [`integrity_audit.ps1`](../windows-ops/integrity_audit.ps1) — 읽기 전용 점검. 워치독이 30분마다 돌려 High·SYSTEM·세션 0 이 보이면 알린다. 점검 자체가 실패하거나 아무것도 못 보면 '정상'이 아니라 실패로 끝난다(fail-closed).
- [`agent_ship.py`](../windows-ops/agent_ship.py) — AI 에이전트용 배포 관문.
  - 관리자 셸이면 거부, staging == master 와 앞문 Caddy 생존을 확인한 뒤 **오너가 바탕화면에서 누르는 것과 같은 bat** 을 새 콘솔로 한 번 띄운다.
  - 오너가 그 턴에 채팅으로 승인했을 때만 부른다. 원격 명령에 들어가는 인자는 모양 검사를 먼저 한다.
- 커밋 단계에서도 막는다: `security_scan.py` S6 이 예약작업 XML 의 `HighestAvailable`, PowerShell 의 `#Requires -RunAsAdministrator` 를 차단한다.
- 운영 규칙 전체: [WINDOWS_MEDIUM_PROCESS_OPS.md](WINDOWS_MEDIUM_PROCESS_OPS.md)

### 결과
- 재시작 대기 최대 **19.7초 → 1초 미만** (옛 방식 운영 전환 때 최장 대기 19.7초 · 새 방식 스테이징 0.34초 · 운영 0.6초대), 스테이징 실측 오류 18 → 0.
- 운영 권한 점검 실측 21개 프로세스 전부 Medium·세션 1.

---

## 2. 공개키 암호화 백업

### 문제
- 풀백업에는 `.env`·마스터 키·외부 연동 키·DB 덤프가 다 들어간다(약 825MB).
- 오프사이트 폴더를 동기화 클라이언트가 클라우드로 올리는 구조라면, 클라우드 계정 하나가 곧 운영 비밀 전체다.
- 무인 일일 백업이라 비밀번호 방식(7z 등)은 비밀번호가 서버에 있어야 해서 의미가 없다.

### 해결 ([`backup-encryption/`](../backup-encryption))
- 서버는 **공개키로 잠그기만** 한다. 여는 개인키는 오너 PC 에 비밀번호로 잠가 둔다. 서버를 턴 사람도 백업을 못 연다.
- **fail-closed**: age 나 공개키가 없거나 결과 파일의 헤더·크기가 이상하면 아무것도 안 내보내고 실패한다. "일단 평문으로 올리기" 폴백은 일부러 없다.
- **`.part` 로 쓰고 검증한 뒤에만 최종 이름으로 rename** — 동기화 클라이언트가 반쪽짜리를 올리지 않는다.
- 공개키 파일에 개인키(`AGE-SECRET-KEY-`)가 섞여 있으면 거부한다. 그 파일이 서버에 있다는 것 자체가 설계 위반이다.
- [`decrypt_backup.py`](../backup-encryption/decrypt_backup.py) — 오너 PC 에서 월 1회 복원 연습. 복호화 후 아카이브를 끝까지 읽어 검증하고 평문은 지운다.
- 코드로 강제: `security_scan.py` S6 이 "오프사이트 경로에 쓰는데 age 를 거치지 않는 코드"를 커밋 단계에서 막는다. 워치독은 오프사이트에 `.age` 가 아닌 백업이 보이면 알린다.

### 결과
- 오프사이트에는 `*.age` 만 간다. 서버·클라우드 어디에도 복호화 키가 없다.
- 암호화 → 복호화 → 끝까지 읽기 왕복을 월 1회 연습한다.

---

## 3. AI 에이전트 협업 거버넌스

### 문제
- 규칙은 문서에 있어도 새 코드 경로가 그 규칙을 안 탈 수 있다.
- 에이전트는 세션마다 기억이 없고, 운영자가 에이전트가 만든 diff 를 전부 읽을 수는 없다.
- 작성자가 안 남으면 사람 커밋과 에이전트 커밋을 구분할 수 없다.

### 해결 ([`agent-guardrails/`](../agent-guardrails))
- [`security_scan.py`](../agent-guardrails/security_scan.py) — 위험 유형 하나당 규칙 하나, S1~S7.
  - S1 공개 static 폴더의 새 파일·개인정보 · S2 비밀값 · S3 전화·주민번호 · S4 로그인도 상한도 없는 공개 라우트 · S5 시각 없는 세션 플래그 · S6 암호화 안 거친 오프사이트 쓰기·관리자 권한 · S7 git 원격 주소의 토큰.
  - pre-commit 과 머지 관문 두 곳에서 **새로 들어온 줄만** 본다. 기존 위반은 텍스트 없이 지문(sha1)만 기준선에 두고, 비밀값(S2)은 파일 통째 면제를 받지 못한다.
  - HIGH 는 예외 없음, MED 만 그 줄에 `security-ok: <이유>`. 결과에 비밀값 원문을 절대 찍지 않는다.
  - git 훅이라 어떤 에이전트든 같은 관문에 걸리고, `--no-verify` 로 건너뛴 커밋은 머지 관문이 범위로 다시 잡는다.
- [`lint_commit_agent.py`](../agent-guardrails/lint_commit_agent.py) — 모든 커밋 끝에 `Agent: Claude Opus 5.5` / `Agent: human` 트레일러.
- [`lint_doc_meta.py`](../agent-guardrails/lint_doc_meta.py) — 코드만 바뀌고 문서가 0개인 커밋 거부(`Docs-skip: 이유` 로만 통과). 새 모듈은 근거 문서를 가리키는 머리 주석이 필수라, 다음 에이전트가 고치기 전에 규칙을 찾는다.
- [`lint_eol.py`](../agent-guardrails/lint_eol.py) — 도구마다 줄바꿈을 다르게 저장하면 3줄 수정이 154줄 diff 가 된다. 섞임·통째 뒤집힘·`\r\r\n` 을 막는다. `.bat` 이 LF 가 되면 윈도우에서 **아무 증상 없이** 안 뜬다.
- [`hookify/`](../agent-guardrails/hookify) — 에이전트가 파일을 고치는 **그 순간** 거는 규칙(비밀 파일 쓰기 차단, heredoc 파이썬으로 윈도 경로가 깨지는 패턴 경고 등).
- [`review-skill/`](../agent-guardrails/review-skill) — 일반론 대신 **이 레포의 사고 기록**을 근거로 리뷰하는 스킬 틀. 근거 문서가 없는 지적은 `opinion` 으로만 쓰게 한다. hookify 규칙 패턴을 복사하지 않고 그 파일에서 직접 읽어서, "편집 때 경고"와 "리뷰 때 지적"이 한 소스에서 나온다.

### 결과
- 편집 순간(hookify) → 커밋(git 훅) → 머지(관문 + 전체 테스트) → 배포(오너 승인 + agent_ship) 4단 관문.
- 어떤 에이전트가 쓰든 같은 규칙에 걸리고, 모든 커밋에 작성자가 남는다.

---

## 4. 그 밖에

- **CSP `'unsafe-inline'` 제거** — 화면 28개의 인라인 `onclick` 을 `data-nx-click` + 페이지 JS 등록으로 옮기는 30줄 디스패처([`nx_on.js`](../csp-migration/nx_on.js)). 손으로 올리는 `?v=` 는 CDN 7일 캐시를 못 비켜 가서, 내용 해시를 URL 에 붙이는 [`cache_bust.py`](../csp-migration/cache_bust.py) 를 만들었다. 절차와 주의: [csp-migration/README.md](../csp-migration/README.md)
- **프로세스 간 공유 비용 상한** — SMS·유료 AI 상한을 프로세스 메모리에 두면 재시작마다 0 이 되고, 대기 인계로 두 프로세스가 겹치는 동안 상한이 2배가 된다. PostgreSQL advisory lock 기반의 공유 상한으로 옮겼다(앱 내부 코드라 여기 없고, 리뷰 스킬 체크리스트 한 줄로 남겼다).
