import datetime
import os
import re

import pymysql
import pymysql.cursors

# ── Connection ─────────────────────────────────────────────────────────────────

def get_conn() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER", "nightowl"),
        password=os.getenv("DB_PASSWORD", "nightowl"),
        database=os.getenv("DB_NAME", "nightowl"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


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


def init_db() -> None:
    """Schema managed by init.sql. No seeding here."""
    pass


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
                fields.append("title = %s"); params.append(title)
            if author is not None:
                fields.append("author = %s"); params.append(author)
            if fields:
                params.append(book_id)
                cur.execute(f"UPDATE books SET {', '.join(fields)} WHERE id = %s", params)

            # Cập nhật ngưỡng chương tính phí nếu có
            if free_chapter_threshold is not None:
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
                free = 1 if ch_num <= free_chapter_threshold else 0
                # Đọc tên chương thật từ dòng đầu file (# Title)
                ch_title = f"Chương {ch_num}"
                try:
                    with open(file_path, encoding="utf-8") as fh:
                        first_line = fh.readline().strip()
                        if first_line.startswith("#"):
                            ch_title = first_line.lstrip("#").strip() or ch_title
                except OSError:
                    pass
                new_rows.append((book_id, ch_num, ch_title, file_path, free))

            if new_rows:
                cur.executemany(
                    "INSERT IGNORE INTO chapters (book_id, chapter_number, title, file_path, free) VALUES (%s,%s,%s,%s,%s)",
                    new_rows,
                )

        conn.commit()
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
