import collections
import datetime
import os
import re
import threading
from typing import Callable

import pymysql
import pymysql.cursors
from dbutils.pooled_db import PooledDB

# ── Connection pool ────────────────────────────────────────────────────────────

_pool: PooledDB | None = None
_pool_lock = threading.Lock()


def _get_pool() -> PooledDB:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = PooledDB(
                    creator=pymysql,
                    mincached=2,
                    maxcached=10,
                    maxconnections=int(os.getenv("DB_MAX_CONNECTIONS", "20")),
                    blocking=True,
                    host=os.getenv("DB_HOST", "localhost"),
                    port=int(os.getenv("DB_PORT", 3306)),
                    user=os.getenv("DB_USER", "nightowl"),
                    password=os.getenv("DB_PASSWORD", "nightowl"),
                    database=os.getenv("DB_NAME", "nightowl"),
                    charset="utf8mb4",
                    cursorclass=pymysql.cursors.DictCursor,
                    autocommit=False,
                )
    return _pool


def get_conn() -> pymysql.connections.Connection:
    return _get_pool().connection()


# ── Books cache invalidation hook ─────────────────────────────────────────────
_invalidate_books_cache: Callable[[], None] | None = None

def register_books_cache_invalidator(fn: Callable[[], None]) -> None:
    global _invalidate_books_cache
    _invalidate_books_cache = fn


# Display metadata keyed by folder slug
BOOK_META = {
    "muc-than-ky": {
        "title": "Mục Thần Ký",
        "author": "Ẩm Nước Trong Gương",
        "genre": "Tiên hiệp",
        "c1": "#3538CD", "c2": "#6172F3",
        "emoji": "🔮",
        "desc": "Tại thế giới Tu Chân, Tư Không Mộc bắt đầu hành trình tu luyện từ một ngôi làng nhỏ, dần dần khám phá những bí ẩn về thần linh và quyền năng tối thượng.",
        "tags": "Đang ra",
        "words": "5.2M",
        "reads": "8.7M",
        "rating": 4.8,
    },
    "tien-nghich": {
        "title": "Tiên Nghịch",
        "author": "Nhĩ Căn",
        "genre": "Tiên hiệp",
        "c1": "#0E9384", "c2": "#15B8A6",
        "emoji": "⚡",
        "desc": "Vương Lâm — kẻ không có thiên phú tu luyện — nhờ một cơ duyên kỳ lạ mà bước vào con đường tu tiên, từng bước phá vỡ giới hạn của chính mình để đạt tới đỉnh cao quyền năng.",
        "tags": "Hoàn thành",
        "words": "6.8M",
        "reads": "11.2M",
        "rating": 4.9,
    },
}

STORY_CONTENT_ROOT = os.path.join(os.path.dirname(__file__), "..", "story-content")


def _parse_chapter_number(filename: str) -> int:
    m = re.search(r"chuong-(\d+)", filename)
    if m:
        return int(m.group(1))
    m = re.match(r"^(\d+)", filename)
    return int(m.group(1)) if m else 0


def _ensure_index(cur, table: str, index_name: str, columns: str, index_type: str = "INDEX") -> bool:
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM information_schema.STATISTICS "
        "WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s",
        (table, index_name),
    )
    if cur.fetchone()["cnt"] == 0:
        cur.execute(f"ALTER TABLE `{table}` ADD {index_type} {index_name} ({columns})")
        return True
    return False


def _ensure_ft_index(cur, index_name: str, columns: str) -> bool:
    return _ensure_index(cur, "books", index_name, columns, "FULLTEXT KEY")


def _ensure_btree_index(cur, index_name: str, columns: str) -> bool:
    return _ensure_index(cur, "books", index_name, columns)


