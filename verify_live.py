#!/usr/bin/env python3
"""verify_live.py — push 뒤 /posts/<slug>/ 가 라이브에서 200 인지 폴링한다.

  python3 verify_live.py <slug> [<slug> ...]            # 최대 3분(15초 간격) 폴링
  python3 verify_live.py --once <slug>                  # 1회만 확인
  옵션: --timeout 180  --interval 15  --host blog.hofn.co.kr

슬러그마다 https → 실패 시 http 순으로 시도한다(Pages https_enforced=false, 인증서 미발급 상태 대비).
전부 200 이면 exit 0, 하나라도 미도달이면 exit 7. 표준 라이브러리만 사용.
"""
import argparse
import sys
import time
import urllib.error
import urllib.request

UA = "hofn-blog-verify/1 (+https://blog.hofn.co.kr)"
EXIT_UNREACHED = 7


def probe(url, timeout=10):
    """(status, note). 네트워크·SSL 오류는 status=None."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, ""
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # noqa: BLE001 — URLError·SSL·timeout 모두 "미도달"
        return None, type(e).__name__


def check_slug(host, slug):
    """https 먼저, 실패(비200·오류)면 http. (ok, 설명)"""
    notes = []
    for scheme in ("https", "http"):
        url = "%s://%s/posts/%s/" % (scheme, host, slug)
        status, note = probe(url)
        if status == 200:
            return True, "%s 200" % url
        notes.append("%s %s" % (url, status if status is not None else note))
    return False, " | ".join(notes)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slugs", nargs="+")
    ap.add_argument("--once", action="store_true", help="폴링 없이 1회만 확인")
    ap.add_argument("--timeout", type=int, default=180, help="최대 대기 초(기본 180)")
    ap.add_argument("--interval", type=int, default=15, help="폴링 간격 초(기본 15)")
    ap.add_argument("--host", default="blog.hofn.co.kr")
    a = ap.parse_args(argv)

    pending = list(dict.fromkeys(a.slugs))
    results = {}
    started = time.monotonic()
    attempt = 0
    while True:
        attempt += 1
        for slug in list(pending):
            ok, desc = check_slug(a.host, slug)
            results[slug] = (ok, desc)
            if ok:
                pending.remove(slug)
        elapsed = int(time.monotonic() - started)
        print("[%ds, %d회차] 도달 %d/%d" % (elapsed, attempt, len(a.slugs) - len(pending), len(a.slugs)))
        if not pending or a.once or elapsed + a.interval > a.timeout:
            break
        time.sleep(a.interval)

    print("--- 라이브 확인 결과 ---")
    for slug in a.slugs:
        ok, desc = results[slug]
        print("%s %s — %s" % ("OK " if ok else "MISS", slug, desc))
    if pending:
        print("미도달 %d건 (최대 %ds 대기%s)" % (len(pending), a.timeout, ", --once" if a.once else ""))
        return EXIT_UNREACHED
    return 0


if __name__ == "__main__":
    sys.exit(main())
