#!/usr/bin/env bash
# publish.sh — 빌드 → 자체 검수 게이트 → git add → commit → push(main) → 라이브 확인.
#   ./publish.sh                    # 발행(검수 PASS·autopilot enabled·주간 상한 이내일 때 자동 게시)
#   ./publish.sh --dry-run          # 빌드 + 게이트 + 변경 목록만, 커밋·push 없음
#   ./publish.sh --dry-run --no-llm # 검수에서 LLM 루브릭 생략(dry-run 전용 — 라이브 발행은 LLM 필수)
#   ./publish.sh -m "메시지"        # 커밋 메시지 지정
#
# 게이트(빌드 뒤·커밋 전, 순서대로) — 계획 hofn-ai-org/docs/superpowers/plans/2026-10-19-publish-autopilot.md §4-1·§4-5
#   ① 설정 파일 없음 또는 enabled≠true  → exit 4 (fail-closed)
#   ② 킬스위치 파일 존재                → exit 4
#   ③ 변경된 content/*.md(draft 아님) publish_review FAIL → exit 5
#   ④ 주간 상한(lanes.blog.weekly_cap) 초과 → exit 6
#   push 뒤 verify_live.py 가 /posts/<slug>/ 200 을 최대 3분 폴링 — 미도달 exit 7(발행은 되돌리지 않음)
# 환경변수: HOFN_ORG(기본 ~/Dev/hofn-ai-org) · HOFN_PY(기본 python3) · CLAUDE_SESSION_URL(커밋 트레일러 대체)
set -euo pipefail
cd "$(dirname "$0")"

HOFN_ORG="${HOFN_ORG:-$HOME/Dev/hofn-ai-org}"
REVIEW="$HOFN_ORG/marketing/compliance/publish_review.py"
CFG="$HOFN_ORG/ops/config/publish_autopilot.json"
PY="${HOFN_PY:-python3}"

DRY=0; MSG=""; NOLLM=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --no-llm) NOLLM=1 ;;
    -m) shift; MSG="${1:-}" ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 1 ;;
  esac
  shift
done

if [ "${NOLLM}" = "1" ] && [ "${DRY}" != "1" ]; then
  echo "--no-llm 은 --dry-run 과 함께일 때만 허용 — 라이브 발행은 LLM 검수 필수" >&2; exit 1
fi

"$PY" build.py --strict

if [ -z "$(git status --porcelain)" ]; then
  echo "변경 없음 — 커밋·push 생략"; exit 0
fi

