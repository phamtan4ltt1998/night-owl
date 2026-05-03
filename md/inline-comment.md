# Feature Plan: Inline Comments (Wattpad-style)

## 1. Overview
Tính năng cho phép người đọc tương tác trực tiếp trên từng đoạn văn (paragraph) của chương truyện. Giúp tăng tỉ lệ giữ chân người dùng (retention) và tạo ra môi trường thảo luận sôi nổi ngay tại nội dung.

## 2. Technical Logic: Paragraph Mapping
Để gán bình luận vào đúng vị trí, chúng ta cần một cơ chế định danh đoạn văn bền vững.

- **Cơ chế đề xuất:** Mỗi đoạn văn trong một chương sẽ được gán một `paragraph_id`.
- **Cách thực hiện:** - Nếu nội dung truyện là tĩnh (HTML chuẩn): Sử dụng số thứ tự đoạn văn `(index)`. 
    - Nếu nội dung có thể thay đổi (Author edit): Sử dụng `hash` của nội dung đoạn văn (cần xử lý trường hợp các đoạn văn trùng nội dung).
    - **Lựa chọn tối ưu:** Backend khi parse nội dung truyện sẽ tự động bao bọc mỗi đoạn văn trong thẻ `<p data-p-id="unique_hash_or_index">`.

## 3. Data Model (Database Schema)

### Table: `inline_comments`
| Field | Type | Description |
| :--- | :--- | :--- |
| `id` | UUID / BigInt | Primary Key |
| `chapter_id` | Foreign Key | Liên kết với chương truyện |
| `paragraph_id` | String/Int | ID của đoạn văn trong chương đó |
| `user_id` | Foreign Key | Người bình luận |
| `content` | Text | Nội dung bình luận |
| `parent_id` | Foreign Key | Hỗ trợ reply bình luận (threaded) |
| `created_at` | Timestamp | Thời gian tạo |

## 4. API Design

### 4.1. Lấy số lượng bình luận theo đoạn
- **Endpoint:** `GET /api/chapters/{chapter_id}/comment-counts`
- **Response:** `{"p1": 5, "p2": 0, "p5": 12}` (Chỉ trả về những đoạn có bình luận để tối ưu dung lượng).

### 4.2. Lấy chi tiết bình luận của một đoạn
- **Endpoint:** `GET /api/chapters/{chapter_id}/paragraphs/{paragraph_id}/comments`
- **Params:** `page`, `limit` (Phân trang).

### 4.3. Đăng bình luận mới
- **Endpoint:** `POST /api/comments/inline`
- **Body:** `{ "chapter_id": 1, "paragraph_id": "p5", "content": "..." }`

## 5. Frontend Implementation (React/Vite Flow)

### Step 1: Render nội dung
- Render nội dung chương từ HTML/Markdown. 
- Sử dụng CSS để hiển thị một icon nhỏ hoặc số lượng bên cạnh mỗi thẻ `<p>` khi hover hoặc click.

### Step 2: Interaction State
- `activeParagraph`: Lưu ID của đoạn văn đang được chọn.
- `showSidebar`: Boolean, điều khiển việc hiện panel bình luận bên phải (hoặc popup trên mobile).

### Step 3: Optimization
- Sử dụng **Intersection Observer** để chỉ fetch số lượng bình luận khi người dùng cuộn đến đoạn đó (nếu chương truyện quá dài).

## 6. UI/UX Suggestions
- **Desktop:** Hiển thị bong bóng chat nhỏ ở lề phải (gutter). Click vào sẽ mở một Side Panel.
- **Mobile:** Khi click vào đoạn văn, highlight đoạn đó và hiện một Bottom Sheet để nhập liệu và xem thảo luận.

## 7. Scalability Considerations
- **Caching:** Cache số lượng bình luận theo `chapter_id` vào Redis để giảm tải cho DB chính.
- **Real-time:** Sử dụng WebSockets (hoặc Supabase Realtime/Firebase) để cập nhật số lượng bình luận ngay lập tức khi có người vừa post.