---
name: warn-sed-inplace
enabled: true
event: bash
conditions:
  - field: command
    operator: regex_match
    pattern: \bsed\s+(-[a-zA-Z]*i|--in-place)
---
⚠️ `sed -i` 는 CRLF 파일을 통째로 LF 로 뒤집는다(Git Bash 실측) — 레포 파일 치환은 Edit 도구. 임시 파일·LF 파일만 sed.
