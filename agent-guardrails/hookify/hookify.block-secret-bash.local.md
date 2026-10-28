---
name: block-secret-bash
enabled: true
event: bash
action: block
pattern: (?i)(?:^|[;&|]\s*)\s*(rm|del|rmdir|mv|move|cp|copy|Remove-Item|Move-Item|Copy-Item|Clear-Content|Out-File|Set-Content)\b[^\n]*(KEYS[\\/]|session_states|[\\/]profile\b|backups?[\\/]|master\.key|\.env\b)
---

⛔ **시크릿·세션·프로필·백업을 향한 파일 조작 명령 차단**

`rm` · `mv` · `cp` · `Remove-Item` · `Set-Content` 계열이 아래를 향하고 있습니다:

- `*/KEYS/` · `*/session_states/` — 외부 서비스 인증 토큰·세션
- `*/profile/` — 자동화용 크롬 프로필 (로그인 세션 본체)
- `backups/` · `*/backup/` — 복구 원본
- `.env` · `master.key` — 시크릿

**왜 막나:**
이 중 아무거나 날아가면 외부 연동이 멈추거나 복구 경로가 사라집니다.
코드 원격 저장소를 두지 않는 운영이라 되돌릴 곳이 백업뿐입니다.

**읽기는 막지 않습니다** — `ls` · `Get-ChildItem` · 용량 확인은 그대로 됩니다.
