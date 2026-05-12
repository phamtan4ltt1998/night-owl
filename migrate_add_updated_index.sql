-- Run once on existing DBs to add updated sort indexes (already in init.sql for new installs)
ALTER TABLE books
    ADD INDEX idx_updated       (updated DESC),
    ADD INDEX idx_genre_updated (genre, updated DESC);