def init_db() -> None:
    """Run init.sql idempotently, then ensure all indexes exist."""
    _run_init_sql()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS push_notifications (
                    id            INT AUTO_INCREMENT PRIMARY KEY,
                    external_id   INT          NOT NULL,
                    `key`         VARCHAR(255) NOT NULL,
                    source_app    VARCHAR(255) NOT NULL,
                    title         VARCHAR(500) NOT NULL,
                    body          TEXT         NOT NULL,
                    posted_at     BIGINT       NOT NULL,
                    posted_at_iso VARCHAR(50)  NOT NULL,
                    received_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_source_app (source_app),
                    INDEX idx_posted_at  (posted_at DESC),
                    UNIQUE KEY uq_key    (`key`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            conn.commit()

            changed = False
            # ── books: single-column ───────────────────────────────────────────
            changed |= _ensure_ft_index(cur, "ft_books_search", "title, author, description, tags")
            changed |= _ensure_ft_index(cur, "ft_books_title", "title")
            changed |= _ensure_btree_index(cur, "idx_genre", "genre")
            changed |= _ensure_btree_index(cur, "idx_read_count", "read_count")
            changed |= _ensure_btree_index(cur, "idx_rating", "rating")
            # ── books: composite (genre + sort_by) for get_books_paged ─────────
            changed |= _ensure_btree_index(cur, "idx_genre_read_count",    "genre, read_count")
            changed |= _ensure_btree_index(cur, "idx_genre_rating",        "genre, rating")
            changed |= _ensure_btree_index(cur, "idx_genre_chapter_count", "genre, chapter_count")
            changed |= _ensure_btree_index(cur, "idx_genre_title",         "genre, title")
            # ── inline_comments: comment-counts + paginated comments ───────────
            changed |= _ensure_index(cur, "inline_comments", "idx_ic_chapter_parent", "chapter_id, parent_id")
            changed |= _ensure_index(cur, "inline_comments", "idx_ic_chapter_para_parent_time",
                                     "chapter_id, paragraph_id, parent_id, created_at")
            if changed:
                conn.commit()

            # ── app_comments: global ephemeral comments (TTL 2 days) ───────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS app_comments (
                    id           INT AUTO_INCREMENT PRIMARY KEY,
                    user_id      INT          NOT NULL,
                    username     VARCHAR(150) NOT NULL,
                    avatar_url   TEXT,
                    content      VARCHAR(280) NOT NULL,
                    context_hint VARCHAR(255) DEFAULT NULL,
                    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    expires_at   DATETIME     NOT NULL,
                    INDEX idx_ac_expires  (expires_at),
                    INDEX idx_ac_created  (created_at DESC),
                    INDEX idx_ac_user     (user_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            conn.commit()
    finally:
        conn.close()


def _run_init_sql() -> None:
    """Execute init.sql against the live DB. All statements use IF NOT EXISTS / INSERT IGNORE — safe to re-run."""
    sql_path = os.path.join(os.path.dirname(__file__), "..", "init.sql")
    if not os.path.isfile(sql_path):
        return
    with open(sql_path, encoding="utf-8") as f:
        sql = f.read()

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Split on ; — skip empty and USE/SET statements that are Docker-only
            for stmt in sql.split(";"):
                stmt = stmt.strip()
                if not stmt:
                    continue
                # Skip USE / SET statements — already connected to correct DB
                upper = stmt.upper().lstrip()
                if upper.startswith("USE ") or upper.startswith("SET "):
                    continue
                # Skip CREATE DATABASE — server already selected DB via env
                if upper.startswith("CREATE DATABASE"):
                    continue
                try:
                    cur.execute(stmt)
                except Exception:
                    pass  # IF NOT EXISTS guards most; swallow duplicates silently
        conn.commit()
    finally:
        conn.close()


# ── Users ──────────────────────────────────────────────────────────────────────

def get_or_create_user(email: str, name: str = "", picture: str = "") -> dict:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
            if not row:
                cur.execute(
                    "INSERT INTO users (email, name, bio, linh_thach, picture) VALUES (%s, %s, '', 50, %s)",
                    (email, name or email.split("@")[0], picture or None),
                )
                conn.commit()
                cur.execute("SELECT * FROM users WHERE email = %s", (email,))
                row = cur.fetchone()
            elif picture and row.get("picture") != picture:
                cur.execute(
                    "UPDATE users SET picture = %s WHERE email = %s",
                    (picture, email),
                )
                conn.commit()
                cur.execute("SELECT * FROM users WHERE email = %s", (email,))
                row = cur.fetchone()
        return dict(row)
    finally:
        conn.close()


def update_user_profile(email: str, name: str, bio: str) -> dict:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET name=%s, bio=%s WHERE email=%s", (name, bio, email)
            )
            conn.commit()
            cur.execute("SELECT * FROM users WHERE email=%s", (email,))
            return dict(cur.fetchone())
    finally:
        conn.close()


def add_linh_thach(user_id: int, amount: int, desc: str, tx_type: str) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET linh_thach = linh_thach + %s WHERE id = %s",
                (amount, user_id),
            )
            cur.execute(
                "INSERT INTO linh_thach_history (user_id, type, `desc`, amount) VALUES (%s,%s,%s,%s)",
                (user_id, tx_type, desc, amount),
            )
            conn.commit()
            cur.execute("SELECT linh_thach FROM users WHERE id=%s", (user_id,))
            return cur.fetchone()["linh_thach"]
    finally:
        conn.close()


def get_linh_thach_history(user_id: int, limit: int = 20) -> list[dict]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM linh_thach_history WHERE user_id=%s ORDER BY id DESC LIMIT %s",
                (user_id, limit),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def claim_daily_reward(user_id: int) -> dict:
    today = datetime.date.today().isoformat()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT last_daily, streak, linh_thach FROM users WHERE id=%s", (user_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError("User not found")

            last_daily, streak = row["last_daily"], row["streak"]
            if last_daily == today:
                return {"already_claimed": True, "streak": streak, "balance": row["linh_thach"]}

            yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
            new_streak = streak + 1 if last_daily == yesterday else 1
            reward = 10 + (new_streak // 7) * 20

            cur.execute(
                "UPDATE users SET last_daily=%s, streak=%s, linh_thach=linh_thach+%s WHERE id=%s",
                (today, new_streak, reward, user_id),
            )
            cur.execute(
                "INSERT INTO linh_thach_history (user_id, type, `desc`, amount) VALUES (%s,'earn',%s,%s)",
                (user_id, f"Phần thưởng nhập {new_streak} ngày liên tiếp", reward),
            )
            conn.commit()
            cur.execute("SELECT linh_thach FROM users WHERE id=%s", (user_id,))
            balance = cur.fetchone()["linh_thach"]
        return {"already_claimed": False, "streak": new_streak, "reward": reward, "balance": balance}
    finally:
        conn.close()


CHAPTER_UNLOCK_COST = 5   # linh thạch mỗi chương
READING_HISTORY_MAX = 5   # tối đa 5 truyện lưu vết đọc mỗi user


def upsert_reading_progress(user_id: int, book_id: int, chapter_number: int) -> None:
    """Cập nhật tiến độ đọc. Nếu vượt quá READING_HISTORY_MAX truyện, xóa truyện cũ nhất."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO reading_history (user_id, book_id, chapter_number, last_read)
                   VALUES (%s, %s, %s, NOW())
                   ON DUPLICATE KEY UPDATE
                       chapter_number = VALUES(chapter_number),
                       last_read = NOW()""",
                (user_id, book_id, chapter_number),
            )
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM reading_history WHERE user_id = %s", (user_id,)
            )
            count = cur.fetchone()["cnt"]
            if count > READING_HISTORY_MAX:
                excess = count - READING_HISTORY_MAX
                cur.execute(
                    """DELETE FROM reading_history WHERE user_id = %s
                       ORDER BY last_read ASC LIMIT %s""",
                    (user_id, excess),
                )
        conn.commit()
    finally:
        conn.close()


