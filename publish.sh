#!/usr/bin/env bash
# publish.sh — 빌드 → git add → commit → push(main) 한 줄.
#   ./publish.sh                 # 발행
#   ./publish.sh --dry-run       # 빌드 + 변경 목록만, 커밋·push 없음
#   ./publish.sh -m "메시지"     # 커밋 메시지 지정
set -euo pipefail
cd "$(dirname "$0")"

DRY=0; MSG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    -m) shift; MSG="${1:-}" ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 1 ;;
  esac
  shift
done

python3 build.py --strict

if [ -z "$(git status --porcelain)" ]; then
  echo "변경 없음 — 커밋·push 생략"; exit 0
fi

git add -A
echo "--- 변경 파일 ---"; git status --short

[ -n "${MSG}" ] || MSG="publish: $(date '+%Y-%m-%d %H:%M')"
BODY="${MSG}

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017UFRVAXy275pTcq7pRCTTY"

if [ "${DRY}" = "1" ]; then
  echo "--- dry-run: 커밋 메시지 ---"; echo "${BODY}"
  git reset -q; echo "(스테이징 해제, 커밋·push 하지 않음)"; exit 0
fi

git commit -q -m "${BODY}"
git push origin main
echo "push 완료: $(git rev-parse --short HEAD)"
