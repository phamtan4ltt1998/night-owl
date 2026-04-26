-- NightOwl MySQL Migration
-- Run once via docker-entrypoint-initdb.d

SET NAMES utf8mb4;
SET character_set_client = utf8mb4;

CREATE DATABASE IF NOT EXISTS nightowl CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE nightowl;

-- ── Books ──────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS books (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    slug         VARCHAR(255)  NOT NULL UNIQUE,
    title        VARCHAR(500)  NOT NULL,
    author       VARCHAR(255)  NOT NULL,
    genre        VARCHAR(100)  NOT NULL,
    chapter_count INT          NOT NULL DEFAULT 0,
    `reads`        VARCHAR(50)   NOT NULL DEFAULT '0',
    rating       FLOAT         NOT NULL DEFAULT 4.5,
    c1           VARCHAR(20)   NOT NULL DEFAULT '#6941C6',
    c2           VARCHAR(20)   NOT NULL DEFAULT '#9E77ED',
    emoji        VARCHAR(20)   NOT NULL DEFAULT '📖',
    description  TEXT          NOT NULL,
    tags         VARCHAR(255)  NOT NULL DEFAULT '',
    words        VARCHAR(50)   NOT NULL DEFAULT '0',
    updated      VARCHAR(100)  NOT NULL DEFAULT '',
    source_url   VARCHAR(1000) NOT NULL DEFAULT '',
    cover_image  VARCHAR(1000) NOT NULL DEFAULT '',
    status       VARCHAR(50)   NOT NULL DEFAULT '',
    read_count   BIGINT        NOT NULL DEFAULT 0,
    INDEX idx_genre        (genre),
    INDEX idx_read_count   (read_count DESC),
    INDEX idx_rating       (rating DESC),
    FULLTEXT KEY ft_books_search (title, author, description, tags),
    FULLTEXT KEY ft_books_title (title)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Failed Crawl Requests ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS failed_crawl_requests (
    id                     INT AUTO_INCREMENT PRIMARY KEY,
    story_url              VARCHAR(1000) NOT NULL,
    story_limit            INT           DEFAULT NULL,
    start_story_from       INT           NOT NULL DEFAULT 1,
    free_chapter_threshold INT           NOT NULL DEFAULT 20,
    error_message          TEXT          NOT NULL,
    retry_count            INT           NOT NULL DEFAULT 0,
    last_tried_at          DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_at             DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved               TINYINT(1)    NOT NULL DEFAULT 0,
    INDEX idx_resolved (resolved),
    INDEX idx_retry    (resolved, retry_count)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Chapters ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS chapters (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    book_id        INT          NOT NULL,
    chapter_number INT          NOT NULL,
    title          VARCHAR(500) NOT NULL,
    file_path      TEXT         NOT NULL,
    free           TINYINT(1)   NOT NULL DEFAULT 1,
    view_count     BIGINT       NOT NULL DEFAULT 0,
    UNIQUE KEY uq_book_chapter (book_id, chapter_number),
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Notifications ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS notifications (
    id     INT AUTO_INCREMENT PRIMARY KEY,
    type   VARCHAR(50)  NOT NULL,
    icon   VARCHAR(20)  NOT NULL,
    title  VARCHAR(255) NOT NULL,
    body   TEXT         NOT NULL,
    time   VARCHAR(50)  NOT NULL,
    unread TINYINT(1)   NOT NULL DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Users ──────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS `users` (
    `id`           INT AUTO_INCREMENT PRIMARY KEY,
    `email`        VARCHAR(255) NOT NULL UNIQUE,
    `name`         VARCHAR(255) NOT NULL DEFAULT '',
    `bio`          TEXT NOT NULL,
    `linh_thach`   INT NOT NULL DEFAULT 50,
    `streak`       INT NOT NULL DEFAULT 0,
    `last_daily`   VARCHAR(20) NOT NULL DEFAULT '',
    `picture`      VARCHAR(500) DEFAULT NULL,
    `created_at`   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
-- ── Linh Thạch History ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS linh_thach_history (
    id      INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT          NOT NULL,
    type    VARCHAR(50)  NOT NULL,
    `desc`  TEXT         NOT NULL,
    amount  INT          NOT NULL,
    date    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    CONSTRAINT fk_lh_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Unlocked Chapters ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS unlocked_chapters (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    user_id        INT          NOT NULL,
    book_id        INT          NOT NULL,
    chapter_number INT          NOT NULL,
    unlocked_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_unlock    (user_id, book_id, chapter_number),
    INDEX idx_user_book     (user_id, book_id),
    CONSTRAINT fk_uc_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_uc_book FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Reading History ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS reading_history (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    user_id        INT          NOT NULL,
    book_id        INT          NOT NULL,
    chapter_number INT          NOT NULL DEFAULT 0,
    last_read      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_reading        (user_id, book_id),
    INDEX idx_user_last_read     (user_id, last_read),
    CONSTRAINT fk_rh_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_rh_book FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Seed Notifications ─────────────────────────────────────────────────────────

INSERT IGNORE INTO notifications (type, icon, title, body, time, unread) VALUES
    ('promo',  '🎉', 'Ưu đãi đặc biệt',  'Nâng cấp Premium giảm 50% hôm nay!',      '3 giờ trước', 1),
    ('system', '🔔', 'Chào mừng NightOwl!', 'Cảm ơn bạn đã tham gia. Khám phá kho truyện.', '2 ngày trước', 0);
