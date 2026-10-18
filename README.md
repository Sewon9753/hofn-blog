# HOFN 블로그 (blog.hofn.co.kr)

GitHub Pages(main 루트) 정적 블로그. 생성기는 `build.py`(python3 표준 라이브러리만), 산출물은 루트에 커밋한다.

## 발행 절차 (5줄)
1. 글 추가: `content/<slug>.md` — front matter(`title`·`date`·`draft`·`scene`·`pillar`·`spoke`·`product`·`description`·`tags`·`evidence`) + 본문. 샘플 = `content/_sample-draft.md`.
2. 발행 전 금지어 게이트: 본문·제목·장면 문장을 JSON 문장 배열로 `python3 ~/.claude/skills/hofn-review-to-copy/scripts/validate.py <file.json>` 에 통과시킨다(exit 0 = 통과. 금지어 정본 `~/Dev/hofn-ai-org/marketing/compliance/cosmetic_ad_rules.json` 단일 원천).
3. 미리보기: `python3 build.py && python3 -m http.server 8000` → http://localhost:8000/ (초안은 `/drafts/<slug>/`).
4. `draft: false` 로 바꾸고 `./publish.sh --dry-run` 으로 변경 파일·커밋 메시지 확인.
5. `./publish.sh` — 빌드(--strict) → git add → commit → push(main). Pages 반영은 수 분 뒤 `curl -s https://blog.hofn.co.kr/sitemap.xml` 로 확인.

## 규칙
- 글 1편 = **장면 문장 1(필러 1 + 스포크 1)** + **9문 답**(h2 `Q1.`~`Q9.`: 누구에게 · 어떤 상황에 · 어떤 불편·목적 · 선택 기준 · 근거 · 유사 제품과의 차이 · 전후 변화 · 주의·한계 · 실사용자). `--strict` 빌드는 발행 글의 9문·장면 문장 누락을 오류로 막는다.
- `draft: true` 는 `/drafts/<slug>/` 에 `noindex` 로만 생성되고 목록·sitemap·llms.txt·feed·robots(Disallow) 에서 제외된다.
- 근거는 front matter `evidence:` 목록(`항목 | 값 | 출처`)에 적는다 — 시험명·번호 병기. 함량 귀속(15.5%=크림)은 `~/Dev/hofn-ai-org/marketing/creative-templates/product_facts.yaml` 가 정본.
- ld+json(BlogPosting + about Product/Brand/publisher)·llms.txt·sitemap.xml·robots.txt(AI 크롤러 UA 명시 허용)·feed.xml 은 빌드가 매번 다시 쓴다. 손으로 고치지 않는다.
- 사이트 상수(브랜드·운영사·기본 제품·9문 문구)는 `site.json`.
- markdown→HTML 은 자체 변환기가 기본(노드 간 산출물 동일). `--use-markdown-lib` 는 python-markdown 이 있을 때만 쓴다.
- 발행 버튼 = `publish.sh` 실행 = 🔒 대표 게이트(공개 게시). 초안·빌드·dry-run 까지는 자율.
