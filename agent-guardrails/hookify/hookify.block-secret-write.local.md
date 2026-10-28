---
name: block-secret-write
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: (?i)(\.env$|master\.key|admins\.json|KEYS[\\/]|session_states|[\\/]profile[\\/]|[\\/]backup[\\/]|backups[\\/]|\.dump$|\.bak)
---

⛔ **시크릿·세션·백업 파일 쓰기 차단**

지금 쓰려는 경로가 아래 중 하나입니다:

- `.env` · `master.key` · `admins.json` — 시크릿 원본
- `*/KEYS/` · `*/session_states/` — 살아 있는 인증 토큰·세션
- `*/profile/` — 크롬 프로필 (수 GB, 로그인 세션 본체)
- `*/backup/` · `backups/` · `*.dump` · `*.bak` — 복구 원본

**왜 막나:**
프로필·세션이 깨지면 외부 서비스 로그인이 풀리고 수집이 멈춥니다. 재인증(OTP)부터
다시 밟아야 하고 그 사이 고객 화면에 구멍이 납니다. 백업을 덮어쓰면
되돌릴 곳 자체가 사라집니다.

**읽기·목록 확인은 이 규칙과 무관합니다.** 정말 써야 하면 사용자에게 먼저 확인받을 것.
