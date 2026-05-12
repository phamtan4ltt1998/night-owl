# Comments API Flow Diagrams

Wattpad-style inline comments. `paragraph_id`: `p0`, `p1`... = paragraph index; `_chapter` = end-of-chapter.

---

## GET /books/{book_id}/chapters/{chapter_number}/comment-counts

Returns map of `{paragraph_id: count}` — only paragraphs with comments.

```mermaid
flowchart TD
    Client[Client] -->|GET /books/{book_id}/chapters/{chapter_number}/comment-counts| Handler
    Handler --> VerifyChapter["SELECT id FROM chapters\nWHERE book_id AND chapter_number"]
    VerifyChapter -- not found --> 404[404 Chapter not found]
    VerifyChapter -- found --> GetCounts["get_comment_counts(chapter_id)\n→ SELECT paragraph_id, COUNT(*)\nFROM inline_comments\nWHERE chapter_id AND parent_id IS NULL\n(top-level only, excludes replies)\nGROUP BY paragraph_id"]
    GetCounts --> Return["200 OK\n{p0: 3, p5: 1, _chapter: 2, ...}"]
```

---

## GET /books/{book_id}/chapters/{chapter_number}/paragraphs/{paragraph_id}/comments

Paginated comments + nested replies for one paragraph.

```mermaid
flowchart TD
    Client[Client] -->|GET .../paragraphs/{paragraph_id}/comments\n?page=1&limit=10| Handler
    Handler --> VerifyChapter["SELECT id FROM chapters\nWHERE book_id AND chapter_number"]
    VerifyChapter -- not found --> 404[404 Chapter not found]
    VerifyChapter -- found --> FetchComments["get_paragraph_comments(chapter_id, paragraph_id, page, limit)\n→ SELECT top-level comments WHERE parent_id IS NULL\n  + nested SELECT replies per comment\n  LIMIT/OFFSET for pagination"]
    FetchComments --> Return["200 OK\n{data: [{id, content, user, replies: [...], ...}], total, page, limit}"]
```

---

## POST /comments/inline

Post new comment or reply. Auth required.

```mermaid
flowchart TD
    Client[Client] -->|POST /comments/inline\nAuthorization: Bearer required\nbody: {chapter_id, paragraph_id, content, parent_id?}| AuthCheck{Bearer token\npresent?}
    AuthCheck -- no --> 401A[401 Authentication required]
    AuthCheck -- yes --> DecodeJWT["jwt.decode → uid\nJWTError → 401"]
    DecodeJWT --> ValidUID{uid present?}
    ValidUID -- no --> 401B[401 Invalid token]
    ValidUID -- yes --> Validate["Pydantic validation:\nparagraph_id max 100 chars\ncontent max 1000 chars"]
    Validate -- error --> 422[422 Validation error]
    Validate -- ok --> Insert["post_inline_comment(\n  chapter_id, paragraph_id,\n  user_id, content, parent_id\n)\n→ MySQL INSERT inline_comments"]
    Insert -- ValueError --> 400[400 Bad Request]
    Insert -- ok --> Return["200 OK\n{id, chapter_id, paragraph_id,\n user_id, content, parent_id,\n created_at}"]
```

---

## DELETE /comments/inline/{comment_id}

Delete comment. Owner only. Auth required.

```mermaid
flowchart TD
    Client[Client] -->|DELETE /comments/inline/{comment_id}\nAuthorization: Bearer required| AuthCheck{Bearer token\npresent?}
    AuthCheck -- no --> 401A[401 Authentication required]
    AuthCheck -- yes --> DecodeJWT["jwt.decode → uid\nJWTError → 401"]
    DecodeJWT --> ValidUID{uid present?}
    ValidUID -- no --> 401B[401 Invalid token]
    ValidUID -- yes --> Delete["delete_inline_comment(comment_id, user_id)\n→ DELETE FROM inline_comments\nWHERE id = comment_id AND user_id = user_id"]
    Delete -- not found or wrong owner --> 404[404 Comment not found or permission denied]
    Delete -- deleted --> Return["200 OK\n{status: deleted, comment_id}"]
```
