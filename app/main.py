from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.scraper import DEFAULT_STORY_URL, StoryScraper

app = FastAPI(title="Story Scraper Service", version="1.0.0")
scraper = StoryScraper(output_root="story")


class CrawlRequest(BaseModel):
    story_url: str = DEFAULT_STORY_URL
    story_limit: int | None = Field(default=None, ge=1)
    start_story_from: int = Field(default=1, ge=1)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/crawl")
async def crawl_story(request: CrawlRequest) -> dict:
    try:
        return await scraper.scrape_story(
            story_url=request.story_url,
            story_limit=request.story_limit,
            start_story_from=request.start_story_from,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Loi crawl: {exc}") from exc
