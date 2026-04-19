"""
Anti-scraping tests — run against local server: uvicorn app.main:app --reload
Usage: python tests/test_anti_scraping.py
"""
import sys
import time

import requests

BASE = "http://localhost:8000"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

SCRAPER_HEADERS = {
    "User-Agent": "python-requests/2.31.0",
}

HEADLESS_UA = {
    "User-Agent": "HeadlessChrome/124",
    "Accept": "*/*",
    "Accept-Language": "en-US",
}

# No Accept-Language — suspicious on content endpoints
MISSING_ACCEPT_LANG = {
    "User-Agent": "Mozilla/5.0 (compatible; MyBrowser/1.0)",
    "Accept": "*/*",
}

results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    status = "PASS" if passed else "FAIL"
    print(f"  [{status}] {name}{' — ' + detail if detail else ''}")


def run_tests() -> None:
    print(f"\n{'='*60}")
    print("NightOwl Anti-Scraping Test Suite")
    print(f"Target: {BASE}")
    print(f"{'='*60}\n")

    # ── Health ────────────────────────────────────────────────────
    print("[ Health ]")
    try:
        r = requests.get(f"{BASE}/health", timeout=5, headers=BROWSER_HEADERS)
        check("Server reachable", r.status_code == 200)
    except Exception as exc:
        check("Server reachable", False, str(exc))
        print("\nServer not running. Start with: uvicorn app.main:app --reload")
        sys.exit(1)

    # ── robots.txt ────────────────────────────────────────────────
    print("\n[ robots.txt ]")
    r = requests.get(f"{BASE}/robots.txt", headers=BROWSER_HEADERS)
    check("returns 200", r.status_code == 200)
    check("has Disallow", "Disallow" in r.text, r.text[:80])

    # ── Bot UA blocking ───────────────────────────────────────────
    print("\n[ Bot UA Blocking ]")
    r = requests.get(f"{BASE}/books", headers=SCRAPER_HEADERS)
    check("python-requests UA → 403", r.status_code == 403, f"got {r.status_code}")

    r = requests.get(f"{BASE}/books", headers=HEADLESS_UA)
    check("HeadlessChrome UA → 403", r.status_code == 403, f"got {r.status_code}")

    r = requests.get(f"{BASE}/books", headers=BROWSER_HEADERS)
    check("Real browser UA → 200", r.status_code == 200, f"got {r.status_code}")
    books = r.json() if r.ok else []

    # ── Missing header detection ──────────────────────────────────
    print("\n[ Missing Header Detection ]")
    if books:
        book_id = books[0]["id"]
        r = requests.get(
            f"{BASE}/books/{book_id}/chapters/1/content?session_token=fake",
            headers=MISSING_ACCEPT_LANG,
        )
        check("No Accept-Language on /content → 403", r.status_code == 403, f"got {r.status_code}")
    else:
        check("No Accept-Language detection", False, "no books in DB")

    # ── Session token enforcement ─────────────────────────────────
    print("\n[ Session Token Enforcement ]")
    if books:
        book_id = books[0]["id"]

        # No token → 422 (FastAPI required query param validation)
        r = requests.get(
            f"{BASE}/books/{book_id}/chapters/1/content",
            headers=BROWSER_HEADERS,
        )
        check("Missing session_token → 422", r.status_code == 422, f"got {r.status_code}")

        # Fake token → 403
        r = requests.get(
            f"{BASE}/books/{book_id}/chapters/1/content?session_token=fakefakefake",
            headers=BROWSER_HEADERS,
        )
        check("Fake session_token → 403", r.status_code == 403, f"got {r.status_code}")

        # Valid flow: getChapters → session_token → content
        r_ch = requests.get(f"{BASE}/books/{book_id}/chapters", headers=BROWSER_HEADERS)
        check("getChapters → 200", r_ch.status_code == 200, f"got {r_ch.status_code}")

        if r_ch.ok:
            data = r_ch.json()
            check("response has session_token", "session_token" in data,
                  str(list(data.keys()) if isinstance(data, dict) else type(data)))
            check("response has chapters list", isinstance(data.get("chapters"), list))

            session_token = data.get("session_token", "")
            free_chapters = [c for c in data.get("chapters", []) if c.get("free")]

            if free_chapters and session_token:
                ch_num = free_chapters[0]["chapterNumber"]
                r_c = requests.get(
                    f"{BASE}/books/{book_id}/chapters/{ch_num}/content"
                    f"?session_token={requests.utils.quote(session_token)}",
                    headers=BROWSER_HEADERS,
                )
                check("Valid token + free chapter → 200", r_c.status_code == 200, f"got {r_c.status_code}")
                if r_c.ok:
                    check("content key present", "content" in r_c.json())
            else:
                check("valid token flow", False, "no free chapters or empty token")
    else:
        print("  [SKIP] No books — skipping session token tests")

    # ── Rate limiting ─────────────────────────────────────────────
    print("\n[ Rate Limiting ]")
    print("  35 requests to /books (limit 60/min) — expect 0 blocked...")
    statuses = [requests.get(f"{BASE}/books", headers=BROWSER_HEADERS).status_code for _ in range(35)]
    blocked = statuses.count(429)
    check("35/60-per-min → 0 blocked", blocked == 0, f"{blocked} blocked")

    print("  35 requests to /books/{id}/chapters (limit 30/min) — expect some 429...")
    if books:
        book_id = books[0]["id"]
        statuses = [
            requests.get(f"{BASE}/books/{book_id}/chapters", headers=BROWSER_HEADERS).status_code
            for _ in range(35)
        ]
        blocked_429 = statuses.count(429)
        check("35/30-per-min → some 429", blocked_429 > 0, f"{blocked_429} got 429 (expected >0)")
    else:
        check("rate limit /chapters", False, "no books to test")

    # ── Honeypot (last — bans test runner IP on non-loopback only) ─
    print("\n[ Honeypot ]")
    r = requests.get(f"{BASE}/api/internal/book-list-cache", headers=BROWSER_HEADERS)
    check("Honeypot → 200 (stealth)", r.status_code == 200, f"got {r.status_code}")
    check("Response is list", isinstance(r.json(), list))
    # Loopback IP is whitelisted, so subsequent requests remain unblocked
    r_after = requests.get(f"{BASE}/books", headers=BROWSER_HEADERS)
    check("Localhost not banned after honeypot", r_after.status_code == 200, f"got {r_after.status_code}")

    # ── Summary ───────────────────────────────────────────────────
    print(f"\n{'='*60}")
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"Results: {passed}/{total} passed")
    if passed < total:
        print("\nFailed:")
        for name, ok, detail in results:
            if not ok:
                print(f"  ✗ {name}{' — ' + detail if detail else ''}")
    print(f"{'='*60}\n")
    return passed == total


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
