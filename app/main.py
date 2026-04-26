import asyncio
import base64
import hashlib
import hmac
import logging
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.concurrency import run_in_threadpool

from app.config import (
    CRAWL_RETRY_INTERVAL_MINUTES, CRAWL_RETRY_MAX_ATTEMPTS,
    HONEYPOT_ENABLED,
    RATE_LIMIT_BOOKS, RATE_LIMIT_CHAPTERS, RATE_LIMIT_CONTENT,
    SESSION_TOKEN_ENABLED, SESSION_TOKEN_TTL,
)
from app.logging_setup import setup_logging
from app.scrape_job import get_schedule_kwargs, load_config, run_scheduled_scrape

setup_logging()
from app.middleware.bot_guard import BANNED_IPS, _is_private, bot_guard_middleware  # noqa: E402
from app.scraper import DEFAULT_STORY_URL, StoryScraper  # noqa: E402
from app.tts_service import StoryTTSService  # noqa: E402
from app.database import (  # noqa: E402
    init_db, get_conn, upsert_story_from_dir, update_book,
    get_or_create_user, update_user_profile,
    add_linh_thach, get_linh_thach_history, claim_daily_reward,
    unlock_chapter, get_unlocked_chapter_numbers,
    upsert_reading_progress, get_reading_history,
    save_failed_crawl, get_pending_failed_crawls,
    mark_crawl_resolved, increment_crawl_retry,
    get_existing_slugs,
    increment_chapter_view,
    get_books_paged,
)

logger = logging.getLogger("nightowl.crawl")

# ── In-memory category crawl jobs ─────────────────────────────────────────────
_crawl_jobs: dict[str, dict] = {}


def _meta_kwargs(src: dict) -> dict:
    return {
        "story_name": src.get("story_name", ""),
        "story_author": src.get("story_author", ""),
        "story_genre": src.get("story_genre", ""),
        "story_status": src.get("story_status", ""),
        "story_description": src.get("story_description", ""),
        "story_cover": src.get("story_cover", ""),
    }

# ── Rate limiter ───────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="NightOwl API", version="2.0.0")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "https://localhost:5173", "https://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(bot_guard_middleware)

init_db()

scraper = StoryScraper(output_root="story")

# ── Crawl retry scheduler ──────────────────────────────────────────────────────

_scheduler = AsyncIOScheduler()


async def _retry_failed_crawls() -> None:
    """Chạy định kỳ: thử lại các request crawl bị lỗi chưa resolve."""
    pending = get_pending_failed_crawls(max_retries=CRAWL_RETRY_MAX_ATTEMPTS)
    if not pending:
        return
    logger.info("[retry] Found %d pending failed crawl(s)", len(pending))
    for rec in pending:
        rec_id = rec["id"]
        try:
            result = await scraper.scrape_story(
                story_url=rec["story_url"],
                story_limit=rec["story_limit"],
                start_story_from=rec["start_story_from"],
            )

            if result.get("mode") == "single_story":
                slug = result.get("story_slug")
                if slug and result.get("status") != "already_updated":
                    upsert_story_from_dir(
                        slug,
                        free_chapter_threshold=rec["free_chapter_threshold"],
                        source_url=rec["story_url"],
                        **_meta_kwargs(result),
                    )
            elif result.get("mode") == "listing_page":
                for story in result.get("stories", []):
                    slug = story.get("story_slug")
                    if slug and story.get("status") != "already_updated":
                        try:
                            upsert_story_from_dir(
                                slug,
                                free_chapter_threshold=rec["free_chapter_threshold"],
                                source_url=story.get("story_url", rec["story_url"]),
                                **_meta_kwargs(story),
                            )
                        except Exception:  # noqa: BLE001
                            pass

            mark_crawl_resolved(rec_id)
            logger.info("[retry] Resolved failed crawl id=%d url=%s", rec_id, rec["story_url"])
        except Exception as exc:  # noqa: BLE001
            increment_crawl_retry(rec_id, str(exc))
            logger.warning("[retry] Still failing id=%d attempt=%d err=%s",
                           rec_id, rec["retry_count"] + 1, exc)