def get_reading_history(user_id: int) -> list[dict]:
    """Trả về tối đa 5 truyện đọc gần nhất, kèm metadata sách."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT rh.book_id, rh.chapter_number, rh.last_read,
                          b.slug, b.title, b.author, b.genre, b.chapter_count,
                          b.rating, b.c1, b.c2, b.emoji, b.description,
                          b.tags, b.words, b.`reads`, b.updated
                   FROM reading_history rh
                   JOIN books b ON b.id = rh.book_id
                   WHERE rh.user_id = %s
                   ORDER BY rh.last_read DESC
                   LIMIT %s""",
                (user_id, READING_HISTORY_MAX),
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_unlocked_chapter_numbers(user_id: int, book_id: int) -> set[int]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT chapter_number FROM unlocked_chapters WHERE user_id=%s AND book_id=%s",
                (user_id, book_id),
            )
            return {r["chapter_number"] for r in cur.fetchall()}
    finally:
        conn.close()


def unlock_chapter(user_id: int, book_id: int, chapter_number: int) -> dict:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT free FROM chapters WHERE book_id=%s AND chapter_number=%s",
                (book_id, chapter_number),
            )
            ch = cur.fetchone()
            if not ch:
                raise ValueError("Chapter not found")
            if ch["free"]:
                return {"status": "free", "cost": 0}

            cur.execute(
                "SELECT 1 FROM unlocked_chapters WHERE user_id=%s AND book_id=%s AND chapter_number=%s",
                (user_id, book_id, chapter_number),
            )
            if cur.fetchone():
                cur.execute("SELECT linh_thach FROM users WHERE id=%s", (user_id,))
                balance = cur.fetchone()["linh_thach"]
                return {"status": "already_unlocked", "cost": 0, "balance": balance}

            cur.execute("SELECT linh_thach FROM users WHERE id=%s", (user_id,))
            user = cur.fetchone()
            if not user:
                raise ValueError("User not found")
            if user["linh_thach"] < CHAPTER_UNLOCK_COST:
                raise ValueError(
                    f"Không đủ Linh Thạch. Cần {CHAPTER_UNLOCK_COST}, hiện có {user['linh_thach']}."
                )

            cur.execute(
                "UPDATE users SET linh_thach = linh_thach - %s WHERE id=%s",
                (CHAPTER_UNLOCK_COST, user_id),
            )
            cur.execute(
                "INSERT INTO unlocked_chapters (user_id, book_id, chapter_number) VALUES (%s,%s,%s)",
                (user_id, book_id, chapter_number),
            )
            cur.execute(
                "INSERT INTO linh_thach_history (user_id, type, `desc`, amount) VALUES (%s,'spend',%s,%s)",
                (user_id, f"Mở khóa chương {chapter_number}", -CHAPTER_UNLOCK_COST),
            )
            cur.execute("SELECT linh_thach FROM users WHERE id=%s", (user_id,))
            new_balance = cur.fetchone()["linh_thach"]
        conn.commit()
        return {"status": "unlocked", "cost": CHAPTER_UNLOCK_COST, "balance": new_balance}
    finally:
        conn.close()


