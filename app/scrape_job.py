"""Scheduled story scrape job.

Đọc danh sách URL từ scrape_sources.json, lọc truyện chưa có trong DB,
crawl các truyện mới và upsert vào DB.

Config file mặc định: scrape_sources.json (cùng thư mục với main.py).
Override bằng env: SCRAPE_SOURCES_PATH=/path/to/file.json
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from datetime import datetime
from pathlib import Path

from starlette.concurrency import run_in_threadpool

logger = logging.getLogger("nightowl.scrape_job")

# Vị trí file config mặc định (project root)
_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "scrape_sources.json"
SCRAPE_SOURCES_PATH = Path(os.getenv("SCRAPE_SOURCES_PATH", str(_DEFAULT_CONFIG_PATH)))


def load_config() -> dict:
    """Đọc và validate scrape_sources.json. Reload mỗi lần job chạy để pick up thay đổi."""
    if not SCRAPE_SOURCES_PATH.exists():
        logger.warning("[scrape_job] Config file không tồn tại: %s", SCRAPE_SOURCES_PATH)
        return {"sources": []}
    try:
        with open(SCRAPE_SOURCES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error("[scrape_job] Không đọc được config: %s", exc)
        return {"sources": []}


def get_schedule_mode(config: dict) -> str:
    """Trả về schedule type: 'continuous' | 'interval' | 'cron'."""
    return config.get("schedule", {}).get("type", "interval")


def get_schedule_kwargs(config: dict) -> dict:
    """Chuyển config['schedule'] → kwargs cho APScheduler add_job().

    Các key được xử lý đặc biệt (không truyền thẳng vào APScheduler):
      - type         : xác định trigger (interval | cron | continuous)
      - active_window: kiểm tra runtime, không phải APScheduler param
      - idle_seconds : chỉ dùng cho continuous mode
    Các key còn lại (hours, minutes, start_date, end_date, hour, minute, …)
    được forward thẳng vào APScheduler sau khi lọc chuỗi rỗng.
    """
    sched = config.get("schedule", {})
    trigger_type = sched.get("type", "interval")

    _skip = {"type", "active_window", "idle_seconds"}
    raw_fields = {
        k: v
        for k, v in sched.items()
        if k not in _skip and not k.startswith("_") and v != "" and v is not None
    }

    if trigger_type == "cron":
        if not raw_fields:
            raw_fields = {"hour": 2, "minute": 0}
        return {"trigger": "cron", **raw_fields}

    # interval (default)
    interval_fields = {k: v for k, v in raw_fields.items()}
    if not any(k in interval_fields for k in ("seconds", "minutes", "hours", "days", "weeks")):
        interval_fields["hours"] = 2
    return {"trigger": "interval", **interval_fields}


async def run_continuous_scrape(stop_event: asyncio.Event, idle_seconds: float = 30.0) -> None:
    """Loop run_scheduled_scrape liên tục đến khi stop_event được set.

    Giữa mỗi vòng nghỉ idle_seconds (mặc định 30s) tránh hammer site.
    """
    logger.info("[scrape_job] Continuous mode bật — idle=%ss giữa mỗi vòng.", idle_seconds)
    iteration = 0
    while not stop_event.is_set():
        iteration += 1
        loop_start = datetime.now()
        logger.info("[scrape_job] === Vòng #%d bắt đầu lúc %s ===", iteration, loop_start.strftime("%H:%M:%S"))
        try:
            await run_scheduled_scrape()
        except Exception as exc:  # noqa: BLE001
            logger.error("[scrape_job] Vòng #%d lỗi: %s", iteration, exc)
        elapsed = (datetime.now() - loop_start).total_seconds()
        logger.info("[scrape_job] === Vòng #%d xong — %.1fs ===", iteration, elapsed)
        if stop_event.is_set():
            break
        logger.info("[scrape_job] Nghỉ %ss trước vòng kế.", idle_seconds)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=idle_seconds)
        except asyncio.TimeoutError:
            pass
    logger.info("[scrape_job] Continuous mode dừng sau %d vòng.", iteration)


def _within_active_window(start_str: str, end_str: str) -> bool:
    """Trả về True nếu giờ hiện tại nằm trong cửa sổ [start, end).

    Hỗ trợ cửa sổ qua đêm: start='22:00', end='06:00' → True từ 22h đến 6h sáng hôm sau.
    Chuỗi rỗng → không giới hạn → luôn True.
    """
    if not start_str or not end_str:
        return True
    try:
        fmt = "%H:%M"
        t_start = datetime.strptime(start_str, fmt).time()
        t_end   = datetime.strptime(end_str,   fmt).time()
        now     = datetime.now().time()

        if t_start <= t_end:
            # Cửa sổ trong ngày, vd: 08:00–20:00
            return t_start <= now < t_end
        else:
            # Cửa sổ qua đêm, vd: 22:00–06:00
            return now >= t_start or now < t_end
    except ValueError:
        logger.warning("[scrape_job] active_window format sai (dùng HH:MM): start=%s end=%s", start_str, end_str)
        return True


async def run_scheduled_scrape() -> None:
    """Entry point cho APScheduler. Scrape tất cả enabled sources."""
    # Import ở đây để tránh circular import
    from app.scraper import StoryScraper
    from app.database import get_existing_slugs, upsert_story_from_dir

    config = load_config()

    # Kiểm tra active_window (cửa sổ giờ hàng ngày)
    window = config.get("schedule", {}).get("active_window", {})
    w_start = window.get("start", "") if isinstance(window, dict) else ""
    w_end   = window.get("end",   "") if isinstance(window, dict) else ""
    if not _within_active_window(w_start, w_end):
        logger.info(
            "[scrape_job] Ngoài khung giờ hoạt động (%s–%s), bỏ qua lần này.",
            w_start, w_end,
        )
        return

    sources = [s for s in config.get("sources", []) if s.get("enabled", True)]

    if not sources:
        logger.info("[scrape_job] Không có source nào được bật.")
        return

    logger.info("[scrape_job] Bắt đầu — %d source(s)", len(sources))
    scraper = StoryScraper(output_root="story")
    total = len(sources)
    overall_start = datetime.now()

    for idx, source in enumerate(sources, start=1):
        url: str = source["url"]
        target_count: int = source.get("target_count", 5)
        free_threshold: int = source.get("free_chapter_threshold", 20)
        concurrency: int = min(source.get("concurrency", 2), 4)

        src_start = datetime.now()
        logger.info(
            "[scrape_job] [%d/%d] Source: %s target=%d concurrency=%d free_threshold=%d",
            idx, total, url, target_count, concurrency, free_threshold,
        )
        try:
            await _scrape_source(
                scraper=scraper,
                url=url,
                target_count=target_count,
                free_chapter_threshold=free_threshold,
                concurrency=concurrency,
                get_existing_slugs=get_existing_slugs,
                upsert_story_from_dir=upsert_story_from_dir,
            )
        except Exception as exc:
            logger.error("[scrape_job] [%d/%d] Lỗi source=%s: %s", idx, total, url, exc)
        finally:
            src_elapsed = (datetime.now() - src_start).total_seconds()
            logger.info("[scrape_job] [%d/%d] Source xong — %.1fs", idx, total, src_elapsed)

    overall_elapsed = (datetime.now() - overall_start).total_seconds()
    logger.info("[scrape_job] Hoàn thành tất cả sources — tổng %.1fs.", overall_elapsed)


async def _scrape_source(
    scraper,
    url: str,
    target_count: int,
    free_chapter_threshold: int,
    concurrency: int,
    get_existing_slugs,
    upsert_story_from_dir,
) -> None:
    # 1. Thu thập tất cả URL truyện trong trang danh mục
    # collect_story_urls routes to Go fetcher or Python depending on USE_GO_FETCHER
    all_story_urls: list[str] = await scraper.collect_story_urls(url)
    if not all_story_urls:
        logger.info("[scrape_job] Không tìm thấy truyện tại: %s", url)
        return

    # 2. Lọc slug đã có trong DB
    slugs = [scraper._story_slug_from_url(u) for u in all_story_urls]
    existing: set[str] = await run_in_threadpool(get_existing_slugs, slugs)
    new_urls = [u for u, s in zip(all_story_urls, slugs) if s not in existing]

    logger.info(
        "[scrape_job] %s — tổng=%d đã_có=%d mới=%d",
        url, len(all_story_urls), len(existing), len(new_urls),
    )

    if not new_urls:
        logger.info("[scrape_job] Tất cả truyện đã có trong DB.")
        return

    # 3. Crawl song song có semaphore + jitter, dừng khi đủ target_count (hoặc unlimited nếu <= 0)
    unlimited = target_count <= 0
    sem = asyncio.Semaphore(concurrency)
    success_lock = asyncio.Lock()
    stop_event = asyncio.Event()
    success_count = 0

    async def crawl_one(story_url: str, position: int) -> None:
        nonlocal success_count
        if stop_event.is_set():
            return
        async with sem:
            if stop_event.is_set():
                return
            jitter = random.uniform(1.5, 4.0) + (position % concurrency) * 1.0
            logger.info(
                "[scrape_job] → start pos=%d/%d sleep=%.1fs url=%s",
                position + 1, len(candidates), jitter, story_url,
            )
            await asyncio.sleep(jitter)
            t0 = datetime.now()
            try:
                result = await scraper.scrape_story(story_url=story_url)
                slug = result.get("story_slug")
                status = result.get("status", "")
                story_url_r = result.get("story_url", story_url)
                elapsed = (datetime.now() - t0).total_seconds()

                if status == "already_updated":
                    logger.info(
                        "[scrape_job] ⊝ skip slug=%s (already_updated) %.1fs",
                        slug, elapsed,
                    )
                    return

                if slug:
                    meta_kwargs = {
                        "story_name": result.get("story_name", ""),
                        "story_author": result.get("story_author", ""),
                        "story_genre": result.get("story_genre", ""),
                        "story_status": result.get("story_status", ""),
                        "story_description": result.get("story_description", ""),
                        "story_cover": result.get("story_cover", ""),
                    }
                    await run_in_threadpool(
                        upsert_story_from_dir,
                        slug,
                        free_chapter_threshold=free_chapter_threshold,
                        source_url=story_url_r,
                        **meta_kwargs,
                    )
                    async with success_lock:
                        success_count += 1
                        new_chapters = result.get("new_chapter_count", 0)
                        target_disp = "∞" if unlimited else str(target_count)
                        logger.info(
                            "[scrape_job] ✓ slug=%s new_chapters=%d %.1fs (%d/%s)",
                            slug, new_chapters, elapsed, success_count, target_disp,
                        )
                        if not unlimited and success_count >= target_count:
                            logger.info("[scrape_job] Đạt target=%d, dừng source này.", target_count)
                            stop_event.set()
                else:
                    logger.warning("[scrape_job] ✗ no slug returned url=%s status=%s", story_url, status)
            except Exception as exc:
                elapsed = (datetime.now() - t0).total_seconds()
                logger.warning("[scrape_job] ✗ Lỗi url=%s %.1fs: %s", story_url, elapsed, exc)

    # Buffer: limited mode gửi target_count * 3, unlimited mode gửi tất cả
    candidates = new_urls if unlimited else new_urls[: target_count * 3]
    await asyncio.gather(*[crawl_one(u, i) for i, u in enumerate(candidates)], return_exceptions=True)

    target_disp = "∞" if unlimited else str(target_count)
    logger.info("[scrape_job] Source=%s xong — %d/%s truyện mới.", url, success_count, target_disp)
