# TTS API Flow Diagrams

Text-to-Speech pipeline. Reads markdown from `story-content/<slug>/`, outputs `.wav` to `outputs/audio/<slug>/`.

---

## GET /tts/story/{story_name}/chapters/{chapter_number}/status

Check if audio file exists for a chapter.

```mermaid
flowchart TD
    Client[Client] -->|GET /tts/story/{story_name}/chapters/{chapter_number}/status| Handler
    Handler --> SlugPath["story_slug = tts_service._slugify(story_name)\naudio_path = outputs/audio/{slug}/{chapter_number:04d}.wav\nstory_dir = story-content/{slug}/"]
    SlugPath --> Return["200 OK\n{audio_exists: bool, content_exists: bool, story_name: slug}"]
```

---

## POST /tts/story

Generate TTS for specified chapters. Runs in background.

```mermaid
flowchart TD
    Client[Client] -->|POST /tts/story\nbody: {story_name, chapters: [int], mode: turbo|standard}| Handler
    Handler --> SlugCheck["story_slug = _slugify(story_name)\nstory_dir = story-content/{slug}/"]
    SlugCheck --> DirExists{story_dir\nexists?}
    DirExists -- no --> 400[400 story dir not found]
    DirExists -- yes --> SpawnBg["BackgroundTasks.add_task(\n  _run_tts_background,\n  story_name, chapters, mode\n)"]
    SpawnBg --> Return200["200 OK\n{status: generating, story_name: slug, chapters}"]

    SpawnBg -.->|background| TTS

    subgraph TTS["_run_tts_background"]
        Loop["run_in_executor:\ntts_service.synthesize_story_chapters(\n  story_name, chapters, mode\n)"]
        Loop --> ReadMD["For each chapter:\nread story-content/{slug}/{num:04d}-*.md"]
        ReadMD --> Synth["synthesize text → .wav\nmode=turbo: fast model\nmode=standard: standard model"]
        Synth --> SaveWav["save to outputs/audio/{slug}/{num:04d}.wav"]
    end
```

---

## GET /tts/story/{story_name}/chapters/{chapter_number}/audio

Stream chapter audio with HTTP Range support.

```mermaid
flowchart TD
    Client[Client] -->|GET /tts/story/{story_name}/chapters/{chapter_number}/audio\nRange: bytes=X-Y  optional| Handler
    Handler --> PathCheck["audio_path = get_chapter_audio_path(story_name, chapter_number)"]
    PathCheck --> FileExists{audio file\nexists?}
    FileExists -- no --> 404[404 Audio not yet generated]
    FileExists -- yes --> RangeHeader{Range header\npresent?}
    RangeHeader -- yes --> ParseRange["parse bytes=start-end\nclamp end to file_size-1"]
    ParseRange --> Stream206["StreamingResponse\n_iter_file(path, start, end, chunk=65536)\n206 Partial Content\nContent-Range, Content-Length, Accept-Ranges headers"]
    RangeHeader -- no --> Stream200["StreamingResponse\n_iter_file(path, 0, file_size-1)\n200 OK\nContent-Length, Accept-Ranges headers"]
```

---

## POST /tts/story/clone

Generate TTS with voice cloning from reference audio. Blocking (synchronous in threadpool).

```mermaid
flowchart TD
    Client[Client] -->|POST /tts/story/clone\nbody: {story_name, chapters, mode, reference_audio_path, reference_text?}| Handler
    Handler --> Threadpool["run_in_threadpool:\ntts_service.synthesize_story_chapters_with_clone_voice(\n  story_name, chapters,\n  reference_audio_path, mode, reference_text\n)"]
    Threadpool --> LoadRef["Load reference audio from reference_audio_path\nIf reference_text is None:\n  try read reference.txt / reference_text.txt\n  in same dir as reference audio"]
    LoadRef --> VoiceClone["Clone voice using 'vieneu' model\n(standard mode only)"]
    VoiceClone --> Synth["Synthesize chapters with cloned voice\n→ save .wav to outputs/audio/{slug}/"]
    Synth -- ValueError --> 400[400 Bad Request]
    Synth -- Exception --> 500[500 TTS clone error]
    Synth -- ok --> Return["200 OK\n{status: ok}"]
```
