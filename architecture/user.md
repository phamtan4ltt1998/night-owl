# User API Flow Diagrams

All endpoints require valid JWT (`Depends(get_current_user)`).

---

## GET /user/profile/{email}

Fetch user profile. Auth required (can only fetch own profile in practice — enforced by JWT dep).

```mermaid
flowchart TD
    Client[Client] -->|GET /user/profile/{email}\nAuthorization: Bearer required| AuthDep["Depends(get_current_user) → validate JWT"]
    AuthDep -- 401 --> 401[401]
    AuthDep -- ok --> Fetch["get_or_create_user(email)\n→ SELECT FROM users; INSERT if new"]
    Fetch --> Return["200 OK — user dict\n{id, email, name, bio, linh_thach, streak, picture}"]
```

---

## PUT /user/profile

Update display name and bio.

```mermaid
flowchart TD
    Client[Client] -->|PUT /user/profile\nAuthorization: Bearer required\nbody: {email, name, bio}| AuthDep["Depends(get_current_user)"]
    AuthDep -- 401 --> 401[401]
    AuthDep -- ok --> Update["update_user_profile(email, name, bio)\n→ UPDATE users SET name, bio WHERE email"]
    Update -- error --> 400[400 Bad Request]
    Update -- ok --> Return["200 OK — updated user dict"]
```

---

## POST /user/linh-thach/purchase

Credit Linh Thạch from in-app purchase. Auth required.

```mermaid
flowchart TD
    Client[Client] -->|POST /user/linh-thach/purchase\nAuthorization: Bearer required\nbody: {email, package_id, gems, bonus, price, label}| AuthDep["Depends(get_current_user)"]
    AuthDep -- 401 --> 401[401]
    AuthDep -- ok --> Calc["total = gems + bonus\ndesc = Mua {label} (+{total} Linh Thạch)"]
    Calc --> AddDB["add_linh_thach(user_id, total, desc, 'purchase')\n→ UPDATE users SET linh_thach += total\n→ INSERT linh_thach_history"]
    AddDB -- error --> 400[400]
    AddDB -- ok --> Return["200 OK\n{status: ok, balance, added}"]
```

---

## GET /user/linh-thach/history/{email}

Transaction history for Linh Thạch.

```mermaid
flowchart TD
    Client[Client] -->|GET /user/linh-thach/history/{email}?limit=20\nAuthorization: Bearer required| AuthDep["Depends(get_current_user)"]
    AuthDep -- 401 --> 401[401]
    AuthDep -- ok --> Fetch["get_linh_thach_history(user_id, limit)\n→ SELECT FROM linh_thach_history\nWHERE user_id ORDER BY created_at DESC LIMIT limit"]
    Fetch --> Return["200 OK — list of transactions\n[{amount, description, type, created_at}]"]
```

---

## POST /user/linh-thach/daily

Claim daily login reward (Linh Thạch + streak).

```mermaid
flowchart TD
    Client[Client] -->|POST /user/linh-thach/daily\nAuthorization: Bearer required\nbody: {email}| AuthDep["Depends(get_current_user)"]
    AuthDep -- 401 --> 401[401]
    AuthDep -- ok --> ClaimDB["claim_daily_reward(user_id)\n→ Check last_daily_reward date\n→ If already claimed today → ValueError\n→ Else: UPDATE streak, linh_thach\n→ INSERT linh_thach_history"]
    ClaimDB -- ValueError user not found --> 404[404]
    ClaimDB -- already claimed / other error --> 500[500]
    ClaimDB -- ok --> Return["200 OK\n{reward, streak, next_reward_at, balance}"]
```

---

## POST /user/reading-progress

Upsert last-read chapter for a book.

```mermaid
flowchart TD
    Client[Client] -->|POST /user/reading-progress\nAuthorization: Bearer required\nbody: {email, book_id, chapter_number}| AuthDep["Depends(get_current_user)"]
    AuthDep -- 401 --> 401[401]
    AuthDep -- ok --> Upsert["upsert_reading_progress(user_id, book_id, chapter_number)\n→ INSERT INTO reading_progress ... ON DUPLICATE KEY UPDATE\n  chapter_number, last_read = NOW()"]
    Upsert -- error --> 500[500]
    Upsert -- ok --> Return["200 OK\n{status: ok}"]
```

---

## GET /user/reading-history/{email}

List all books user has read, with last chapter and book metadata.

```mermaid
flowchart TD
    Client[Client] -->|GET /user/reading-history/{email}\nAuthorization: Bearer required| AuthDep["Depends(get_current_user)"]
    AuthDep -- 401 --> 401[401]
    AuthDep -- ok --> Fetch["get_reading_history(user_id)\n→ SELECT rp.*, b.* FROM reading_progress rp\nJOIN books b ON rp.book_id = b.id\nWHERE rp.user_id ORDER BY last_read DESC"]
    Fetch --> Transform["Map rows to:\n{bookId, chapterNumber, lastRead, book: {...}}"]
    Transform --> Return["200 OK — list of reading history entries"]
```