def update_book(book_id: int, title: str | None, author: str | None, free_chapter_threshold: int | None) -> dict:
    """Cập nhật metadata truyện và/hoặc đổi ngưỡng chương miễn phí."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM books WHERE id = %s", (book_id,))
            if not cur.fetchone():
                raise ValueError(f"Book {book_id} not found")

            # Cập nhật metadata nếu có
            fields, params = [], []
            if title is not None:
                fields.append("title = %s")
                params.append(title)
            if author is not None:
                fields.append("author = %s")
                params.append(author)
            if fields:
                params.append(book_id)
                cur.execute(f"UPDATE books SET {', '.join(fields)} WHERE id = %s", params)

            # Cập nhật ngưỡng chương tính phí nếu có
            if free_chapter_threshold is not None:
                if free_chapter_threshold == 0:
                    cur.execute(
                        "UPDATE chapters SET free = 1 WHERE book_id = %s",
                        (book_id,),
                    )
                else:
                    cur.execute(
                        "UPDATE chapters SET free = (chapter_number <= %s) WHERE book_id = %s",
                        (free_chapter_threshold, book_id),
                    )

            conn.commit()
            cur.execute("SELECT * FROM books WHERE id = %s", (book_id,))
            row = cur.fetchone()
            # Đếm số chương miễn phí hiện tại
            cur.execute("SELECT COUNT(*) AS cnt FROM chapters WHERE book_id = %s AND free = 1", (book_id,))
            free_count = cur.fetchone()["cnt"]
        return {"book": dict(row), "free_chapters": free_count}
    finally:
        conn.close()


_VALID_SORT_COLS = {"read_count", "rating", "id", "title", "chapter_count"}
_VALID_SORT_ORDERS = {"asc", "desc"}


def get_books_paged(
    page: int = 1,
    page_size: int = 24,
    genre: str | None = None,
    sort_by: str = "read_count",
    sort_order: str = "desc",
) -> dict:
    if sort_by not in _VALID_SORT_COLS:
        sort_by = "read_count"
    if sort_order not in _VALID_SORT_ORDERS:
        sort_order = "desc"

    offset = (page - 1) * page_size
    where = "WHERE genre = %s" if genre else ""
    params_filter: list = [genre] if genre else []

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) AS cnt FROM books {where}",
                params_filter,
            )
            total = cur.fetchone()["cnt"]

            cur.execute(
                f"SELECT id, slug, title, author, genre, chapter_count, `reads`, rating, "
                f"c1, c2, emoji, cover_image, status, read_count, updated, tags, words "
                f"FROM books {where} "
                f"ORDER BY {sort_by} {sort_order} "
                f"LIMIT %s OFFSET %s",
                params_filter + [page_size, offset],
            )
            rows = cur.fetchall()
        return {
            "data": [dict(r) for r in rows],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": max(1, -(-total // page_size)),
            },
        }
    finally:
        conn.close()


def get_existing_slugs(slugs: list[str]) -> set[str]:
    """Trả về subset slugs đã tồn tại trong bảng books."""
    if not slugs:
        return set()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(slugs))
            cur.execute(f"SELECT slug FROM books WHERE slug IN ({placeholders})", slugs)
            return {r["slug"] for r in cur.fetchall()}
    finally:
        conn.close()


def save_failed_crawl(
    story_url: str,
    error_message: str,
    story_limit: int | None = None,
    start_story_from: int = 1,
    free_chapter_threshold: int = 20,
) -> int:
    """Lưu request crawl bị lỗi. Trả về id."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO failed_crawl_requests
                   (story_url, story_limit, start_story_from, free_chapter_threshold, error_message)
                   VALUES (%s, %s, %s, %s, %s)""",
                (story_url, story_limit, start_story_from, free_chapter_threshold, error_message),
            )
            conn.commit()
            return cur.lastrowid
    finally:
        conn.close()


def get_pending_failed_crawls(max_retries: int = 5) -> list[dict]:
    """Lấy danh sách request lỗi chưa resolve và chưa vượt max_retries."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM failed_crawl_requests
                   WHERE resolved = 0 AND retry_count < %s
                   ORDER BY created_at ASC""",
                (max_retries,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def mark_crawl_resolved(record_id: int) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE failed_crawl_requests SET resolved = 1 WHERE id = %s",
                (record_id,),
            )
        conn.commit()
    finally:
        conn.close()


def increment_crawl_retry(record_id: int, error_message: str) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE failed_crawl_requests
                   SET retry_count = retry_count + 1, error_message = %s
                   WHERE id = %s""",
                (error_message, record_id),
            )
        conn.commit()
    finally:
        conn.close()


