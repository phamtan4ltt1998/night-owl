# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
crawl4ai-setup          # first-time browser setup
python -m playwright install --with-deps chromium  # if browser errors

# Run server
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Docker (MySQL only)
docker-compose up -d

# Tests
python -m pytest tests/
python -m pytest tests/test_anti_scraping.py  # single file
```

## Environment

Copy `.env.example` → `.env`. Key vars:

| Var | Purpose |
|-----|---------|
| `DB_HOST/USER/PASSWORD/NAME` | MySQL connection |
| `JWT_SECRET` | Auth token signing |
| `CONTENT_SECRET` | Chapter content HMAC |
| `LOG_DIR` | Log output directory (default: `logs/`) |
| `LOG_LEVEL` | DEBUG/INFO/WARNING/ERROR (default: INFO) |
| `ANTI_SCRAPING_ENABLED` | Master switch for all anti-scraping layers |
| `SESSION_TOKEN_ENABLED/TTL` | Chapter content session tokens |

## Architecture

**FastAPI app** (`app/main.py`) — single large file containing all routes, startup/shutdown hooks, and background job registration.

**Data flow for reading:**
1. Client fetches `/books` → list from MySQL (`app/database.py`)
2. Client fetches `/books/{id}/chapters` → chapter list + session token issued
3. Client fetches chapter content with session token → HMAC-validated, rate-limited, content cleaned (title removed, links stripped to text)

**Anti-scraping layers** (`app/config.py`, `app/middleware/bot_guard.py`):
1. Honeypot IP ban
2. User-Agent blocking
3. Missing-header detection
4. Session token (HMAC, bound to uid+book_id, 10min TTL)
5. Rate limiting via slowapi

**Scheduled jobs** (APScheduler, started on FastAPI startup):
- `crawl_retry`: retries failed crawls every N minutes (env: `CRAWL_RETRY_INTERVAL_MINUTES`)
- `scheduled_scrape`: periodic crawl from `scrape_sources.json` sources

**Crawl pipeline** (`app/scraper.py`):
- Uses `crawl4ai` + Playwright/Patchright for JS-rendered pages
- Saves chapters as numbered markdown with title heading: `story/<slug>/0001-*.md`, `0002-*.md`, …
- After crawl: `upsert_story_from_dir()` syncs story+chapters into MySQL
- Note: Each markdown file starts with `# {title}\n\n{content}` format

**Content cleaning** (`app/main.py`, `_clean_chapter_content()`):
- Strips title heading from markdown (first `#` line) to prevent duplication in reader
- Removes markdown links `[text](url)` → keeps text only
- Applied in `/books/{id}/chapters/{num}/content` endpoint before returning

**Comments** (Wattpad-style paragraph + end-of-chapter):
- Single table `inline_comments`: `chapter_id`, `paragraph_id`, `user_id`, `content`, `parent_id` (for replies), `created_at`
- Paragraph ID convention:
  - `p0`, `p1`, `p2`... → inline paragraph comments (frontend assigns by index)
  - `_chapter` → sentinel for end-of-chapter comments (whole-chapter discussion)
- APIs (shared for both modes):
  - `GET /books/{book_id}/chapters/{chapter_number}/comment-counts` → `{paragraph_id: count}` (includes `_chapter` if present)
  - `GET /books/{book_id}/chapters/{chapter_number}/paragraphs/{paragraph_id}/comments?page=1&limit=10` → paginated comments + nested replies
  - `POST /comments/inline` (auth required) → body `{chapter_id, paragraph_id, content, parent_id?}`
  - `DELETE /comments/inline/{comment_id}` (auth required, owner only)
- DB function: `get_comment_counts(chapter_id)` excludes replies (only counts top-level via `parent_id IS NULL`)

**TTS pipeline** (`app/tts_service.py`):
- Reads markdown from `story-content/<slug>/`
- Outputs `.wav` to `outputs/audio/<slug>/`
- Two modes: `turbo` (fast) and `standard` (voice clone via `vieneu`)
- Voice clone (`/tts/story/clone`) uses reference audio from `input/sample-voice/`

**Database** (`app/database.py`, `init.sql`):
- MySQL via PyMySQL (no ORM)
- `init_db()` called at startup creates tables if missing
- `BOOK_META` dict in `database.py` holds hardcoded display metadata per slug

**Scrape scheduling config** (`scrape_sources.json`):
- `schedule.type`: `interval` (hours/minutes) or `cron`
- `schedule.active_window`: daily time window to constrain job execution
- Each source: `url`, `target_count`, `free_chapter_threshold`, `concurrency`, `enabled`

## API sequences & performance

See [`architecture/api-sequences.md`](architecture/api-sequences.md) for full endpoint flows (auth, browse, read, comment, user, notifications, TTS, crawl) + flagged hotspots needing optimization.

Hot-path summary (must stay fast, <100ms p95):
- `GET /books/paged` — verify composite indexes `(genre, read_count|rating|chapter_count)`
- `GET /books/search` — FTS already; watch `COUNT(*)` cost
- `GET /books/{id}/chapters` — **paginate**, currently returns all rows
- `GET /books/{id}/chapters/{n}/content` — file IO + DB; cache LRU + cache unlocked set
- `GET /books/{id}/chapters/{n}/comment-counts` — called per chapter open; cache 30s + index `(chapter_id, parent_id)`
- `POST /user/reading-progress` — high QPS; ensure UPSERT uses unique index

Cross-cutting fixes (P0):
1. Connection pool for `get_conn()` (currently open/close every request)
2. Wrap sync `pymysql` calls in `run_in_threadpool` for all async endpoints
3. Add `LIMIT` + cursor pagination to `GET /notifications` and `GET /books` (unpaged variant)
