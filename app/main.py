from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.scraper import DEFAULT_STORY_URL, StoryScraper
from app.tts_service import StoryTTSService

app = FastAPI(title="Story Scraper Service", version="1.0.0")
scraper = StoryScraper(output_root="story")
tts_service = StoryTTSService(story_content_root="story-content", output_root="outputs/audio")


class CrawlRequest(BaseModel):
    story_url: str = DEFAULT_STORY_URL
    story_limit: int | None = Field(default=None, ge=1)
    start_story_from: int = Field(default=1, ge=1)


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


@app.post("/tts/story")
async def tts_story(request: StoryTTSRequest) -> dict:
    try:
        return await run_in_threadpool(
            tts_service.synthesize_story_chapters,
            request.story_name,
            request.chapters,
            request.mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Loi TTS: {exc}") from exc


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
