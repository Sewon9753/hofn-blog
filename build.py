#!/usr/bin/env python3
"""
build.py — HOFN 블로그(blog.hofn.co.kr) 정적 생성기. 표준 라이브러리만 사용.

입력  content/*.md (front matter + 마크다운) · templates/*.html · site.json
출력  레포 루트: index.html · posts/<slug>/index.html · drafts/<slug>/index.html
      llms.txt · sitemap.xml · robots.txt · feed.xml · assets/style.css(그대로 유지)

규칙
  - draft: true  → drafts/ 아래 noindex 로만 생성. 목록·sitemap·llms.txt·feed 제외.
  - 글 1편 = 장면 문장 1(필러 1 + 스포크 1) + 9문 답(h2 9개, 「Q1.」~「Q9.」 또는 「9문-1」~「9문-9」 접두).
  - --strict: 발행 글(draft 아님)이 9문 h2 를 다 갖추지 못하면 실패.
  - markdown→HTML 은 자체 변환기가 기본(노드 간 결과 동일). --use-markdown-lib 로 python-markdown 사용 가능.

용법
  python3 build.py            # 빌드
  python3 build.py --strict   # 발행용(publish.sh 가 호출)
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import sys
from email.utils import format_datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
CONTENT_DIR = os.path.join(ROOT, "content")
TPL_DIR = os.path.join(ROOT, "templates")
SITE_JSON = os.path.join(ROOT, "site.json")

AI_CRAWLERS = ["GPTBot", "OAI-SearchBot", "PerplexityBot", "ClaudeBot", "Google-Extended"]
NINE_Q_RE = re.compile(r"^\s*(?:\[?\s*(?:Q|9문-)\s*([1-9])\b)", re.I)


# ---------------------------------------------------------------- front matter
def parse_front_matter(text):
    """'---' 로 감싼 key: value 블록을 dict 로. 값은 문자열·true/false·[a, b] 목록·'- ' 목록 지원."""
    meta, body = {}, text
    if text.startswith("---"):
        parts = text.split("\n---", 1)
        if len(parts) == 2:
            head, body = parts[0][3:], parts[1]
            body = body[1:] if body.startswith("\n") else body
            key = None
            for line in head.splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                if line.startswith((" ", "\t")) and key and line.strip().startswith("- "):
                    meta.setdefault(key, [])
                    if not isinstance(meta[key], list):
                        meta[key] = []
                    meta[key].append(line.strip()[2:].strip().strip('"').strip("'"))
                    continue
                if ":" not in line:
                    continue
                key, _, val = line.partition(":")
                key, val = key.strip(), val.strip()
                if val == "":
                    meta[key] = ""
                elif val.lower() in ("true", "false"):
                    meta[key] = val.lower() == "true"
                elif val.startswith("[") and val.endswith("]"):
                    meta[key] = [v.strip().strip('"').strip("'") for v in val[1:-1].split(",") if v.strip()]
                else:
                    meta[key] = val.strip('"').strip("'")
    return meta, body


# ---------------------------------------------------------------- markdown (자체)
_INLINE_RULES = [
    (re.compile(r"`([^`]+)`"), lambda m: "<code>%s</code>" % html.escape(m.group(1))),
    (re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"), r"<em>\1</em>"),
    (re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)"), r'<img src="\2" alt="\1" loading="lazy">'),
    (re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)"), r'<a href="\2">\1</a>'),
]


def inline_md(s):
    # 코드 스팬은 escape 된 채로 먼저 치환하고, 나머지 텍스트는 escape 후 규칙 적용
    out, pos = [], 0
    for m in re.finditer(r"`[^`]+`", s):
        out.append(_apply_inline(html.escape(s[pos:m.start()], quote=False)))
        out.append("<code>%s</code>" % html.escape(m.group(0)[1:-1]))
        pos = m.end()
    out.append(_apply_inline(html.escape(s[pos:], quote=False)))
    return "".join(out)


def _apply_inline(s):
    for rule, rep in _INLINE_RULES[1:]:
        s = rule.sub(rep, s)
    return s


def md_to_html(md):
    """제목·단락·굵게/기울임/링크/이미지/코드·목록(ul/ol)·인용·표·구분선·코드블록만 지원하는 소형 변환기."""
    lines = md.replace("\r\n", "\n").split("\n")
    out, i, n = [], 0, len(lines)
    para = []

    def flush_para():
        if para:
            out.append("<p>%s</p>" % inline_md(" ".join(x.strip() for x in para)))
            para.clear()

    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush_para(); i += 1; continue
        if stripped.startswith("```"):
            flush_para()
            buf, i = [], i + 1
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i]); i += 1
            out.append("<pre><code>%s</code></pre>" % html.escape("\n".join(buf)))
            i += 1; continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*$", stripped)
        if m:
            flush_para()
            lvl, text = len(m.group(1)), m.group(2)
            hid = slugify(text)
            out.append('<h%d id="%s">%s</h%d>' % (lvl, hid, inline_md(text), lvl))
            i += 1; continue
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            flush_para(); out.append("<hr>"); i += 1; continue
        if stripped.startswith(">"):
            flush_para()
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip()); i += 1
            out.append("<blockquote>%s</blockquote>" % md_to_html("\n".join(buf)))
            continue
        if "|" in stripped and i + 1 < n and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            flush_para()
            header = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")]); i += 1
            out.append(render_table(header, rows))
            continue
        lm = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
        if lm:
            flush_para()
            tag = "ol" if lm.group(2)[0].isdigit() else "ul"
            items = []
            while i < n:
                lm2 = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", lines[i])
                if not lm2:
                    break
                items.append(lm2.group(3)); i += 1
            out.append("<%s>%s</%s>" % (tag, "".join("<li>%s</li>" % inline_md(x) for x in items), tag))
            continue
        para.append(line); i += 1
    flush_para()
    return "\n".join(out)


def render_table(header, rows):
    th = "".join("<th>%s</th>" % inline_md(c) for c in header)
    trs = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % inline_md(c) for c in r) for r in rows)
    return "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (th, trs)


def md_to_html_lib(md):
    import markdown  # noqa
    return markdown.markdown(md, extensions=["tables", "fenced_code"])


# ---------------------------------------------------------------- helpers
def slugify(s):
    s = re.sub(r"[^\w\s가-힣-]", "", s.strip().lower())
    s = re.sub(r"[\s_]+", "-", s)
    return s.strip("-") or "section"


def render(tpl, ctx):
    """{{key}} 치환(HTML escape) · {{{key}}} 원문 삽입 · {{#key}}...{{/key}} 는 값이 참일 때만."""
    sec_re = re.compile(r"\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}", re.S)

    def section(m):
        key, body = m.group(1), m.group(2)
        return sec_re.sub(section, body) if ctx.get(key) else ""
    tpl = sec_re.sub(section, tpl)
    tpl = re.sub(r"\{\{\{(\w+)\}\}\}", lambda m: str(ctx.get(m.group(1), "")), tpl)
    tpl = re.sub(r"\{\{(\w+)\}\}", lambda m: html.escape(str(ctx.get(m.group(1), ""))), tpl)
    return tpl


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def iso_date(s):
    return dt.date.fromisoformat(str(s)[:10])


def rfc2822(d):
    return format_datetime(dt.datetime(d.year, d.month, d.day, 7, 30, tzinfo=dt.timezone(dt.timedelta(hours=9))))


def nine_question_check(body_html, site):
    """h2 에서 Q1~Q9 / 9문-1~9 접두를 찾아 빠진 번호 목록을 돌려준다."""
    found = set()
    for m in re.finditer(r"<h2[^>]*>(.*?)</h2>", body_html, flags=re.S):
        text = re.sub(r"<[^>]+>", "", m.group(1))
        q = NINE_Q_RE.match(html.unescape(text))
        if q:
            found.add(int(q.group(1)))
    return [i for i in range(1, 10) if i not in found]


def evidence_table(meta):
    """front matter evidence: ['항목 | 값 | 출처', ...] → 표 HTML. 없으면 빈 문자열."""
    rows = meta.get("evidence") or []
    if isinstance(rows, str):
        rows = [rows]
    parsed = [[c.strip() for c in r.split("|")] for r in rows if r.strip()]
    if not parsed:
        return ""
    parsed = [r + [""] * (3 - len(r)) for r in parsed]
    return render_table(["항목", "값", "출처"], parsed)


# ---------------------------------------------------------------- posts
def load_posts(site, use_lib):
    posts = []
    for fn in sorted(os.listdir(CONTENT_DIR)):
        if not fn.endswith(".md"):
            continue
        meta, body = parse_front_matter(read(os.path.join(CONTENT_DIR, fn)))
        if not meta.get("title"):
            raise SystemExit("content/%s: title 없음" % fn)
        if not meta.get("date"):
            raise SystemExit("content/%s: date 없음" % fn)
        slug = meta.get("slug") or slugify(re.sub(r"^_+", "", fn[:-3]))
        draft = bool(meta.get("draft", False))
        date = iso_date(meta["date"])
        updated = iso_date(meta.get("updated") or meta["date"])
        body_html = md_to_html_lib(body) if use_lib else md_to_html(body)
        product = dict(site["product_default"])
        if isinstance(meta.get("product"), str) and meta["product"]:
            product["name"] = meta["product"]
        if meta.get("product_url"):
            product["url"] = meta["product_url"]
        rel = ("drafts/%s/" if draft else "posts/%s/") % slug
        posts.append({
            "file": fn, "slug": slug, "draft": draft, "meta": meta,
            "title": meta["title"],
            "description": meta.get("description") or meta.get("scene") or site["site_description"],
            "scene": meta.get("scene", ""),
            "pillar": meta.get("pillar", ""),
            "spoke": meta.get("spoke", ""),
            "tags": meta.get("tags") or [],
            "date": date, "updated": updated,
            "body_html": body_html,
            "evidence_html": evidence_table(meta),
            "product": product,
            "rel": rel,
            "url": site["site_url"].rstrip("/") + "/" + rel,
            "image": meta.get("image", ""),
        })
    posts.sort(key=lambda p: (p["date"], p["slug"]), reverse=True)
    return posts


def ld_json(post, site):
    data = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "@id": post["url"],
        "mainEntityOfPage": {"@type": "WebPage", "@id": post["url"]},
        "headline": post["title"],
        "description": post["description"],
        "inLanguage": site["language"],
        "datePublished": post["date"].isoformat(),
        "dateModified": post["updated"].isoformat(),
        "author": {"@type": "Organization", "name": site["brand"]["name"], "url": site["brand"]["url"]},
        "publisher": {"@type": "Organization", "name": site["publisher"]["name"], "url": site["publisher"]["url"]},
        "isPartOf": {"@type": "Blog", "@id": site["site_url"] + "/", "name": site["site_name"]},
        "about": {
            "@type": "Product",
            "name": post["product"]["name"],
            "url": post["product"]["url"],
            "category": post["product"].get("category", ""),
            "brand": {"@type": "Brand", "name": site["brand"]["name"], "alternateName": site["brand"]["alternate_name"]},
            "manufacturer": {"@type": "Organization", "name": site["publisher"]["name"]},
        },
    }
    if post["tags"]:
        data["keywords"] = ", ".join(post["tags"])
    if post["image"]:
        data["image"] = post["image"]
    return json.dumps(data, ensure_ascii=False, indent=2)


def build(strict=False, use_lib=False):
    site = json.load(open(SITE_JSON, encoding="utf-8"))
    base = site["site_url"].rstrip("/")
    post_tpl = read(os.path.join(TPL_DIR, "post.html"))
    index_tpl = read(os.path.join(TPL_DIR, "index.html"))
    posts = load_posts(site, use_lib)
    published = [p for p in posts if not p["draft"]]
    drafts = [p for p in posts if p["draft"]]

    # 생성 전용 폴더는 비우고 다시 만든다(이름 바뀐 글의 잔재 방지)
    for d in ("posts", "drafts"):
        shutil.rmtree(os.path.join(ROOT, d), ignore_errors=True)

    problems = []
    for p in posts:
        missing = nine_question_check(p["body_html"], site)
        if missing and not p["draft"]:
            problems.append("%s: 9문 h2 누락 %s" % (p["file"], missing))
        if not p["scene"] and not p["draft"]:
            problems.append("%s: scene(장면 문장) 없음" % p["file"])
        ctx = {
            "site_name": site["site_name"], "site_url": base,
            "title": p["title"], "description": p["description"],
            "canonical": p["url"], "scene": p["scene"], "pillar": p["pillar"], "spoke": p["spoke"],
            "date": p["date"].isoformat(), "updated": p["updated"].isoformat(),
            "date_human": p["date"].strftime("%Y년 %m월 %d일"),
            "updated_human": p["updated"].strftime("%Y년 %m월 %d일"),
            "is_updated": p["updated"] != p["date"],
            "content": p["body_html"], "evidence": p["evidence_html"],
            "ld_json": ld_json(p, site),
            "product_name": p["product"]["name"], "product_url": p["product"]["url"],
            "brand_name": site["brand"]["name"], "brand_url": site["brand"]["url"],
            "publisher_name": site["publisher"]["name"],
            "tags_html": "".join('<li>%s</li>' % html.escape(t) for t in p["tags"]),
            "has_tags": bool(p["tags"]),
            "robots": "noindex, nofollow" if p["draft"] else "index, follow",
            "is_draft": p["draft"],
            "og_image": p["image"],
            "year": str(p["date"].year),
        }
        write(os.path.join(ROOT, p["rel"], "index.html"), render(post_tpl, ctx))

    if strict and problems:
        print("\n".join("오류: " + x for x in problems), file=sys.stderr)
        raise SystemExit(2)
    for x in problems:
        print("경고: " + x, file=sys.stderr)

    # index.html
    items = "".join(
        '<li class="post-item"><a href="/%s"><span class="post-title">%s</span></a>'
        '<span class="post-meta"><time datetime="%s">%s</time></span>'
        '%s</li>' % (
            p["rel"], html.escape(p["title"]), p["date"].isoformat(), p["date"].strftime("%Y.%m.%d"),
            ('<p class="post-scene">%s</p>' % html.escape(p["scene"])) if p["scene"] else "")
        for p in published)
    latest = published[0]["date"].isoformat() if published else dt.date.today().isoformat()
    write(os.path.join(ROOT, "index.html"), render(index_tpl, {
        "site_name": site["site_name"], "site_url": base, "description": site["site_description"],
        "canonical": base + "/", "post_list": items, "has_posts": bool(published),
        "brand_url": site["brand"]["url"], "publisher_name": site["publisher"]["name"],
        "year": str(dt.date.today().year),
        "ld_json": json.dumps({
            "@context": "https://schema.org", "@type": "Blog", "@id": base + "/",
            "name": site["site_name"], "url": base + "/", "description": site["site_description"],
            "inLanguage": site["language"],
            "publisher": {"@type": "Organization", "name": site["publisher"]["name"], "url": site["publisher"]["url"]},
            "about": {"@type": "Brand", "name": site["brand"]["name"], "alternateName": site["brand"]["alternate_name"], "url": site["brand"]["url"]},
        }, ensure_ascii=False, indent=2),
    }))

    # sitemap.xml
    urls = ['  <url><loc>%s/</loc><lastmod>%s</lastmod></url>' % (base, latest)]
    urls += ['  <url><loc>%s</loc><lastmod>%s</lastmod></url>' % (p["url"], p["updated"].isoformat()) for p in published]
    write(os.path.join(ROOT, "sitemap.xml"),
          '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n%s\n</urlset>\n' % "\n".join(urls))

    # robots.txt — Allow: / 유지 + AI 크롤러 명시 허용 + 초안 폴더 차단
    rb = ["User-agent: *", "Allow: /", "Disallow: /drafts/", ""]
    for ua in AI_CRAWLERS:
        rb += ["User-agent: %s" % ua, "Allow: /", "Disallow: /drafts/", ""]
    rb += ["Sitemap: %s/sitemap.xml" % base, ""]
    write(os.path.join(ROOT, "robots.txt"), "\n".join(rb))

    # llms.txt
    lt = ["# %s" % site["site_name"], "", "> %s" % site["site_description"], "",
          "- 브랜드: %s(%s) · 운영: %s · 자사몰: %s" % (site["brand"]["name"], site["brand"]["alternate_name"], site["publisher"]["name"], site["brand"]["url"]),
          "- 글 구조: 장면 문장 1(필러 1 + 스포크 1) + 9문 답(%s)" % " · ".join(site["nine_questions"]),
          "- 피드: %s/feed.xml · 사이트맵: %s/sitemap.xml" % (base, base), "", "## 글", ""]
    if published:
        lt += ["- [%s](%s): %s (%s)" % (p["title"], p["url"], p["scene"] or p["description"], p["date"].isoformat()) for p in published]
    else:
        lt += ["- (아직 발행된 글이 없습니다)"]
    lt += ["", "## 관련", "", "- [HOFN 자사몰](%s)" % site["brand"]["url"], ""]
    write(os.path.join(ROOT, "llms.txt"), "\n".join(lt))

    # feed.xml (RSS 2.0)
    fitems = "".join(
        "  <item>\n    <title>%s</title>\n    <link>%s</link>\n    <guid isPermaLink=\"true\">%s</guid>\n"
        "    <pubDate>%s</pubDate>\n    <description>%s</description>\n  </item>\n" % (
            html.escape(p["title"]), p["url"], p["url"], rfc2822(p["date"]), html.escape(p["description"]))
        for p in published)
    write(os.path.join(ROOT, "feed.xml"),
          '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n<channel>\n'
          '  <title>%s</title>\n  <link>%s/</link>\n  <description>%s</description>\n  <language>%s</language>\n'
          '  <atom:link href="%s/feed.xml" rel="self" type="application/rss+xml"/>\n%s</channel>\n</rss>\n' % (
              html.escape(site["site_name"]), base, html.escape(site["site_description"]), site["language"], base, fitems))

    print("빌드 완료: 발행 %d · 초안 %d · 출력 index.html, sitemap.xml, robots.txt, llms.txt, feed.xml" % (len(published), len(drafts)))
    for p in posts:
        print("  %s %s -> /%s" % ("[draft]" if p["draft"] else "[post] ", p["file"], p["rel"]))
    return 0


def main():
    ap = argparse.ArgumentParser(description="HOFN 블로그 정적 생성기")
    ap.add_argument("--strict", action="store_true", help="발행 글의 9문·장면 문장 누락을 오류로")
    ap.add_argument("--use-markdown-lib", action="store_true", help="python-markdown 이 있으면 그걸로 변환")
    a = ap.parse_args()
    use_lib = False
    if a.use_markdown_lib:
        try:
            import markdown  # noqa
            use_lib = True
        except ImportError:
            print("python-markdown 없음 → 자체 변환기 사용", file=sys.stderr)
    return build(strict=a.strict, use_lib=use_lib)


if __name__ == "__main__":
    sys.exit(main())
