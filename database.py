"""SQLite schema and connection helpers."""
from __future__ import annotations

import sqlite3

from flask import current_app, g

SCHEMA = """
DROP TABLE IF EXISTS dms;
DROP TABLE IF EXISTS follows;
DROP TABLE IF EXISTS taps;
DROP TABLE IF EXISTS reels;
DROP TABLE IF EXISTS users;

CREATE TABLE users (
    id               INTEGER PRIMARY KEY,
    handle           TEXT UNIQUE NOT NULL,
    account_age_days INTEGER NOT NULL,
    activity_score   REAL NOT NULL,            -- 0..1: recent posting / liking / login activity
    is_creator       INTEGER NOT NULL DEFAULT 0,
    in_good_standing INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE reels (
    id             INTEGER PRIMARY KEY,
    creator_id     INTEGER NOT NULL REFERENCES users(id),
    caption        TEXT NOT NULL,
    product        TEXT NOT NULL,
    emoji          TEXT NOT NULL,
    link_url       TEXT NOT NULL,
    follow_gate    INTEGER NOT NULL DEFAULT 0, -- creator opted in to "Get link + Follow"
    button_enabled INTEGER NOT NULL DEFAULT 0, -- creator passed the eligibility check
    paused         INTEGER NOT NULL DEFAULT 0, -- auto-paused after reports
    views          INTEGER NOT NULL,
    likes          INTEGER NOT NULL,
    sim_kind       TEXT NOT NULL               -- SIMULATION LABEL ONLY. Never read by ranking code.
);

CREATE TABLE taps (
    id            INTEGER PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    reel_id       INTEGER NOT NULL REFERENCES reels(id),
    ts            TEXT NOT NULL,
    was_following INTEGER NOT NULL DEFAULT 0,
    sim_fake      INTEGER NOT NULL DEFAULT 0,  -- SIMULATION LABEL ONLY. Used to grade the filters.
    UNIQUE (user_id, reel_id)                  -- one tap per account per reel
);

CREATE TABLE follows (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    creator_id  INTEGER NOT NULL REFERENCES users(id),
    ts          TEXT NOT NULL,
    via_button  INTEGER NOT NULL DEFAULT 0,
    via_reel_id INTEGER REFERENCES reels(id),
    ended_ts    TEXT,                          -- set when the user unfollows
    UNIQUE (user_id, creator_id)
);

CREATE TABLE dms (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    reel_id     INTEGER NOT NULL REFERENCES reels(id),
    ts          TEXT NOT NULL,
    clicked_ts  TEXT,
    reported_ts TEXT,
    UNIQUE (user_id, reel_id)
);

CREATE INDEX idx_taps_reel ON taps(reel_id);
CREATE INDEX idx_dms_reel ON dms(reel_id);
CREATE INDEX idx_follows_reel ON follows(via_reel_id);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DB_PATH"])
    return g.db


def close_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()