@app.on_event("startup")
async def _start_scheduler() -> None:
    # Job 1: retry failed crawls
    _scheduler.add_job(
        _retry_failed_crawls,
        "interval",
        minutes=CRAWL_RETRY_INTERVAL_MINUTES,
        id="crawl_retry",
        replace_existing=True,
    )

    # Job 2: scheduled scrape từ scrape_sources.json
    scrape_config = load_config()
    enabled_sources = [s for s in scrape_config.get("sources", []) if s.get("enabled", True)]
    if not enabled_sources:
        logger.warning("[scheduler] Không có source nào trong config — bỏ qua đăng ký scheduled_scrape job.")
    else:
        schedule_kwargs = get_schedule_kwargs(scrape_config)
        _scheduler.add_job(
            run_scheduled_scrape,
            id="scheduled_scrape",
            replace_existing=True,
            **schedule_kwargs,
        )
        logger.info("[scheduler] Scheduled scrape job registered — sources=%d %s", len(enabled_sources), schedule_kwargs)

    _scheduler.start()
    logger.info("[scheduler] Crawl retry job started — interval=%dm max_attempts=%d",
                CRAWL_RETRY_INTERVAL_MINUTES, CRAWL_RETRY_MAX_ATTEMPTS)


@app.on_event("shutdown")
async def _stop_scheduler() -> None:
    _scheduler.shutdown(wait=False)
tts_service = StoryTTSService(story_content_root="story-content", output_root="outputs/audio")

# ── Content session token (anti-scraping) ──────────────────────────────────────
# Protects chapter content: caller must first fetch /books/{id}/chapters to get token.
# Token bound to uid + book_id, expires in 10 minutes.

CONTENT_SECRET = os.getenv("CONTENT_SECRET", "nightowl-content-secret-change-me")
_SESSION_TOKEN_TTL = SESSION_TOKEN_TTL  # from config (env: SESSION_TOKEN_TTL, default 600s)