def upsert_story_from_dir(
    slug: str,
    story_name: str = "",
    free_chapter_threshold: int = 20,
    source_url: str = "",
    story_author: str = "",
    story_genre: str = "",
    story_status: str = "",
    story_description: str = "",
    story_cover: str = "",
) -> dict:
    story_dir = os.path.join(STORY_CONTENT_ROOT, slug)
    if not os.path.isdir(story_dir):
        raise ValueError(f"Khong tim thay thu muc: {story_dir}")

    chapter_files = sorted([f for f in os.listdir(story_dir) if f.endswith(".md")])
    chapter_count = len(chapter_files)

    # story_name từ scraper ưu tiên hơn BOOK_META, fallback về slug nếu không có
    meta = BOOK_META.get(slug, {
        "title": story_name if story_name else slug.replace("-", " ").title(),
        "author": "Không rõ",
        "genre": "Tiên hiệp",
        "c1": "#6941C6", "c2": "#9E77ED",
        "emoji": "📖",
        "desc": "",
        "tags": "Đang ra",
        "words": "0",
        "reads": "0",
        "rating": 4.5,
    })

    # Scraped metadata overrides defaults (but not BOOK_META hardcoded entries)
    if slug not in BOOK_META:
        if story_author:
            meta["author"] = story_author
        if story_genre:
            meta["genre"] = story_genre
        if story_description:
            meta["desc"] = story_description
        if story_status:
            meta["tags"] = story_status

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM books WHERE slug = %s", (slug,))
            existing = cur.fetchone()
            if existing:
                book_id = existing["id"]
                update_fields = "chapter_count=%s, updated=%s"
                update_params = [chapter_count, f"{chapter_count} chương"]
                if source_url:
                    update_fields += ", source_url=%s"
                    update_params.append(source_url)
                if story_cover:
                    update_fields += ", cover_image=%s"
                    update_params.append(story_cover)
                if story_author:
                    update_fields += ", author=%s"
                    update_params.append(story_author)
                if story_genre:
                    update_fields += ", genre=%s"
                    update_params.append(story_genre)
                if story_status:
                    update_fields += ", status=%s"
                    update_params.append(story_status)
                if story_description:
                    update_fields += ", description=%s"
                    update_params.append(story_description)
                update_params.append(book_id)
                cur.execute(f"UPDATE books SET {update_fields} WHERE id=%s", update_params)
            else:
                cur.execute(
                    """INSERT INTO books
                       (slug, title, author, genre, chapter_count, `reads`, rating,
                        c1, c2, emoji, description, tags, words, updated, source_url,
                        cover_image, status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        slug, meta["title"], meta["author"], meta["genre"],
                        chapter_count, meta["reads"], meta["rating"],
                        meta["c1"], meta["c2"], meta["emoji"],
                        meta["desc"], meta["tags"], meta["words"],
                        f"{chapter_count} chương", source_url,
                        story_cover, story_status,
                    ),
                )
                book_id = cur.lastrowid

            cur.execute(
                "SELECT chapter_number FROM chapters WHERE book_id = %s", (book_id,)
            )
            existing_numbers = {r["chapter_number"] for r in cur.fetchall()}

            new_rows = []
            for fname in chapter_files:
                ch_num = _parse_chapter_number(fname)
                if ch_num in existing_numbers:
                    continue
                file_path = os.path.join(story_dir, fname)
                free = 1 if free_chapter_threshold == 0 or ch_num <= free_chapter_threshold else 0
                # Đọc tên chương thật từ dòng đầu file (# Title)
                ch_title = f"Chương {ch_num}"
                try:
                    with open(file_path, encoding="utf-8") as fh:
                        first_line = fh.readline().strip()
                        if first_line.startswith("#"):
                            raw_title = first_line.lstrip("#").strip()
                            # Strip markdown link [label](url) → label
                            cleaned = re.sub(r'\[([^\]]+)\]\([^\)]*\)', r'\1', raw_title).strip().strip('"').strip()
                            ch_title = cleaned or ch_title
                except OSError:
                    pass
                new_rows.append((book_id, ch_num, ch_title, file_path, free))

            if new_rows:
                cur.executemany(
                    "INSERT IGNORE INTO chapters (book_id, chapter_number, title, file_path, free) VALUES (%s,%s,%s,%s,%s)",
                    new_rows,
                )

        conn.commit()
        if _invalidate_books_cache is not None:
            _invalidate_books_cache()
        return {"book_id": book_id, "slug": slug, "new_chapters": len(new_rows), "total_chapters": chapter_count}
    finally:
        conn.close()


def increment_chapter_view(book_id: int, chapter_number: int) -> None:
    """Increment view_count on chapter and read_count on book (fire-and-forget)."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE chapters SET view_count = view_count + 1 WHERE book_id = %s AND chapter_number = %s",
                (book_id, chapter_number),
            )
            cur.execute(
                "UPDATE books SET read_count = read_count + 1 WHERE id = %s",
                (book_id,),
            )
        conn.commit()
    finally:
        conn.close()


