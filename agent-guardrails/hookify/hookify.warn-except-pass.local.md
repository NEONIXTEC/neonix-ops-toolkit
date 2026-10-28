---
name: warn-except-pass
enabled: true
event: file
conditions:
  - field: file_path
    operator: regex_match
    pattern: (?i)\.py$
  - field: content
    operator: regex_match
    pattern: except[^\n:]*:\s*\n\s*pass\b
---
⚠️ `except: pass` 금지: 예외를 좁히고 `logger.warning` 한 줄. 정말 무시할 자리면 이유를 주석으로 남기고 진행.
(넓은 except 가 값을 버리면 0원·빈 목록이 오류 없이 화면에 나간다 — 실패는 로그 한 줄로 보이게.)
