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
