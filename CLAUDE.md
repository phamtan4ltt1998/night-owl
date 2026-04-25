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
3. Client fetches chapter content with session token → HMAC-validated, rate-limited

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
- Saves chapters as numbered markdown: `story/<slug>/0001-*.md`, `0002-*.md`, …
- After crawl: `upsert_story_from_dir()` syncs story+chapters into MySQL

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