def get_chapter_views(book_id: int) -> dict[int, int]:
    """Return {chapter_number: view_count} for all chapters of a book."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT chapter_number, view_count FROM chapters WHERE book_id = %s",
                (book_id,),
            )
            rows = cur.fetchall()
        return {r["chapter_number"]: r["view_count"] for r in rows}
    finally:
        conn.close()


def get_chapters_paged(book_id: int, page: int = 1, page_size: int = 200) -> dict:
    """Return paginated chapter list with total count."""
    offset = (page - 1) * page_size
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM chapters WHERE book_id = %s", (book_id,))
            total = cur.fetchone()["cnt"]
            cur.execute(
                "SELECT id, chapter_number, title, free, view_count FROM chapters "
                "WHERE book_id = %s ORDER BY chapter_number LIMIT %s OFFSET %s",
                (book_id, page_size, offset),
            )
            rows = cur.fetchall()
        return {
            "total": total,
            "data": [dict(r) for r in rows],
        }
    finally:
        conn.close()


# ── View-count batch buffer ────────────────────────────────────────────────────

_view_counter: collections.Counter = collections.Counter()
_view_lock = threading.Lock()


def record_chapter_view(book_id: int, chapter_number: int) -> None:
    """Thread-safe: buffer a view event; flushed in batch every ~10s."""
    with _view_lock:
        _view_counter[(book_id, chapter_number)] += 1


def flush_view_counts() -> None:
    """Drain the in-memory buffer and write batched counts to DB."""
    with _view_lock:
        if not _view_counter:
            return
        snapshot = dict(_view_counter)
        _view_counter.clear()

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for (book_id, chapter_number), count in snapshot.items():
                cur.execute(
                    "UPDATE chapters SET view_count = view_count + %s "
                    "WHERE book_id = %s AND chapter_number = %s",
                    (count, book_id, chapter_number),
                )
                cur.execute(
                    "UPDATE books SET read_count = read_count + %s WHERE id = %s",
                    (count, book_id),
                )
        conn.commit()
    finally:
        conn.close()


# ── Inline Comments (Wattpad-style) ────────────────────────────────────────────

def get_comment_counts(chapter_id: int) -> dict[str, int]:
    """Get comment count per paragraph_id for a chapter. Only returns paragraphs with comments."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT paragraph_id, COUNT(*) AS cnt FROM inline_comments WHERE chapter_id = %s AND parent_id IS NULL GROUP BY paragraph_id",
                (chapter_id,),
            )
            rows = cur.fetchall()
        return {r["paragraph_id"]: r["cnt"] for r in rows}
    finally:
        conn.close()


