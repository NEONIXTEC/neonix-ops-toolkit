---
name: warn-master-commit
enabled: true
event: bash
pattern: (?i)(git\s+-C\s+["']?/?[a-z]?:?[\\/]myapp(?!_staging)["']?\s+commit|cd\s+["']?/?[a-z]?:?[\\/]myapp(?!_staging)["']?\s*(&&|;)\s*git\s+commit)
---
🚧 운영 체크아웃(master) 직행 커밋 금지: 커밋은 스테이징 워크트리(`C:\myapp_staging`, 브랜치 staging)에서, master 는 검사 통과 뒤 fast-forward 머지로만.
(`myapp` 을 자기 운영 체크아웃 폴더 이름으로 바꿔 쓴다.)
