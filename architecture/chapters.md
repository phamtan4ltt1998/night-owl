# Chapters API Flow Diagrams

## GET /books/{book_id}/chapters

List chapters + issue session token for content access.

```mermaid
flowchart TD
    Client[Client] -->|GET /books/{book_id}/chapters\nAuthorization: Bearer? optional| RateLimit{Rate limit}
    RateLimit -- exceeded --> 429[429]
    RateLimit -- ok --> BookCheck["SELECT id FROM books WHERE id = book_id"]
    BookCheck -- not found --> 404[404 Book not found]
    BookCheck -- found --> FetchChapters["SELECT id, chapter_number, title, free, view_count\nFROM chapters WHERE book_id ORDER BY chapter_number"]

    FetchChapters --> HasJWT{Bearer token\npresent?}
    HasJWT -- yes --> DecodeJWT["jwt.decode → uid"]
    DecodeJWT -- ok --> GetUnlocked["get_unlocked_chapter_numbers(uid, book_id)\n→ MySQL"]
    DecodeJWT -- JWTError --> UseUID0[uid = 0, unlocked = empty]
    HasJWT -- no --> UseUID0

    GetUnlocked --> MakeToken
    UseUID0 --> MakeToken

    MakeToken{SESSION_TOKEN\nENABLED?}
    MakeToken -- yes --> GenToken["_make_session_token(uid, book_id)\nHMAC-SHA256, TTL=10min\nbound to uid+book_id"]
    MakeToken -- no --> EmptyToken[session_token = '']

    GenToken --> BuildResponse
    EmptyToken --> BuildResponse

    BuildResponse["Build chapter list:\n{id, chapterNumber, title, free, unlocked, viewCount}\nunlocked = free OR chapter_number in unlocked_set"]
    BuildResponse --> Return["200 OK\n{session_token, chapters: [...]}"]
```

---

## POST /books/{book_id}/chapters/{chapter_number}/unlock

Spend Linh Thạch to unlock a paid chapter. Requires auth.

```mermaid
flowchart TD
    Client[Client] -->|POST /books/{book_id}/chapters/{chapter_number}/unlock\nAuthorization: Bearer required| AuthDep["Depends(get_current_user)\n→ validate JWT"]
    AuthDep -- 401 --> 401[401 Unauthorized]
    AuthDep -- ok --> UnlockDB["unlock_chapter(user_id, book_id, chapter_number)\n→ MySQL:\n  1. Check chapter exists & is paid\n  2. Check user balance ≥ cost\n  3. Deduct Linh Thạch\n  4. INSERT chapter_unlocks"]
    UnlockDB -- ValueError --> 400["400 Bad Request\n(already unlocked / insufficient balance / free chapter)"]
    UnlockDB -- ok --> Return["200 OK\n{status, linh_thach_remaining, chapter_number}"]
```

---

## GET /books/{book_id}/chapters/{chapter_number}/content

Read chapter content. Anti-scraping: session token required, rate limited, view count incremented.

```mermaid
flowchart TD
    Client[Client] -->|GET /books/{id}/chapters/{num}/content\n?session_token=...\nAuthorization: Bearer? optional| RateLimit{Rate limit\ncheck}
    RateLimit -- exceeded --> 429[429]
    RateLimit -- ok --> ExtractUID{Bearer token?}

    ExtractUID -- yes --> DecodeUID["jwt.decode → uid\nJWTError → 401"]
    ExtractUID -- no --> UID0[uid = 0]

    DecodeUID --> SessionCheck
    UID0 --> SessionCheck

    SessionCheck{SESSION_TOKEN\nENABLED?}
    SessionCheck -- yes, no token --> 422[422 session_token required]
    SessionCheck -- yes, has token --> VerifyToken["_verify_session_token(token, uid, book_id)\n1. base64 decode\n2. check exp timestamp\n3. check uid+book_id match\n4. hmac.compare_digest(sig, expected)"]
    VerifyToken -- invalid/expired --> 403[403 Session token invalid or expired]
    VerifyToken -- ok --> DBFetch
    SessionCheck -- disabled --> DBFetch

    DBFetch["SELECT title, file_path, free FROM chapters\nWHERE book_id AND chapter_number"]
    DBFetch -- not found --> 404[404 Chapter not found]
    DBFetch -- found --> AccessCheck{chapter.free?}

    AccessCheck -- yes --> CacheCheck
    AccessCheck -- no --> AuthCheck{uid > 0?}
    AuthCheck -- no --> 401Auth[401 Login required]
    AuthCheck -- yes --> UnlockCheck["get_unlocked_chapter_numbers(uid, book_id)"]
    UnlockCheck --> IsUnlocked{chapter_number\nin unlocked?}
    IsUnlocked -- no --> 403Locked[403 locked]
    IsUnlocked -- yes --> CacheCheck

    CacheCheck{_content_cache\nhit?}
    CacheCheck -- HIT --> ReturnContent
    CacheCheck -- MISS --> ReadFile["run_in_threadpool: read file_path\n_clean_chapter_content():\n  1. Strip first # heading\n  2. Remove markdown links [text](url) → text\n_clean_chapter_title(): strip link syntax"]
    ReadFile -- FileNotFound --> 404File[404 Content file not found]
    ReadFile -- ok --> StoreCache["_content_cache[(book_id, chapter_number)]\nTTL=30min"]
    StoreCache --> ReturnContent

    ReturnContent["Return {chapterNumber, title, free, content}"]
    ReturnContent --> IncrView["BackgroundTask:\nincrement_chapter_view(book_id, chapter_number)"]
```
