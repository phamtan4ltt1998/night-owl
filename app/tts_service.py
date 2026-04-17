from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import Iterable

from vieneu import Vieneu

logger = logging.getLogger(__name__)

# Ten file transcript trong cung thu muc voi file audio mau (input/sample-voice/<ten>/...)
_REFERENCE_TEXT_FILENAMES = ("reference.txt", "reference_text.txt", "reference text")


class StoryTTSService:
    def __init__(self, story_content_root: str = "story-content", output_root: str = "outputs/audio") -> None:
        self.story_content_root = Path(story_content_root)
        self.output_root = Path(output_root)
        self._tts_by_mode: dict[str, Vieneu] = {}

    def synthesize_story_chapters(
        self, story_name: str, chapters: Iterable[int], mode: str = "turbo"
    ) -> dict[str, object]:
        story_slug, chapter_numbers, chapter_files, merged_text = self._load_story_chapter_texts(
            story_name=story_name, chapters=chapters
        )
        normalized_mode = self._normalize_mode(mode)
        tts = self._get_tts(normalized_mode)
        audio = tts.infer(text=merged_text, speed=0.8, temperature=0.7, top_p=0.9)

        output_dir = self.output_root / story_slug
        output_dir.mkdir(parents=True, exist_ok=True)
        chapter_suffix = ",".join(str(chapter) for chapter in chapter_numbers)
        output_name = f"{story_slug}_chuong-{chapter_suffix}.wav"
        output_path = output_dir / output_name
        tts.save(audio, str(output_path))

        return {
            "story_name": story_slug,
            "mode": normalized_mode,
            "chapters": chapter_numbers,
            "chapter_files": chapter_files,
            "output_file": str(output_path),
            "output_dir": str(output_dir),
        }

    def synthesize_story_chapters_with_clone_voice(
        self,
        story_name: str,
        chapters: Iterable[int],
        reference_audio_path: str,
        mode: str = "turbo",
        reference_text: str | None = None,
    ) -> dict[str, object]:
        story_slug, chapter_numbers, chapter_files, merged_text = self._load_story_chapter_texts(
            story_name=story_name, chapters=chapters
        )
        normalized_mode = self._normalize_mode(mode)
        reference_audio = Path(reference_audio_path)
        if not reference_audio.exists():
            raise ValueError(f"Khong tim thay reference audio: {reference_audio}")

        tts = self._get_tts(normalized_mode)
        resolved_text, text_from_file = self._resolve_reference_text(reference_audio, reference_text)
        ref_size_mb = reference_audio.stat().st_size / 1024 / 1024
        logger.info("encode_reference bat dau: %s (%.1f MB)", reference_audio, ref_size_mb)
        if normalized_mode == "standard":
            if not resolved_text:
                names = ", ".join(_REFERENCE_TEXT_FILENAMES)
                raise ValueError(
                    "mode='standard' can reference_text trong body hoac file transcript trong thu muc audio mau "
                    f"({names}) tai {reference_audio.parent}."
                )
            ref_codes = tts.encode_reference(str(reference_audio))
            logger.info("encode_reference hoan thanh, bat dau infer (standard)...")
            audio = tts.infer(
                text=merged_text,
                speed=0.8, temperature=0.7, top_p=0.9,
                ref_codes=ref_codes,
                ref_text=resolved_text,
            )
        else:
            cloned_voice = tts.encode_reference(str(reference_audio))
            logger.info("encode_reference hoan thanh, bat dau infer (turbo)...")
            audio = tts.infer(text=merged_text, voice=cloned_voice)
        logger.info("infer hoan thanh, dang luu file...")

        output_dir = self.output_root / story_slug
        output_dir.mkdir(parents=True, exist_ok=True)
        chapter_suffix = ",".join(str(chapter) for chapter in chapter_numbers)
        reference_slug = self._slugify(reference_audio.stem)
        output_name = f"{story_slug}_chuong-{chapter_suffix}_voice-{reference_slug}.wav"
        output_path = output_dir / output_name
        tts.save(audio, str(output_path))

        out: dict[str, object] = {
            "story_name": story_slug,
            "mode": normalized_mode,
            "chapters": chapter_numbers,
            "chapter_files": chapter_files,
            "reference_audio": str(reference_audio),
            "output_file": str(output_path),
            "output_dir": str(output_dir),
        }
        if text_from_file is not None:
            out["reference_text_file"] = text_from_file
        return out

    def _resolve_clone_output_dir(self, reference_audio: Path) -> Path:
        """
        Clone API luu theo voice: output/<ten-voice>.
        Uu tien ten thu muc cha cua sample audio, fallback sang ten file audio.
        """
        voice_folder = self._slugify(reference_audio.parent.name)
        if voice_folder == "story":
            voice_folder = self._slugify(reference_audio.stem)
        return Path("output") / voice_folder

    def _load_story_chapter_texts(
        self, story_name: str, chapters: Iterable[int]
    ) -> tuple[str, list[int], list[str], str]:
        chapter_numbers = sorted(set(chapters))
        if not chapter_numbers:
            raise ValueError("chapters khong duoc rong.")

        if any(chapter < 0 for chapter in chapter_numbers):
            raise ValueError("Moi chapter phai >= 0.")

        story_slug = self._slugify(story_name)
        story_dir = self.story_content_root / story_slug
        if not story_dir.exists():
            raise ValueError(f"Khong tim thay thu muc truyen: {story_dir}")

        chapter_texts: list[str] = []
        chapter_files: list[str] = []
        for chapter in chapter_numbers:
            chapter_file = self._resolve_chapter_file(story_dir, chapter)
            if chapter_file is None:
                raise ValueError(
                    f"Khong tim thay file cho chuong {chapter} trong truyen '{story_slug}'."
                )
            chapter_text = chapter_file.read_text(encoding="utf-8").strip()
            if chapter_text:
                chapter_texts.append(chapter_text)
                chapter_files.append(str(chapter_file))

        if not chapter_texts:
            raise ValueError("Khong co noi dung chuong de tao audio.")

        merged_text = "\n\n".join(chapter_texts)
        return story_slug, chapter_numbers, chapter_files, merged_text

    def _resolve_chapter_file(self, story_dir: Path, chapter: int) -> Path | None:
        patterns = [
            f"{chapter:04d}-*.md",
            f"chuong-{chapter}.md",
        ]
        for pattern in patterns:
            matches = sorted(story_dir.glob(pattern))
            if matches:
                return matches[0]
        return None

    def _get_tts(self, mode: str) -> Vieneu:
        tts = self._tts_by_mode.get(mode)
        if tts is None:
            tts = Vieneu(mode=mode)
            self._tts_by_mode[mode] = tts
        return tts

    def _normalize_mode(self, mode: str) -> str:
        normalized = mode.strip().lower()
        if normalized not in {"turbo", "standard"}:
            raise ValueError("mode phai la 'turbo' hoac 'standard'.")
        return normalized

    def _slugify(self, value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value)
        normalized = normalized.encode("ascii", "ignore").decode("ascii")
        normalized = normalized.lower()
        normalized = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
        return normalized or "story"

    def _resolve_reference_text(
        self, reference_audio: Path, explicit: str | None
    ) -> tuple[str | None, str | None]:
        """Tra ve (noi dung transcript, duong file neu doc tu disk). Uu tien body, sau do file trong thu muc mau."""
        if explicit and explicit.strip():
            return explicit.strip(), None
        sample_dir = reference_audio.parent
        for name in _REFERENCE_TEXT_FILENAMES:
            candidate = sample_dir / name
            if candidate.is_file():
                text = candidate.read_text(encoding="utf-8").strip()
                return text, str(candidate)
        return None, None