def _make_session_token(uid: int, book_id: int) -> str:
    exp = int(time.time()) + _SESSION_TOKEN_TTL
    payload = f"{uid}:{book_id}:{exp}"
    sig = hmac.new(CONTENT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode().rstrip("=")


def _verify_session_token(token: str, uid: int, book_id: int) -> bool:
    try:
        padded = token + "=" * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode()).decode()
        # format: uid:book_id:exp:sig
        parts = decoded.rsplit(":", 1)
        if len(parts) != 2:
            return False
        body, sig = parts
        body_parts = body.split(":")
        if len(body_parts) != 3:
            return False
        t_uid, t_bid, exp = body_parts
        if int(exp) < time.time():
            return False
        if int(t_uid) != uid or int(t_bid) != book_id:
            return False
        expected = hmac.new(CONTENT_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False


class CrawlRequest(BaseModel):
    story_url: str = DEFAULT_STORY_URL
    story_limit: int | None = Field(default=None, ge=1)
    start_story_from: int = Field(default=1, ge=1)
    free_chapter_threshold: int = Field(default=20, ge=0, description="Chương từ 1 đến giá trị này sẽ miễn phí. Chương sau sẽ trả Linh Thạch.")


class StoryTTSRequest(BaseModel):
    story_name: str = Field(..., min_length=1)
    chapters: list[int] = Field(..., min_length=1)
    mode: str = Field(default="turbo")


class StoryCloneTTSRequest(BaseModel):
    story_name: str = Field(..., min_length=1)
    chapters: list[int] = Field(..., min_length=1)
    mode: str = Field(default="turbo")
    reference_audio_path: str = Field(
        default="input/sample-voice/nguyen-ngoc-ngan/nguyen_ngoc_ngan.mp3",
        min_length=1,
    )
    reference_text: str | None = Field(
        default=None,
        description=(
            "Transcript cua audio mau (mode standard). Bo trong thi doc file "
            "reference.txt / reference_text.txt / 'reference text' trong cung thu muc voi reference_audio_path."
        ),
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
async def robots_txt() -> str:
    return (
        "User-agent: *\n"
        "Disallow: /books/\n"
        "Disallow: /user/\n"
        "Crawl-delay: 10\n"
    )


@app.get("/api/internal/book-list-cache", include_in_schema=False)
async def honeypot(request: Request):
    """Hidden honeypot — non-private clients hitting this are auto-banned (if HONEYPOT_ENABLED)."""
    if HONEYPOT_ENABLED:
        ip = request.client.host if request.client else ""
        if ip and not _is_private(ip):
            BANNED_IPS.add(ip)
    # Return 200 so attacker doesn't know they were flagged
    return []


@app.post("/crawl")
async def crawl_story(request: CrawlRequest) -> dict:
    try:
        result = await scraper.scrape_story(
            story_url=request.story_url,
            story_limit=request.story_limit,
            start_story_from=request.start_story_from,
        )

        # Upsert vào DB sau khi crawl xong
        db_results: list[dict] = []

        if result.get("mode") == "single_story":
            slug = result.get("story_slug")
            story_url = result.get("story_url", "")
            if slug:
                if result.get("status") == "already_updated":
                    result["message"] = "Đã cập nhật truyện, không có chương mới."
                # Always upsert — ensures metadata (cover/author/genre/status) saved even when no new chapters
                db_results.append(upsert_story_from_dir(
                    slug,
                    free_chapter_threshold=request.free_chapter_threshold,
                    source_url=story_url,
                    **_meta_kwargs(result),
                ))
        elif result.get("mode") == "listing_page":
            for story in result.get("stories", []):
                slug = story.get("story_slug")
                story_url = story.get("story_url", "")
                if slug:
                    try:
                        if story.get("status") == "already_updated":
                            story["message"] = "Đã cập nhật truyện, không có chương mới."
                        db_results.append(upsert_story_from_dir(
                            slug,
                            free_chapter_threshold=request.free_chapter_threshold,
                            source_url=story_url,
                            **_meta_kwargs(story),
                        ))
                    except Exception:  # noqa: BLE001
                        pass

        result["db_upsert"] = db_results
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        try:
            save_failed_crawl(
                story_url=request.story_url,
                error_message=str(exc),
                story_limit=request.story_limit,
                start_story_from=request.start_story_from,
                free_chapter_threshold=request.free_chapter_threshold,
            )
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(status_code=500, detail=f"Loi crawl: {exc}") from exc


class CategoryCrawlRequest(BaseModel):
    listing_url: str = Field(..., description="URL danh mục, vd: https://truyencom.com/truyen-ngon-tinh/full/")
    target_count: int = Field(default=5, ge=1, le=100, description="Số truyện MỚI cần crawl (chưa có trong DB)")
    free_chapter_threshold: int = Field(default=20, ge=0)
    concurrency: int = Field(default=2, ge=1, le=4, description="Số truyện crawl song song (tối đa 4 tránh bot)")


async def _run_category_crawl(job_id: str, req: CategoryCrawlRequest) -> None:
    """Background runner: collect listing → filter existing → parallel crawl với semaphore + jitter."""
    job = _crawl_jobs[job_id]
    try:
        # 1. Thu thập tất cả URL truyện từ trang danh mục (sync → threadpool)
        job["phase"] = "collecting_urls"
        all_story_urls: list[str] = await run_in_threadpool(
            scraper._collect_story_urls_from_listing, req.listing_url
        )

        # 2. Lọc slug đã có trong DB
        slugs = [scraper._story_slug_from_url(u) for u in all_story_urls]
        existing: set[str] = await run_in_threadpool(get_existing_slugs, slugs)
        new_urls = [u for u, s in zip(all_story_urls, slugs) if s not in existing]

        job["total_in_listing"] = len(all_story_urls)
        job["already_in_db"] = len(all_story_urls) - len(new_urls)
        job["new_available"] = len(new_urls)
        job["phase"] = "crawling"

        if not new_urls:
            job["status"] = "done"
            job["message"] = "Tất cả truyện trong danh mục đã có trong DB."
            return

        # 3. Semaphore-limited parallel crawl cho đến khi đủ target_count
        sem = asyncio.Semaphore(req.concurrency)
        success_lock = asyncio.Lock()
        stop_event = asyncio.Event()
        success_count = 0

        async def crawl_one(url: str, position: int) -> None:
            nonlocal success_count
            if stop_event.is_set():
                return
            async with sem:
                if stop_event.is_set():
                    return
                # Jitter: mỗi slot trì hoãn khác nhau để tránh bot detection
                jitter = random.uniform(1.0, 3.0) + (position % req.concurrency) * 0.8
                await asyncio.sleep(jitter)
                try:
                    result = await scraper.scrape_story(story_url=url)
                    slug = result.get("story_slug")
                    story_url_r = result.get("story_url", url)
                    if slug:
                        db_result = await run_in_threadpool(
                            upsert_story_from_dir,
                            slug,
                            free_chapter_threshold=req.free_chapter_threshold,
                            source_url=story_url_r,
                            **_meta_kwargs(result),
                        )
                        async with success_lock:
                            success_count += 1
                            job["done"] = success_count
                            job["results"].append({
                                "slug": slug,
                                "story_name": result.get("story_name", ""),
                                "story_cover": result.get("story_cover", ""),
                                "new_chapters": result.get("new_chapter_count", 0),
                                "chapter_count": result.get("chapter_count", 0),
                                "crawl_status": result.get("status", ""),
                                "book_id": db_result.get("book_id"),
                            })
                            if success_count >= req.target_count:
                                stop_event.set()
                except Exception as exc:  # noqa: BLE001
                    job["errors"].append({"url": url, "error": str(exc)})
                    logger.warning("[category] Failed url=%s err=%s", url, exc)

        # Chỉ gửi tối đa target_count * 3 candidates (buffer cho failures)
        candidates = new_urls[: req.target_count * 3]
        await asyncio.gather(*[crawl_one(u, i) for i, u in enumerate(candidates)], return_exceptions=True)

        job["status"] = "done"
        job["phase"] = "done"
        job["message"] = f"Hoàn thành {success_count}/{req.target_count} truyện mới."

    except Exception as exc:  # noqa: BLE001
        job["status"] = "error"
        job["phase"] = "error"
        job["error"] = str(exc)
        logger.error("[category] Job %s failed: %s", job_id, exc)


@app.post("/crawl/category", status_code=202)
async def crawl_category(req: CategoryCrawlRequest, background_tasks: BackgroundTasks) -> dict:
    """
    Crawl truyện theo danh mục. Bỏ qua truyện đã có trong DB.
    Chạy background — poll `GET /crawl/category/jobs/{job_id}` để theo dõi tiến độ.
    """
    job_id = uuid.uuid4().hex[:10]
    _crawl_jobs[job_id] = {
        "job_id": job_id,
        "status": "running",
        "phase": "queued",
        "listing_url": req.listing_url,
        "target_count": req.target_count,
        "done": 0,
        "total_in_listing": 0,
        "already_in_db": 0,
        "new_available": 0,
        "results": [],
        "errors": [],
        "message": "",
    }
    background_tasks.add_task(_run_category_crawl, job_id, req)
    return {"job_id": job_id, "status": "running", "poll": f"/crawl/category/jobs/{job_id}"}


@app.get("/crawl/category/jobs/{job_id}")
async def get_category_job(job_id: str) -> dict:
    """Kiểm tra tiến độ crawl danh mục."""
    job = _crawl_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} không tồn tại hoặc đã hết hạn.")
    return job


@app.get("/crawl/category/jobs")
async def list_category_jobs() -> list:
    """Danh sách tất cả jobs (in-memory, mất khi restart server)."""
    return sorted(_crawl_jobs.values(), key=lambda j: j.get("job_id", ""), reverse=True)


@app.get("/crawl/failed")
async def list_failed_crawls(
    resolved: bool = Query(default=False, description="true = xem đã resolve"),
) -> list:
    """Danh sách request crawl bị lỗi (chưa/đã resolve)."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM failed_crawl_requests WHERE resolved = %s ORDER BY created_at DESC",
            (1 if resolved else 0,),
        )
        rows = cur.fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "story_url": r["story_url"],
            "story_limit": r["story_limit"],
            "start_story_from": r["start_story_from"],
            "free_chapter_threshold": r["free_chapter_threshold"],
            "error_message": r["error_message"],
            "retry_count": r["retry_count"],
            "last_tried_at": r["last_tried_at"].isoformat() if r["last_tried_at"] else None,
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "resolved": bool(r["resolved"]),
        }
        for r in rows
    ]


async def _run_tts_background(story_name: str, chapters: list[int], mode: str) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, tts_service.synthesize_story_chapters, story_name, chapters, mode
    )


@app.get("/tts/story/{story_name}/chapters/{chapter_number}/status")
async def chapter_audio_status(story_name: str, chapter_number: int) -> dict:
    audio_path = tts_service.get_chapter_audio_path(story_name, chapter_number)
    story_slug = tts_service._slugify(story_name)
    story_dir = tts_service.story_content_root / story_slug
    return {
        "audio_exists": audio_path.is_file(),
        "content_exists": story_dir.exists(),
        "story_name": story_slug,
    }


@app.post("/tts/story")
async def tts_story(request: StoryTTSRequest, background_tasks: BackgroundTasks) -> dict:
    try:
        story_slug = tts_service._slugify(request.story_name)
        story_dir = tts_service.story_content_root / story_slug
        if not story_dir.exists():
            raise ValueError(f"Khong tim thay thu muc truyen: {story_dir}")
        background_tasks.add_task(_run_tts_background, request.story_name, request.chapters, request.mode)
        return {"status": "generating", "story_name": story_slug, "chapters": request.chapters}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Loi TTS: {exc}") from exc


def _iter_file(path, start: int, end: int, chunk_size: int = 65536):
    with open(path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = f.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@app.get("/tts/story/{story_name}/chapters/{chapter_number}/audio")
async def stream_chapter_audio(story_name: str, chapter_number: int, request: Request):
    audio_path = tts_service.get_chapter_audio_path(story_name, chapter_number)
    if not audio_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Audio chuong {chapter_number} chua duoc tao: {audio_path}",
        )
    file_size = audio_path.stat().st_size
    range_header = request.headers.get("Range")

    if range_header:
        range_val = range_header.strip().lower().replace("bytes=", "")
        start_str, end_str = range_val.split("-")
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1
        end = min(end, file_size - 1)
        return StreamingResponse(
            _iter_file(audio_path, start, end),
            status_code=206,
            media_type="audio/wav",
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(end - start + 1),
                "Accept-Ranges": "bytes",
            },
        )

    return StreamingResponse(
        _iter_file(audio_path, 0, file_size - 1),
        media_type="audio/wav",
        headers={
            "Content-Length": str(file_size),
            "Accept-Ranges": "bytes",
        },
    )


@app.post("/tts/story/clone")
async def tts_story_clone(request: StoryCloneTTSRequest) -> dict:
    try:
        await run_in_threadpool(
            tts_service.synthesize_story_chapters_with_clone_voice,
            request.story_name,
            request.chapters,
            request.reference_audio_path,
            request.mode,
            request.reference_text,
        )
        return {"status": "ok"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Loi TTS clone: {exc}") from exc


# ── JWT / Auth ─────────────────────────────────────────────────────────────────

JWT_SECRET = os.getenv("JWT_SECRET", "nightowl-dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30

_bearer = HTTPBearer(auto_error=False)


def _create_token(email: str, user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRE_DAYS)
    return jwt.encode({"sub": email, "uid": user_id, "exp": expire}, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """FastAPI dependency — validates JWT, returns user dict. Raises 401 if invalid."""
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        email: str = payload.get("sub", "")
        if not email:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token expired or invalid")
    from app.database import get_or_create_user as _get_user
    user = _get_user(email)
    # Backfill uid in token payload for old tokens that don't have it
    if "uid" not in payload:
        user["_token_needs_refresh"] = True
    return user


GOOGLE_CLIENT_ID = "580494851023-u34i18n43a2cho99kp20ncjnl47u2q1d.apps.googleusercontent.com"


class GoogleLoginRequest(BaseModel):
    email: str
    name: str | None = None
    picture: str | None = None
    sub: str | None = None


@app.post("/auth/google")
async def google_login(req: GoogleLoginRequest) -> dict:
    """Upsert user from Google userinfo, return JWT + profile."""
    user = get_or_create_user(req.email, req.name or "", req.picture or "")
    token = _create_token(req.email, user["id"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "email": user["email"],
            "name": user["name"],
            "linh_thach": user["linh_thach"],
            "streak": user["streak"],
            "picture": user.get("picture"),
        },
    }


class FacebookLoginRequest(BaseModel):
    email: str | None = None
    name: str | None = None
    username: str | None = None
    picture: str | None = None
    facebook_id: str | None = None


@app.post("/auth/facebook")
async def facebook_login(req: FacebookLoginRequest) -> dict:
    """Upsert user from Facebook userinfo, return JWT + profile."""
    email = req.email
    if not email:
        if req.username:
            email = f"fb_{req.username}@nightowl.local"
        elif req.facebook_id:
            email = f"fb_{req.facebook_id}@nightowl.local"
        else:
            raise HTTPException(status_code=400, detail="Email, username hoặc facebook_id là bắt buộc")
    user = get_or_create_user(email, req.name or "", req.picture or "")
    token = _create_token(email, user["id"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "email": user["email"],
            "name": user["name"],
            "linh_thach": user["linh_thach"],
            "streak": user["streak"],
            "picture": user.get("picture"),
        },
    }


# ── Books ──────────────────────────────────────────────────────────────────────

def _row_to_book(row) -> dict:
    tags = row["tags"].split(",") if row["tags"] else []
    return {
        "id": row["id"],
        "slug": row["slug"],
        "title": row["title"],
        "author": row["author"],
        "genre": row["genre"],
        "chapters": row["chapter_count"],
        "reads": row["reads"],
        "rating": row["rating"],
        "c1": row["c1"],
        "c2": row["c2"],
        "emoji": row["emoji"],
        "desc": row["description"],
        "lastChapter": row["updated"],
        "tags": tags,
        "words": row["words"],
        "updated": row["updated"],
        "cover_image": row.get("cover_image") or "",
        "status": row.get("status") or "",
        "read_count": row.get("read_count") or 0,
    }


@app.get("/books")
@limiter.limit(RATE_LIMIT_BOOKS)
async def list_books(request: Request, genre: str | None = None) -> list:
    conn = get_conn()
    with conn.cursor() as cur:
        if genre:
            cur.execute("SELECT * FROM books WHERE genre = %s", (genre,))
        else:
            cur.execute("SELECT * FROM books")
        rows = cur.fetchall()
    conn.close()
    return [_row_to_book(r) for r in rows]


def _build_ft_query(q: str) -> str:
    """Build MySQL boolean-mode FTS query: prefix-match each token."""
    import re as _re
    tokens = q.strip().split()
    parts = []
    for tok in tokens:
        cleaned = _re.sub(r'[+\-><\(\)~*"@]', '', tok).strip()
        if len(cleaned) >= 2:
            parts.append(f'{cleaned}*')
    return ' '.join(parts) if parts else q


@app.get("/books/search")
@limiter.limit(RATE_LIMIT_CHAPTERS)
async def search_books(
    request: Request,
    q: str = Query(..., min_length=1),
    genre: str | None = None,
    limit: int = Query(20, le=50),
    offset: int = 0,
) -> dict:
    q = q.strip()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Exact ID match shortcut (only pure digit IDs, not "10 văn tiền")
            if q.isdigit() and len(q) <= 6:
                cur.execute("SELECT * FROM books WHERE id = %s", (q,))
                row = cur.fetchone()
                if row:
                    return {"data": [_row_to_book(row)], "total": 1, "limit": limit, "offset": offset}
                # no ID match → fall through to text search

            genre_filter = " AND genre = %s" if genre and genre != "Tất cả" else ""
            genre_params: list = [genre] if genre and genre != "Tất cả" else []

            def _like_search(cur, q: str, genre_filter: str, genre_params: list, limit: int, offset: int):
                like_q = f"%{q}%"
                starts_q = f"{q}%"
                like_where = "WHERE (title LIKE %s OR author LIKE %s)" + genre_filter
                like_params = [like_q, like_q] + genre_params
                cur.execute(f"SELECT COUNT(*) AS cnt FROM books {like_where}", like_params)
                total = cur.fetchone()["cnt"]
                # Sort: title starts-with query first, then contains, then author match
                cur.execute(
                    f"SELECT * FROM books {like_where} "
                    f"ORDER BY CASE WHEN title LIKE %s THEN 0 ELSE 1 END, title "
                    f"LIMIT %s OFFSET %s",
                    like_params + [starts_q, limit, offset],
                )
                return total

            # Short queries (≤2 chars): MySQL FTS min_token_size=3 ignores them → use LIKE directly
            if len(q) <= 2:
                total = _like_search(cur, q, genre_filter, genre_params, limit, offset)
                rows = cur.fetchall()
            else:
                # Full-text search with title-boosted relevance ranking
                ft_q = _build_ft_query(q)
                ft_where = (
                    "WHERE MATCH(title, author, description, tags) "
                    "AGAINST (%s IN BOOLEAN MODE)" + genre_filter
                )
                ft_params = [ft_q] + genre_params

                cur.execute(f"SELECT COUNT(*) AS cnt FROM books {ft_where}", ft_params)
                total = cur.fetchone()["cnt"]

                if total == 0:
                    total = _like_search(cur, q, genre_filter, genre_params, limit, offset)
                else:
                    cur.execute(
                        f"SELECT *, "
                        f"(MATCH(title) AGAINST (%s IN BOOLEAN MODE) * 3 + "
                        f"MATCH(title, author, description, tags) AGAINST (%s IN BOOLEAN MODE)) AS _score "
                        f"FROM books {ft_where} ORDER BY _score DESC LIMIT %s OFFSET %s",
                        [ft_q, ft_q] + ft_params + [limit, offset],
                    )

                rows = cur.fetchall()
    finally:
        conn.close()

    return {
        "data": [_row_to_book(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/books/paged")
@limiter.limit(RATE_LIMIT_BOOKS)
async def list_books_paged(
    request: Request,
    page: int = Query(1, ge=1, description="Trang hiện tại (bắt đầu từ 1)"),
    page_size: int = Query(24, ge=1, le=100, description="Số truyện mỗi trang (tối đa 100)"),
    genre: str | None = Query(None, description="Lọc theo thể loại"),
    sort_by: str = Query("read_count", description="Sắp xếp theo: read_count | rating | id | title | chapter_count"),
    sort_order: str = Query("desc", description="Thứ tự: asc | desc"),
) -> dict:
    result = await run_in_threadpool(
        get_books_paged,
        page=page,
        page_size=page_size,
        genre=genre if genre and genre != "Tất cả" else None,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    result["data"] = [_row_to_book(r) for r in result["data"]]
    return result


@app.get("/books/{book_id}")
async def get_book(book_id: int) -> dict:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM books WHERE id = %s", (book_id,))
        row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")
    return _row_to_book(row)


class UpdateBookRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500, description="Tên truyện mới")
    author: str | None = Field(default=None, min_length=1, max_length=255, description="Tên tác giả mới")
    free_chapter_threshold: int | None = Field(default=None, ge=0, description="Chương từ 1 đến giá trị này miễn phí. 0 = tất cả tính phí.")


@app.patch("/books/{book_id}")
async def patch_book(book_id: int, req: UpdateBookRequest) -> dict:
    """Cập nhật tên truyện, tác giả và/hoặc ngưỡng chương miễn phí."""
    if req.title is None and req.author is None and req.free_chapter_threshold is None:
        raise HTTPException(status_code=400, detail="Cần ít nhất một trường để cập nhật: title, author, free_chapter_threshold")
    try:
        result = await run_in_threadpool(
            update_book, book_id, req.title, req.author, req.free_chapter_threshold
        )
        return {
            "id": result["book"]["id"],
            "title": result["book"]["title"],
            "author": result["book"]["author"],
            "free_chapters": result["free_chapters"],
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/books/{book_id}/chapters")
@limiter.limit(RATE_LIMIT_CHAPTERS)
async def list_chapters(
    request: Request,
    book_id: int,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM books WHERE id = %s", (book_id,))
        if not cur.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Book not found")
        cur.execute(
            "SELECT id, chapter_number, title, free, view_count FROM chapters WHERE book_id = %s ORDER BY chapter_number",
            (book_id,),
        )
        rows = cur.fetchall()
    conn.close()

    # Lấy danh sách chương đã mở khóa của user (nếu đã đăng nhập)
    uid: int = 0
    unlocked: set[int] = set()
    if creds:
        try:
            payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            uid = payload.get("uid", 0)
            if uid:
                unlocked = get_unlocked_chapter_numbers(uid, book_id)
        except JWTError:
            pass

    # Generate short-lived session token — required to read content (if SESSION_TOKEN_ENABLED)
    session_token = _make_session_token(uid, book_id) if SESSION_TOKEN_ENABLED else ""

    return {
        "session_token": session_token,
        "chapters": [
            {
                "id": r["id"],
                "chapterNumber": r["chapter_number"],
                "title": r["title"],
                "free": bool(r["free"]),
                "unlocked": bool(r["free"]) or r["chapter_number"] in unlocked,
                "viewCount": r["view_count"],
            }
            for r in rows
        ],
    }


@app.post("/books/{book_id}/chapters/{chapter_number}/unlock")
async def unlock_chapter_endpoint(
    book_id: int,
    chapter_number: int,
    current_user: dict = Depends(get_current_user),
) -> dict:
    try:
        result = unlock_chapter(current_user["id"], book_id, chapter_number)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/books/{book_id}/chapters/{chapter_number}/content")
@limiter.limit(RATE_LIMIT_CONTENT)
async def get_chapter_content(
    request: Request,
    background_tasks: BackgroundTasks,
    book_id: int,
    chapter_number: int,
    session_token: str | None = Query(default=None, description="Token từ /books/{id}/chapters"),
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    # Xác thực uid từ JWT (hoặc 0 nếu guest)
    uid: int = 0
    if creds:
        try:
            payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            uid = payload.get("uid", 0)
        except JWTError:
            raise HTTPException(status_code=401, detail="Token không hợp lệ")

    # Verify session token (anti-scraping: phải gọi /chapters trước)
    if SESSION_TOKEN_ENABLED:
        if not session_token:
            raise HTTPException(status_code=422, detail="session_token là bắt buộc")
        if not _verify_session_token(session_token, uid, book_id):
            raise HTTPException(status_code=403, detail="Session token không hợp lệ hoặc đã hết hạn")

    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT title, file_path, free FROM chapters WHERE book_id = %s AND chapter_number = %s",
            (book_id, chapter_number),
        )
        row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Chapter not found")

    # Kiểm tra quyền truy cập nội dung chương bị khóa
    if not row["free"]:
        if not creds:
            raise HTTPException(status_code=401, detail="Cần đăng nhập để đọc chương này")
        unlocked = get_unlocked_chapter_numbers(uid, book_id)
        if chapter_number not in unlocked:
            raise HTTPException(status_code=403, detail="locked")

    file_path = row["file_path"]
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail=f"Content file not found: {file_path}")
    with open(file_path, encoding="utf-8") as f:
        content = f.read()

    background_tasks.add_task(
        run_in_threadpool, lambda: increment_chapter_view(book_id, chapter_number)
    )

    return {
        "chapterNumber": chapter_number,
        "title": row["title"],
        "free": bool(row["free"]),
        "content": content,
    }


# ── Genres ─────────────────────────────────────────────────────────────────────

@app.get("/genres")
async def list_genres() -> list:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT genre FROM books ORDER BY genre")
        rows = cur.fetchall()
    conn.close()
    return [r["genre"] for r in rows]


# ── Notifications ──────────────────────────────────────────────────────────────

@app.get("/notifications")
async def list_notifications() -> list:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM notifications ORDER BY id DESC")
        rows = cur.fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "type": r["type"],
            "icon": r["icon"],
            "title": r["title"],
            "body": r["body"],
            "time": r["time"],
            "unread": bool(r["unread"]),
        }
        for r in rows
    ]


@app.patch("/notifications/{notif_id}/read")
async def mark_notification_read(notif_id: int) -> dict:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("UPDATE notifications SET unread = 0 WHERE id = %s", (notif_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.patch("/notifications/read-all")
async def mark_all_read() -> dict:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("UPDATE notifications SET unread = 0")
    conn.commit()
    conn.close()
    return {"status": "ok"}


# ── User / Linh Thạch ──────────────────────────────────────────────────────────

class UpdateProfileRequest(BaseModel):
    email: str
    name: str
    bio: str = ""


class PurchaseRequest(BaseModel):
    email: str
    package_id: str
    gems: int
    bonus: int = 0
    price: int
    label: str


class DailyRewardRequest(BaseModel):
    email: str


class ReadingProgressRequest(BaseModel):
    email: str
    book_id: int
    chapter_number: int


@app.get("/user/profile/{email:path}")
async def get_user_profile(
    email: str,
    _current: dict = Depends(get_current_user),
) -> dict:
    return get_or_create_user(email)


@app.put("/user/profile")
async def put_user_profile(
    req: UpdateProfileRequest,
    _current: dict = Depends(get_current_user),
) -> dict:
    try:
        return update_user_profile(req.email, req.name, req.bio)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/user/linh-thach/purchase")
async def purchase_linh_thach(
    req: PurchaseRequest,
    _current: dict = Depends(get_current_user),
) -> dict:
    total = req.gems + req.bonus
    desc = f"Mua {req.label} (+{total} Linh Thạch)"
    try:
        balance = add_linh_thach(_current["id"], total, desc, "purchase")
        return {"status": "ok", "balance": balance, "added": total}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/user/linh-thach/history/{email:path}")
async def linh_thach_history(
    email: str,
    limit: int = 20,
    _current: dict = Depends(get_current_user),
) -> list:
    return get_linh_thach_history(_current["id"], limit)


@app.post("/user/linh-thach/daily")
async def daily_reward(
    req: DailyRewardRequest,
    _current: dict = Depends(get_current_user),
) -> dict:
    try:
        return claim_daily_reward(_current["id"])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/user/reading-progress")
async def update_reading_progress(
    req: ReadingProgressRequest,
    _current: dict = Depends(get_current_user),
) -> dict:
    try:
        upsert_reading_progress(_current["id"], req.book_id, req.chapter_number)
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/user/reading-history/{email:path}")
async def reading_history(
    email: str,
    _current: dict = Depends(get_current_user),
) -> list:
    rows = get_reading_history(_current["id"])
    return [
        {
            "bookId": r["book_id"],
            "chapterNumber": r["chapter_number"],
            "lastRead": r["last_read"].isoformat() if r["last_read"] else None,
            "book": {
                "id": r["book_id"],
                "slug": r["slug"],
                "title": r["title"],
                "author": r["author"],
                "genre": r["genre"],
                "chapters": r["chapter_count"],
                "rating": r["rating"],
                "c1": r["c1"],
                "c2": r["c2"],
                "emoji": r["emoji"],
                "desc": r["description"],
                "tags": r["tags"].split(",") if r["tags"] else [],
                "words": r["words"],
                "reads": r["reads"],
                "updated": r["updated"],
            },
        }
        for r in rows
    ]
