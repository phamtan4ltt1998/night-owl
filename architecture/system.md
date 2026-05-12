# System / Infrastructure API Flow Diagrams

---

## GET /health

Simple liveness probe.

```mermaid
flowchart TD
    Client[Client] -->|GET /health| Handler
    Handler --> Return["200 OK\n{status: ok}"]
```

---

## GET /robots.txt

Tells crawlers to stay out of `/books/` and `/user/`.

```mermaid
flowchart TD
    Client[Crawler / Browser] -->|GET /robots.txt| Handler
    Handler --> Return["200 OK (text/plain)\nUser-agent: *\nDisallow: /books/\nDisallow: /user/\nCrawl-delay: 10"]
```

---

## GET /api/internal/book-list-cache (Honeypot)

Hidden endpoint. Real clients never hit this. Any public IP that calls it gets banned.

```mermaid
flowchart TD
    Client[Client] -->|GET /api/internal/book-list-cache| Handler
    Handler --> HoneypotEnabled{HONEYPOT_ENABLED?}
    HoneypotEnabled -- no --> ReturnEmpty["200 OK — []"]
    HoneypotEnabled -- yes --> GetIP["ip = request.client.host"]
    GetIP --> IsPrivate{_is_private(ip)?}
    IsPrivate -- yes, private subnet --> ReturnEmpty
    IsPrivate -- no, public IP --> BanIP["BANNED_IPS.add(ip)\n(in-memory set, checked by bot_guard_middleware)"]
    BanIP --> ReturnEmpty["200 OK — []\n(attacker not alerted)"]
```

**Note:** Returns 200 (not 403) intentionally — ban is silent so attacker doesn't know they were flagged.

---

## Startup: Scheduler Init

```mermaid
flowchart TD
    Start[FastAPI startup event] --> Job1["APScheduler.add_job:\n_retry_failed_crawls\ninterval=CRAWL_RETRY_INTERVAL_MINUTES"]
    Start --> LoadConfig["load_config() → scrape_sources.json"]
    LoadConfig --> FilterEnabled["Filter sources where enabled=true"]
    FilterEnabled --> NoSources{any enabled\nsources?}
    NoSources -- no --> WarnLog["Log warning: no sources configured"]
    NoSources -- yes --> ModeCheck{schedule.type?}
    ModeCheck -- continuous --> CreateTask["asyncio.create_task(\n  run_continuous_scrape(stop_event, idle_seconds)\n)"]
    ModeCheck -- interval/cron --> AddJob2["APScheduler.add_job:\nrun_scheduled_scrape\ninterval/cron kwargs from config"]
    WarnLog --> StartScheduler
    CreateTask --> StartScheduler
    AddJob2 --> StartScheduler["_scheduler.start()"]
```

## Shutdown: Graceful Stop

```mermaid
flowchart TD
    Stop[FastAPI shutdown event] --> SetStopEvent["stop_event.set()\n(signals continuous scrape to stop)"]
    SetStopEvent --> WaitTask["asyncio.wait_for(scrape_task, timeout=10s)"]
    WaitTask -- timeout/cancelled --> CancelTask["task.cancel()"]
    WaitTask -- ok --> ShutdownScheduler
    CancelTask --> ShutdownScheduler["_scheduler.shutdown(wait=False)"]
```
