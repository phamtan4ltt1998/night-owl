from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

DEFAULT_STORY_URL = "https://truyencom.com/truyen-xuyen-nhanh/full/"


@dataclass
class ChapterLink:
    title: str
    url: str
    slug: str
    chapter_number: int | None


class StoryScraper:
    def __init__(self, output_root: str = "story") -> None:
        self.output_root = Path(output_root)
        self.content_root = Path("story-content")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
                )
            }
        )

    async def scrape_story(
        self,
        story_url: str = DEFAULT_STORY_URL,
        story_limit: int | None = None,
        start_story_from: int = 1,
    ) -> Dict[str, object]:
        if start_story_from < 1:
            raise ValueError("start_story_from phai >= 1.")
        if story_limit is not None and story_limit < 1:
            raise ValueError("story_limit phai >= 1 neu duoc truyen vao.")

        source_url = self._normalize_url(story_url)

        # Case 1: URL la trang truyện chi tiết -> crawl chapter của truyện đó.
        direct_story_chapters = self._collect_chapters_for_story(source_url)
        if direct_story_chapters:
            story_result = await self._save_story(source_url, direct_story_chapters)
            return {"source_url": source_url, "mode": "single_story", **story_result}

        # Case 2: URL la trang danh sách (vd: /truyen-xuyen-nhanh/full/)
        # -> tìm toàn bộ link truyện rồi crawl chapter từng truyện.
        story_urls = self._collect_story_urls_from_listing(source_url)
        if not story_urls:
            raise ValueError("Khong tim thay truyen hoac chapter tu URL da cho.")

        start_index = start_story_from - 1
        if start_index >= len(story_urls):
            raise ValueError(
                f"start_story_from={start_story_from} vuot qua tong so truyen tim thay ({len(story_urls)})."
            )
        selected_story_urls = story_urls[start_index:]
        if story_limit is not None:
            selected_story_urls = selected_story_urls[:story_limit]

        stories: List[Dict[str, object]] = []
        for story_page_url in selected_story_urls:
            chapters = self._collect_chapters_for_story(story_page_url)
            if not chapters:
                continue
            stories.append(await self._save_story(story_page_url, chapters))

        if not stories:
            raise ValueError("Tim thay danh sach truyen, nhung khong lay duoc chapter nao.")

        return {
            "source_url": source_url,
            "mode": "listing_page",
            "start_story_from": start_story_from,
            "story_limit": story_limit,
            "selected_story_count": len(selected_story_urls),
            "story_count": len(stories),
            "stories": stories,
        }

    async def _save_story(
        self, story_url: str, chapters: List[ChapterLink]
    ) -> Dict[str, object]:
        story_slug = self._story_slug_from_url(story_url)
        output_dir = self.output_root / f"{story_slug}-clone"
        content_dir = self.content_root / story_slug
        output_dir.mkdir(parents=True, exist_ok=True)
        content_dir.mkdir(parents=True, exist_ok=True)

        chapters = self._sort_chapters(chapters)
        saved_files = await self._crawl_and_save_chapters(chapters, output_dir)
        content_files = self._extract_story_content_files(output_dir, content_dir)
        metadata = {
            "story_url": story_url,
            "story_slug": story_slug,
            "chapter_count": len(chapters),
            "saved_files": saved_files,
            "content_files": content_files,
        }
        metadata_path = output_dir / "metadata.json"
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "story_url": story_url,
            "output_dir": str(output_dir),
            "story_slug": story_slug,
            "chapter_count": len(chapters),
            "metadata_file": str(metadata_path),
            "content_output_dir": str(content_dir),
            "content_file_count": len(content_files),
        }

    async def _crawl_and_save_chapters(
        self, chapters: List[ChapterLink], output_dir: Path
    ) -> List[str]:
        browser_config = BrowserConfig(headless=True, verbose=False)
        run_config = CrawlerRunConfig()
        saved_files: List[str] = []

        async with AsyncWebCrawler(config=browser_config) as crawler:
            for index, chapter in enumerate(chapters, start=1):
                result = await crawler.arun(url=chapter.url, config=run_config)
                markdown = self._extract_markdown(result)
                if not markdown:
                    markdown = f"# {chapter.title}\n\nKhong trich xuat duoc noi dung."
                markdown = self._replace_branding(markdown)

                file_name = f"{index:04d}-{chapter.slug}.md"
                chapter_path = output_dir / file_name
                chapter_path.write_text(markdown, encoding="utf-8")
                saved_files.append(str(chapter_path))

                # Nhe tay de han che bi chan bot
                await asyncio.sleep(0.3)

        return saved_files

    def _extract_story_content_files(self, source_dir: Path, content_dir: Path) -> List[str]:
        saved_content_files: List[str] = []
        for chapter_path in sorted(source_dir.glob("*.md")):
            chapter_markdown = chapter_path.read_text(encoding="utf-8")
            chapter_content = self._extract_chapter_content(chapter_markdown)
            if not chapter_content:
                continue

            content_path = content_dir / chapter_path.name
            content_path.write_text(self._replace_branding(chapter_content), encoding="utf-8")
            saved_content_files.append(str(content_path))
        return saved_content_files

    def _extract_chapter_content(self, markdown: str) -> str:
        lines = markdown.splitlines()
        separator_indices = [index for index, line in enumerate(lines) if line.strip() == "* * *"]
        if len(separator_indices) < 3:
            return markdown.strip()

        start_index = separator_indices[1] + 1
        end_index = separator_indices[-1]
        content_lines = lines[start_index:end_index]

        while content_lines and not content_lines[0].strip():
            content_lines.pop(0)
        while content_lines and not content_lines[-1].strip():
            content_lines.pop()

        return "\n".join(content_lines).strip()

    def _replace_branding(self, content: str) -> str:
        return content.replace("Truyencom.com", "nightowl.com")

    def _extract_markdown(self, crawl_result: object) -> str:
        markdown = getattr(crawl_result, "markdown", "")
        if isinstance(markdown, str):
            return markdown.strip()

        raw_markdown = getattr(markdown, "raw_markdown", "")
        if isinstance(raw_markdown, str):
            return raw_markdown.strip()
        return ""

    def _collect_chapters_for_story(self, story_url: str) -> List[ChapterLink]:
        visited_pages: Set[str] = set()
        pages_to_visit: List[str] = [story_url]
        collected: Dict[str, ChapterLink] = {}
        story_key = self._story_key_from_url(story_url)
        story_key_re = re.compile(rf"/{re.escape(story_key)}/chuong-(\d+)\.html$", re.IGNORECASE)

        while pages_to_visit:
            page_url = pages_to_visit.pop(0)
            if page_url in visited_pages:
                continue
            visited_pages.add(page_url)

            html = self._fetch_html(page_url)
            if not html:
                continue

            chapters, pagination_pages = self._extract_story_links_and_pages(
                html=html, base_url=story_url, story_key_re=story_key_re
            )
            for chapter_link in chapters:
                collected[chapter_link.url] = chapter_link
            for pagination_url in pagination_pages:
                if pagination_url not in visited_pages:
                    pages_to_visit.append(pagination_url)

        return list(collected.values())

    def _extract_story_links_and_pages(
        self, html: str, base_url: str, story_key_re: re.Pattern[str]
    ) -> Tuple[List[ChapterLink], List[str]]:
        soup = BeautifulSoup(html, "html.parser")
        anchors = soup.select("a[href]")
        chapters: List[ChapterLink] = []
        pages: List[str] = []

        for anchor in anchors:
            href = anchor.get("href")
            if not href:
                continue
            full_url = self._normalize_url(urljoin(base_url, href))
            parsed = urlparse(full_url)
            if not parsed.netloc.endswith("truyencom.com"):
                continue
            chapter_match = story_key_re.search(parsed.path)
            if chapter_match:
                chapter_number = int(chapter_match.group(1))
                title = " ".join(anchor.get_text(" ", strip=True).split()) or f"Chuong {chapter_number}"
                slug = self._slugify(parsed.path.strip("/").split("/")[-1].replace(".html", ""))
                chapters.append(
                    ChapterLink(
                        title=title,
                        url=full_url,
                        slug=slug,
                        chapter_number=chapter_number,
                    )
                )
                continue
            if re.search(r"/trang-\d+/?$", parsed.path):
                pages.append(full_url)
        return chapters, pages

    def _collect_story_urls_from_listing(self, listing_url: str) -> List[str]:
        visited_pages: Set[str] = set()
        pages_to_visit: List[str] = [listing_url]
        story_urls: Set[str] = set()

        while pages_to_visit:
            page_url = pages_to_visit.pop(0)
            if page_url in visited_pages:
                continue
            visited_pages.add(page_url)

            html = self._fetch_html(page_url)
            if not html:
                continue
            page_stories, pagination_pages = self._extract_story_listing_links(html, listing_url)
            story_urls.update(page_stories)
            for pagination_url in pagination_pages:
                if pagination_url not in visited_pages:
                    pages_to_visit.append(pagination_url)

        return sorted(story_urls)

    def _extract_story_listing_links(
        self, html: str, listing_root_url: str
    ) -> Tuple[List[str], List[str]]:
        soup = BeautifulSoup(html, "html.parser")
        stories: Set[str] = set()
        pages: Set[str] = set()

        for anchor in soup.select("a[href]"):
            href = anchor.get("href")
            if not href:
                continue
            full_url = self._normalize_url(urljoin(listing_root_url, href))
            parsed = urlparse(full_url)
            if not parsed.netloc.endswith("truyencom.com"):
                continue

            if re.search(r"/truyen-[a-z0-9-]+/full/trang-\d+/?$", parsed.path):
                pages.add(full_url)
                continue

            # Trang truyện thường có dạng: /ten-truyen.1234/
            if re.fullmatch(r"/[a-z0-9-]+\.\d+/?", parsed.path):
                stories.add(full_url)

        return list(stories), list(pages)

    def _sort_chapters(self, chapters: Iterable[ChapterLink]) -> List[ChapterLink]:
        return sorted(
            chapters,
            key=lambda chapter: (
                chapter.chapter_number is None,
                chapter.chapter_number if chapter.chapter_number is not None else 10**9,
                chapter.slug,
            ),
        )

    def _fetch_html(self, url: str) -> str:
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.text
        except requests.RequestException:
            return ""

    def _extract_chapter_number(self, slug: str, title: str) -> int | None:
        probes = [
            re.search(r"(?:chuong|chapter|ch)[-_ ]*(\d+)", slug, re.IGNORECASE),
            re.search(r"(\d+)", slug),
            re.search(r"(?:chuong|chapter|ch)[-_ ]*(\d+)", title, re.IGNORECASE),
            re.search(r"(\d+)", title),
        ]
        for match in probes:
            if match:
                return int(match.group(1))
        return None

    def _story_slug_from_url(self, story_url: str) -> str:
        parsed = urlparse(story_url)
        parts = [part for part in parsed.path.split("/") if part]
        if not parts:
            return "story"
        if parts[-1] == "full" and len(parts) >= 2:
            return self._slugify(parts[-2])
        if parts[-1].startswith("trang-") and len(parts) >= 2:
            return self._slugify(parts[-2])
        return self._slugify(re.sub(r"\.\d+$", "", parts[-1]))

    def _story_key_from_url(self, story_url: str) -> str:
        parsed = urlparse(story_url)
        parts = [part for part in parsed.path.split("/") if part]
        if not parts:
            return ""
        story_segment = parts[-1]
        if story_segment in {"full"} and len(parts) >= 2:
            story_segment = parts[-2]
        return re.sub(r"\.\d+$", "", story_segment)

    def _normalize_url(self, url: str) -> str:
        normalized = url.strip()
        parsed = urlsplit(normalized)
        clean = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
        if parsed.path and not parsed.path.endswith("/"):
            last_segment = parsed.path.split("/")[-1]
            if "." not in last_segment:
                clean += "/"
        return clean

    def _slugify(self, value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value)
        normalized = normalized.encode("ascii", "ignore").decode("ascii")
        normalized = normalized.lower()
        normalized = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
        return normalized or "item"
