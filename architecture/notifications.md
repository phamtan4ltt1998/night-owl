# Notifications API Flow Diagrams

Simple CRUD for notification bell. No auth required.

---

## GET /notifications

Fetch all notifications ordered newest first.

```mermaid
flowchart TD
    Client[Client] -->|GET /notifications| Handler
    Handler --> DB["SELECT * FROM notifications\nORDER BY id DESC"]
    DB --> Transform["Map rows to:\n{id, type, icon, title, body, time, unread}"]
    Transform --> Return["200 OK — list of notifications"]
```

---

## PATCH /notifications/{notif_id}/read

Mark single notification as read.

```mermaid
flowchart TD
    Client[Client] -->|PATCH /notifications/{notif_id}/read| Handler
    Handler --> DB["UPDATE notifications\nSET unread = 0\nWHERE id = notif_id"]
    DB --> Commit[conn.commit]
    Commit --> Return["200 OK\n{status: ok}"]
```

> Note: no 404 guard — silent no-op if ID doesn't exist.

---

## PATCH /notifications/read-all

Mark all notifications as read.

```mermaid
flowchart TD
    Client[Client] -->|PATCH /notifications/read-all| Handler
    Handler --> DB["UPDATE notifications\nSET unread = 0\n(no WHERE — all rows)"]
    DB --> Commit[conn.commit]
    Commit --> Return["200 OK\n{status: ok}"]
```