def get_paragraph_comments(chapter_id: int, paragraph_id: str, page: int = 1, limit: int = 10) -> dict:
    """Get paginated comments for a specific paragraph (only top-level, not replies)."""
    offset = (page - 1) * limit
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM inline_comments WHERE chapter_id = %s AND paragraph_id = %s AND parent_id IS NULL",
                (chapter_id, paragraph_id),
            )
            total = cur.fetchone()["cnt"]

            cur.execute(
                """SELECT c.id, c.user_id, c.content, c.created_at, u.name, u.picture
                   FROM inline_comments c
                   LEFT JOIN users u ON u.id = c.user_id
                   WHERE c.chapter_id = %s AND c.paragraph_id = %s AND c.parent_id IS NULL
                   ORDER BY c.created_at DESC
                   LIMIT %s OFFSET %s""",
                (chapter_id, paragraph_id, limit, offset),
            )
            comments = [dict(r) for r in cur.fetchall()]

            if comments:
                parent_ids = [c["id"] for c in comments]
                placeholders = ",".join(["%s"] * len(parent_ids))
                cur.execute(
                    f"""SELECT c.id, c.user_id, c.content, c.created_at, c.parent_id, u.name, u.picture
                       FROM inline_comments c
                       LEFT JOIN users u ON u.id = c.user_id
                       WHERE c.parent_id IN ({placeholders})
                       ORDER BY c.created_at ASC""",
                    parent_ids,
                )
                replies_by_parent: dict[int, list] = {}
                for r in cur.fetchall():
                    replies_by_parent.setdefault(r["parent_id"], []).append(dict(r))
                for comment in comments:
                    comment["replies"] = replies_by_parent.get(comment["id"], [])
            else:
                for comment in comments:
                    comment["replies"] = []

        return {
            "data": comments,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "total_pages": max(1, -(-total // limit)),
            },
        }
    finally:
        conn.close()


def post_inline_comment(chapter_id: int, paragraph_id: str, user_id: int, content: str, parent_id: int | None = None) -> dict:
    """Post a new inline comment. Returns comment object with user info."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Verify chapter exists
            cur.execute("SELECT id FROM chapters WHERE id = %s", (chapter_id,))
            if not cur.fetchone():
                raise ValueError("Chapter not found")

            # Verify user exists
            cur.execute("SELECT name, picture FROM users WHERE id = %s", (user_id,))
            user = cur.fetchone()
            if not user:
                raise ValueError("User not found")

            # Insert comment
            cur.execute(
                """INSERT INTO inline_comments (chapter_id, paragraph_id, user_id, content, parent_id)
                   VALUES (%s, %s, %s, %s, %s)""",
                (chapter_id, paragraph_id, user_id, content, parent_id),
            )
            comment_id = cur.lastrowid
            conn.commit()

            return {
                "id": comment_id,
                "chapter_id": chapter_id,
                "paragraph_id": paragraph_id,
                "user_id": user_id,
                "content": content,
                "parent_id": parent_id,
                "created_at": datetime.datetime.now().isoformat(),
                "name": user["name"],
                "picture": user["picture"],
            }
    finally:
        conn.close()


def delete_inline_comment(comment_id: int, user_id: int) -> bool:
    """Delete a comment (only if user is owner). Returns True if deleted, False if not found/not owner."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Check ownership
            cur.execute("SELECT user_id FROM inline_comments WHERE id = %s", (comment_id,))
            row = cur.fetchone()
            if not row:
                return False
            if row["user_id"] != user_id:
                return False

            # Delete comment (will cascade delete replies via FK)
            cur.execute("DELETE FROM inline_comments WHERE id = %s", (comment_id,))
            conn.commit()
            return True
    finally:
        conn.close()


# ── Global ephemeral app comments (TTL 2 days) ────────────────────────────────

def get_app_comments(limit: int = 50, requesting_user_id: int | None = None) -> list[dict]:
    """Fetch live app_comments (not yet expired), newest first."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, user_id, username, avatar_url, content, context_hint, created_at, expires_at
                   FROM app_comments
                   WHERE expires_at > NOW()
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (limit,),
            )
            rows = cur.fetchall()
            result = []
            for r in rows:
                result.append({
                    "id": r["id"],
                    "user_id": r["user_id"],
                    "username": r["username"],
                    "avatar_url": r["avatar_url"],
                    "content": r["content"],
                    "context_hint": r["context_hint"],
                    "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"]),
                    "expires_at": r["expires_at"].isoformat() if hasattr(r["expires_at"], "isoformat") else str(r["expires_at"]),
                    "is_own": (requesting_user_id is not None and r["user_id"] == requesting_user_id),
                })
            return result
    finally:
        conn.close()


def post_app_comment(
    user_id: int,
    username: str,
    avatar_url: str | None,
    content: str,
    context_hint: str | None = None,
    ttl_days: int = 2,
) -> dict:
    """Insert a new app_comment with expires_at = NOW() + ttl_days."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO app_comments (user_id, username, avatar_url, content, context_hint, expires_at)
                   VALUES (%s, %s, %s, %s, %s, DATE_ADD(NOW(), INTERVAL %s DAY))""",
                (user_id, username, avatar_url, content, context_hint, ttl_days),
            )
            comment_id = cur.lastrowid
            conn.commit()
            cur.execute(
                "SELECT id, user_id, username, avatar_url, content, context_hint, created_at, expires_at FROM app_comments WHERE id = %s",
                (comment_id,),
            )
            row = cur.fetchone()
            return {
                "id": row["id"],
                "user_id": row["user_id"],
                "username": row["username"],
                "avatar_url": row["avatar_url"],
                "content": row["content"],
                "context_hint": row["context_hint"],
                "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
                "expires_at": row["expires_at"].isoformat() if hasattr(row["expires_at"], "isoformat") else str(row["expires_at"]),
                "is_own": True,
            }
    finally:
        conn.close()


def delete_app_comment(comment_id: int, user_id: int) -> bool:
    """Delete app_comment. Only owner can delete. Returns True if deleted."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM app_comments WHERE id = %s", (comment_id,))
            row = cur.fetchone()
            if not row:
                return False
            if row["user_id"] != user_id:
                return False
            cur.execute("DELETE FROM app_comments WHERE id = %s", (comment_id,))
            conn.commit()
            return True
    finally:
        conn.close()


def purge_expired_app_comments() -> int:
    """Hard-delete expired app_comments. Returns count deleted."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM app_comments WHERE expires_at < NOW()")
            deleted = cur.rowcount
            conn.commit()
            return deleted
    finally:
        conn.close()


def save_push_notification(
    external_id: int,
    key: str,
    source_app: str,
    title: str,
    body: str,
    posted_at: int,
    posted_at_iso: str,
) -> int:
    """Insert push notification. Returns new row id. Raises on duplicate key."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO push_notifications
                    (external_id, `key`, source_app, title, body, posted_at, posted_at_iso)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (external_id, key, source_app, title, body, posted_at, posted_at_iso),
            )
            conn.commit()
            return cur.lastrowid
    finally:
        conn.close()
