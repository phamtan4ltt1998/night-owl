# Story Scraper Backend (Crawl4AI)

Service backend nay crawl du lieu truyẹn tu:

- `https://truyencom.com/truyen-xuyen-nhanh/full/`

Va luu theo dung cau truc:

- `story/ten-truyen-clone/`
- file chapter theo thu tu tang dan: `0001-...md`, `0002-...md`, ...

## 1) Cai dat

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

Sau khi cai `crawl4ai`, neu can hay chay:

```bash
crawl4ai-setup
crawl4ai-doctor
```

Neu van loi browser:

```bash
python -m playwright install --with-deps chromium
```

## 2) Chay service

```bash
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 3) Goi API crawl

```bash
curl -X POST "http://127.0.0.1:8000/crawl" \
  -H "Content-Type: application/json" \
  -d '{
    "story_url": "https://truyencom.com/truyen-xuyen-nhanh/full/",
    "story_limit": 3,
    "start_story_from": 2
  }'
```

Trong do:

- `story_limit`: crawl toi da `n` truyen (bo trong thi crawl tat ca)
- `start_story_from`: bat dau tu truyen thu bao nhieu (1-based, mac dinh = 1)

Ket qua tra ve se cho biet:

- `output_dir`
- `chapter_count`
- `metadata_file`

## 4) Cac endpoint

- `GET /health`
- `POST /crawl`
- `POST /tts/story`
- `POST /tts/story/clone`

## 5) TTS tu story-content

API se doc file markdown trong `story-content/<ten-truyen>/` theo chapter ban truyen vao,
ghep noi dung roi tao file audio `.wav`.

Vi du request:

```bash
curl -X POST "http://127.0.0.1:8000/tts/story" \
  -H "Content-Type: application/json" \
  -d '{
    "story_name": "muc-than-ky",
    "chapters": [1, 2],
    "mode": "turbo"
  }'
```

`mode` ho tro:

- `turbo` (mac dinh, nhanh hon)
- `standard` (chat luong uu tien hon, cham hon)

Voi request tren, service se lay:

- `story-content/muc-than-ky/0001-*.md`
- `story-content/muc-than-ky/0002-*.md`

Va luu audio vao:

- `outputs/audio/muc-than-ky/muc-than-ky_chuong-1-2.wav`
- `outputs/audio/muc-than-ky/muc-than-ky_chuong-1,2.wav`

Chapter duoc phep >= 0. Service uu tien tim theo:

- `000x-*.md` (vi du `0001-*.md`)
- `chuong-x.md` (vi du `chuong-0.md`)

## 6) TTS Zero-shot Voice Cloning (SDK)

API clone giong tu sample audio (mac dinh su dung file:
`input/sample-voice/nguyen-ngoc-ngan/nguyen_ngoc_ngan.mp3`).

```bash
curl -X POST "http://127.0.0.1:8000/tts/story/clone" \
  -H "Content-Type: application/json" \
  -d '{
    "story_name": "muc-than-ky",
    "chapters": [0],
    "mode": "turbo",
    "reference_audio_path": "input/sample-voice/nguyen-ngoc-ngan/nguyen_ngoc_ngan.mp3"
  }'
```

Ket qua audio duoc luu vao `output/<ten-voice>/` voi ten:
`<ten-truyen>_chuong-x,y_voice-<ten-sample>.wav`

Vi du voi `reference_audio_path = input/sample-voice/mc-minh-nguyet/mc-minh-nguyet.wav`,
file se duoc luu trong `output/mc-minh-nguyet/`.

Luu y voi `mode: "standard"`:

- Can transcript dung voi audio mau: hoac truyen `reference_text` trong JSON, hoac dat file trong cung thu muc voi file mp3 (vi du `input/sample-voice/nguyen-ngoc-ngan/`): `reference.txt`, `reference_text.txt`, hoac file ten `reference text`.
- Khi doc tu file, response co them `reference_text_file` (duong dan file da dung).
- Vi du chi can audio + file transcript (khong can `reference_text` trong body):

```json
{
  "story_name": "muc-than-ky",
  "chapters": [0],
  "mode": "standard",
  "reference_audio_path": "input/sample-voice/nguyen-ngoc-ngan/nguyen_ngoc_ngan.mp3"
}
```

Hoac ghi de bang body:

```json
{
  "story_name": "muc-than-ky",
  "chapters": [0],
  "mode": "standard",
  "reference_audio_path": "input/sample-voice/nguyen-ngoc-ngan/nguyen_ngoc_ngan.mp3",
  "reference_text": "Noi dung dung voi audio mau"
}
```