# ---------------------------------------------------------------- 게이트 공용 파이썬
# 모드: config(①②) · posts(변경된 non-draft content → 파일 경로 줄 단위) · cap(④) · slugs(verify 대상)
gate() {
  PYTHONDONTWRITEBYTECODE=1 GATE_CFG="${CFG}" GATE_ORG="${HOFN_ORG}" GATE_CHANGED="${CHANGED_MD:-}" "$PY" - "$1" <<'PYEOF'
import datetime as dt
import json
import os
import re
import sys

mode = sys.argv[1]
ROOT = os.getcwd()
sys.path.insert(0, ROOT)
from build import parse_front_matter, slugify  # noqa: E402 — slug·front matter 해석은 build.py 와 동일 규칙

cfg_path = os.environ["GATE_CFG"]
org = os.environ["GATE_ORG"]
changed = [p for p in os.environ.get("GATE_CHANGED", "").split("\n") if p.strip()]


def load_cfg():
    if not os.path.isfile(cfg_path):
        print("autopilot 비활성 — 발행 중단 (설정 파일 없음: %s)" % cfg_path)
        sys.exit(4)
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:  # noqa: BLE001
        print("autopilot 비활성 — 발행 중단 (설정 파일 해석 실패: %s)" % e)
        sys.exit(4)
    if cfg.get("enabled") is not True:
        print("autopilot 비활성 — 발행 중단 (enabled=%r, %s)" % (cfg.get("enabled"), cfg_path))
        sys.exit(4)
    return cfg


def read_meta(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    meta, _ = parse_front_matter(text)
    return meta


def post_slug(path, meta):
    fn = os.path.basename(path)
    return meta.get("slug") or slugify(re.sub(r"^_+", "", fn[:-3]))


def non_draft_changed():
    out = []
    for p in changed:
        full = os.path.join(ROOT, p)
        if not (p.startswith("content/") and p.endswith(".md") and os.path.isfile(full)):
            continue  # 삭제된 파일·md 아님 → 검수 대상 아님
        meta = read_meta(full)
        if meta.get("draft") is True:
            continue
        out.append((p, meta))
    return out


if mode == "config":
    cfg = load_cfg()
    for ks in cfg.get("kill_switch_files") or []:
        path = os.path.expanduser(str(ks))
        if not os.path.isabs(path):
            path = os.path.join(org, path)
        if os.path.exists(path):
            print("킬스위치 — 발행 중단 (%s 존재)" % path)
            sys.exit(4)
    cap = (cfg.get("lanes") or {}).get("blog", {}).get("weekly_cap")
    print("autopilot enabled · 킬스위치 없음 · blog weekly_cap=%s" % cap)

elif mode == "posts":
    for p, _ in non_draft_changed():
        print(p)

elif mode == "slugs":
    for p, meta in non_draft_changed():
        print(post_slug(p, meta))

elif mode == "cap":
    cfg = load_cfg()
    cap = (cfg.get("lanes") or {}).get("blog", {}).get("weekly_cap")
    if cap is None:
        print("주간 상한 — 발행 중단 (lanes.blog.weekly_cap 없음)")
        sys.exit(6)
    cap = int(cap)
    today = dt.date.today()
    window_start = today - dt.timedelta(days=6)  # 오늘 포함 최근 7일. 미래 날짜도 이번 주 게시로 센다
    recent = []
    cdir = os.path.join(ROOT, "content")
    for fn in sorted(os.listdir(cdir)):
        if not fn.endswith(".md"):
            continue
        meta = read_meta(os.path.join(cdir, fn))
        if meta.get("draft") is True or not meta.get("date"):
            continue
        try:
            d = dt.date.fromisoformat(str(meta["date"])[:10])
        except ValueError:
            continue
        if d >= window_start:
            recent.append("%s(%s)" % (fn, d))
    # 이번 변경분 포함 count 가 cap 을 넘으면 중단 (cap=2 → 주 2편까지 허용)
    if len(recent) > cap:
        print("주간 상한 초과 — 발행 중단 (최근 7일 non-draft %d편 > weekly_cap %d): %s" % (len(recent), cap, ", ".join(recent)))
        sys.exit(6)
    print("주간 상한 이내 (최근 7일 non-draft %d편 / weekly_cap %d)" % (len(recent), cap))

else:
    print("gate: 알 수 없는 모드 %s" % mode)
    sys.exit(1)
PYEOF
}

# 변경된 content/*.md (상태 코드 제거, 리네임은 새 경로)
CHANGED_MD="$(git status --porcelain -- content/ | sed -E 's/^.{3}//; s/^.* -> //' | grep -E '\.md$' || true)"
export CHANGED_MD

echo "--- 게이트 ① autopilot 설정 · ② 킬스위치 ---"
gate config

echo "--- 게이트 ③ 자체 검수(publish_review) ---"
REVIEW_TARGETS="$(gate posts)"
if [ -z "${REVIEW_TARGETS}" ]; then
  echo "검수 대상 없음 (변경된 non-draft content/*.md 없음)"
else
  if [ ! -f "${REVIEW}" ]; then
    echo "검수 FAIL — 발행 중단 (publish_review.py 없음: ${REVIEW})" >&2; exit 5
  fi
  REVIEW_OPTS=()
  [ "${NOLLM}" = "1" ] && REVIEW_OPTS+=(--no-llm)
  FAILS=0
  while IFS= read -r f; do
    [ -n "${f}" ] || continue
    echo "· ${f}"
    if "$PY" "${REVIEW}" blog "${f}" ${REVIEW_OPTS[@]+"${REVIEW_OPTS[@]}"}; then
      echo "  PASS"
    else
      echo "  FAIL (exit $?)"; FAILS=$((FAILS + 1))
    fi
  done <<< "${REVIEW_TARGETS}"
  if [ "${FAILS}" -gt 0 ]; then
    echo "검수 FAIL — 발행 중단 (${FAILS}건)" >&2; exit 5
  fi
  echo "--- 게이트 ④ 주간 상한 ---"
  gate cap
fi

git add -A
echo "--- 변경 파일 ---"; git status --short

[ -n "${MSG}" ] || MSG="publish: $(date '+%Y-%m-%d %H:%M')"
SESSION_URL="${CLAUDE_SESSION_URL:-https://claude.ai/code/session_017UFRVAXy275pTcq7pRCTTY}"
BODY="${MSG}

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: ${SESSION_URL}"

if [ "${DRY}" = "1" ]; then
  echo "--- dry-run: 커밋 메시지 ---"; echo "${BODY}"
  git reset -q; echo "(스테이징 해제, 커밋·push 하지 않음)"; exit 0
fi

SLUGS="$(gate slugs | tr '\n' ' ')"

git commit -q -m "${BODY}"
git push origin main
echo "push 완료: $(git rev-parse --short HEAD)"

if [ -n "${SLUGS// /}" ]; then
  echo "--- 라이브 확인 ---"
  # shellcheck disable=SC2086
  "$PY" verify_live.py ${SLUGS} || { echo "라이브 미도달 — 발행은 완료됨(되돌리지 않음). 수 분 뒤 다시 확인: $PY verify_live.py --once ${SLUGS}" >&2; exit 7; }
fi
