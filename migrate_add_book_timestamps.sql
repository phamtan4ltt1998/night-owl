-- Migration: add created_at / updated_at to books table
-- Safe to re-run: each ALTER is guarded by IF NOT EXISTS logic in application init_db().
-- Run manually on existing DBs, or let the app auto-apply on next startup via init_db().

ALTER TABLE books
    ADD COLUMN IF NOT EXISTS created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP  AFTER id,
    ADD COLUMN IF NOT EXISTS updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP AFTER created_at;

-- Indexes (skip if already exist — ADD INDEX IF NOT EXISTS requires MySQL 8.0.29+)
-- For MySQL < 8.0.29, run selectively if the indexes don't already exist.
ALTER TABLE books
    ADD INDEX IF NOT EXISTS idx_created_at       (created_at DESC),
    ADD INDEX IF NOT EXISTS idx_updated_at       (updated_at DESC),
    ADD INDEX IF NOT EXISTS idx_genre_created_at (genre, created_at DESC),
    ADD INDEX IF NOT EXISTS idx_genre_updated_at (genre, updated_at DESC);
