"""本地 SQLite 存储层（企划书 4.1：数据存储）。

表结构覆盖四条业务链路：
- contents  内容创作产物（草稿 / 待发布 / 已发布）
- posts     发布记录（含平台回链）
- metrics   指标快照，只追加，支撑增量更新与趋势图
- profile   账号画像六维配置
- hot_items 热榜缓存（5~10 分钟复用）
- publish_log 发布审计日志（风控留痕）
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

from .. import paths

log = logging.getLogger(__name__)

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS contents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    topic        TEXT    NOT NULL DEFAULT '',
    platform     TEXT    NOT NULL DEFAULT '',
    title        TEXT    NOT NULL DEFAULT '',
    titles_json  TEXT    NOT NULL DEFAULT '[]',
    body         TEXT    NOT NULL DEFAULT '',
    summary      TEXT    NOT NULL DEFAULT '',
    tags_json    TEXT    NOT NULL DEFAULT '[]',
    status       TEXT    NOT NULL DEFAULT 'draft',
    source       TEXT    NOT NULL DEFAULT 'manual',
    source_ref   TEXT    NOT NULL DEFAULT '',
    media_json   TEXT    NOT NULL DEFAULT '[]',
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contents_status ON contents(status);
CREATE INDEX IF NOT EXISTS idx_contents_updated ON contents(updated_at DESC);

CREATE TABLE IF NOT EXISTS posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id   INTEGER REFERENCES contents(id) ON DELETE SET NULL,
    platform     TEXT    NOT NULL,
    title        TEXT    NOT NULL DEFAULT '',
    body         TEXT    NOT NULL DEFAULT '',
    url          TEXT    NOT NULL DEFAULT '',
    status       TEXT    NOT NULL DEFAULT 'pending',
    error        TEXT    NOT NULL DEFAULT '',
    published_at TEXT,
    created_at   TEXT    NOT NULL,
    last_synced  TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_posts_platform ON posts(platform);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);

CREATE TABLE IF NOT EXISTS metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id     INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    captured_at TEXT    NOT NULL,
    views       INTEGER NOT NULL DEFAULT 0,
    likes       INTEGER NOT NULL DEFAULT 0,
    comments    INTEGER NOT NULL DEFAULT 0,
    collects    INTEGER NOT NULL DEFAULT 0,
    shares      INTEGER NOT NULL DEFAULT 0,
    raw_json    TEXT    NOT NULL DEFAULT '{}',
    UNIQUE(post_id, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_metrics_post ON metrics(post_id, captured_at DESC);

CREATE TABLE IF NOT EXISTS profile (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    positioning      TEXT NOT NULL DEFAULT '',
    style            TEXT NOT NULL DEFAULT '',
    audience         TEXT NOT NULL DEFAULT '',
    platforms_json   TEXT NOT NULL DEFAULT '[]',
    preferences      TEXT NOT NULL DEFAULT '',
    memory_json      TEXT NOT NULL DEFAULT '[]',
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hot_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    platform   TEXT NOT NULL,
    rank       INTEGER NOT NULL DEFAULT 0,
    title      TEXT NOT NULL,
    url        TEXT NOT NULL DEFAULT '',
    heat       TEXT NOT NULL DEFAULT '',
    keyword    TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hot_platform ON hot_items(platform, fetched_at DESC);

CREATE TABLE IF NOT EXISTS publish_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id    INTEGER,
    platform   TEXT NOT NULL,
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _json_list(value: Any) -> str:
    if value is None:
        return "[]"
    if isinstance(value, str):
        return value
    return json.dumps(list(value), ensure_ascii=False)


def _loads(value: Any, default: Any) -> Any:
    if not value:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class Database:
    """轻量 SQLite 封装，连接在单线程内复用，跨线程调用加锁。"""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or str(paths.db_path())
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # -- 连接管理 --------------------------------------------------------
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=15)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def init(self) -> None:
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()
            self._ensure_profile()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def _ensure_profile(self) -> None:
        row = self.query_one("SELECT id FROM profile WHERE id = 1")
        if not row:
            self.execute(
                """INSERT INTO profile (id, positioning, style, audience, platforms_json,
                       preferences, memory_json, updated_at)
                   VALUES (1, '', '', '', '[]', '', '[]', ?)""",
                (now(),),
            )

    # -- 内容 ------------------------------------------------------------
    def create_content(self, **fields: Any) -> int:
        stamp = now()
        payload = {
            "topic": fields.get("topic", ""),
            "platform": fields.get("platform", ""),
            "title": fields.get("title", ""),
            "titles_json": _json_list(fields.get("titles")),
            "body": fields.get("body", ""),
            "summary": fields.get("summary", ""),
            "tags_json": _json_list(fields.get("tags")),
            "status": fields.get("status", "draft"),
            "source": fields.get("source", "manual"),
            "source_ref": fields.get("source_ref", ""),
            "media_json": _json_list(fields.get("media")),
            "created_at": stamp,
            "updated_at": stamp,
        }
        cols = ", ".join(payload)
        marks = ", ".join("?" for _ in payload)
        cur = self.execute(
            f"INSERT INTO contents ({cols}) VALUES ({marks})", tuple(payload.values())
        )
        return int(cur.lastrowid or 0)

    def update_content(self, content_id: int, **fields: Any) -> None:
        mapping = {
            "titles": ("titles_json", _json_list),
            "tags": ("tags_json", _json_list),
            "media": ("media_json", _json_list),
        }
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key in mapping:
                col, conv = mapping[key]
                sets.append(f"{col} = ?")
                params.append(conv(value))
            else:
                sets.append(f"{key} = ?")
                params.append(value)
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(now())
        params.append(content_id)
        self.execute(f"UPDATE contents SET {', '.join(sets)} WHERE id = ?", tuple(params))

    def get_content(self, content_id: int) -> dict[str, Any] | None:
        row = self.query_one("SELECT * FROM contents WHERE id = ?", (content_id,))
        return _decode_content(row) if row else None

    def list_contents(
        self, *, status: str | None = None, platform: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM contents WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if platform:
            sql += " AND platform = ?"
            params.append(platform)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        return [_decode_content(r) for r in self.query(sql, tuple(params))]

    def delete_content(self, content_id: int) -> None:
        self.execute("DELETE FROM contents WHERE id = ?", (content_id,))

    # -- 发布 ------------------------------------------------------------
    def create_post(self, **fields: Any) -> int:
        payload = {
            "content_id": fields.get("content_id"),
            "platform": fields.get("platform", ""),
            "title": fields.get("title", ""),
            "body": fields.get("body", ""),
            "url": fields.get("url", ""),
            "status": fields.get("status", "pending"),
            "error": fields.get("error", ""),
            "published_at": fields.get("published_at"),
            "created_at": now(),
            "last_synced": "",
        }
        cols = ", ".join(payload)
        marks = ", ".join("?" for _ in payload)
        cur = self.execute(f"INSERT INTO posts ({cols}) VALUES ({marks})", tuple(payload.values()))
        return int(cur.lastrowid or 0)

    def update_post(self, post_id: int, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        params = list(fields.values()) + [post_id]
        self.execute(f"UPDATE posts SET {sets} WHERE id = ?", tuple(params))

    def get_post(self, post_id: int) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM posts WHERE id = ?", (post_id,))

    def list_posts(
        self, *, status: str | None = None, platform: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM posts WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if platform:
            sql += " AND platform = ?"
            params.append(platform)
        sql += " ORDER BY COALESCE(published_at, created_at) DESC LIMIT ?"
        params.append(limit)
        return self.query(sql, tuple(params))

    def posts_needing_sync(self, min_interval_minutes: int = 30, limit: int = 50) -> list[dict[str, Any]]:
        """需要增量拉取指标的已发布内容（企划书 4.3：只拉新数据）。"""
        threshold = (datetime.now() - timedelta(minutes=min_interval_minutes)).strftime("%Y-%m-%d %H:%M:%S")
        return self.query(
            """SELECT * FROM posts
               WHERE status = 'published' AND (last_synced = '' OR last_synced < ?)
               ORDER BY COALESCE(published_at, created_at) DESC LIMIT ?""",
            (threshold, limit),
        )

    # -- 指标 ------------------------------------------------------------
    def add_metric(self, post_id: int, data: dict[str, Any], captured_at: str | None = None) -> None:
        stamp = captured_at or now()
        with self._lock:
            self.conn.execute(
                """INSERT INTO metrics (post_id, captured_at, views, likes, comments, collects, shares, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(post_id, captured_at) DO UPDATE SET
                       views=excluded.views, likes=excluded.likes, comments=excluded.comments,
                       collects=excluded.collects, shares=excluded.shares, raw_json=excluded.raw_json""",
                (
                    post_id,
                    stamp,
                    int(data.get("views") or 0),
                    int(data.get("likes") or 0),
                    int(data.get("comments") or 0),
                    int(data.get("collects") or 0),
                    int(data.get("shares") or 0),
                    json.dumps(data.get("raw") or {}, ensure_ascii=False),
                ),
            )
            self.conn.commit()

    def latest_metric(self, post_id: int) -> dict[str, Any] | None:
        return self.query_one(
            "SELECT * FROM metrics WHERE post_id = ? ORDER BY captured_at DESC LIMIT 1", (post_id,)
        )

    def metric_series(self, post_id: int, limit: int = 60) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT * FROM metrics WHERE post_id = ? ORDER BY captured_at ASC LIMIT ?", (post_id, limit)
        )
        return rows

    def metric_overview(self, days: int = 30) -> list[dict[str, Any]]:
        """每个已发布内容的最近一条指标，用于列表/合计展示。"""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        return self.query(
            """SELECT p.id AS post_id, p.platform, p.title, p.url, p.published_at,
                      m.views, m.likes, m.comments, m.collects, m.shares, m.captured_at
               FROM posts p
               LEFT JOIN metrics m ON m.id = (
                   SELECT id FROM metrics WHERE post_id = p.id ORDER BY captured_at DESC LIMIT 1
               )
               WHERE p.status = 'published' AND COALESCE(p.published_at, p.created_at) >= ?
               ORDER BY COALESCE(p.published_at, p.created_at) DESC""",
            (since,),
        )

    # -- 画像 ------------------------------------------------------------
    def get_profile(self) -> dict[str, Any]:
        self._ensure_profile()
        row = self.query_one("SELECT * FROM profile WHERE id = 1") or {}
        row["platforms"] = _loads(row.pop("platforms_json", "[]"), [])
        row["memory"] = _loads(row.pop("memory_json", "[]"), [])
        return row

    def save_profile(self, **fields: Any) -> None:
        self._ensure_profile()
        mapping = {"platforms": "platforms_json", "memory": "memory_json"}
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            col = mapping.get(key, key)
            if key in mapping:
                value = _json_list(value)
            sets.append(f"{col} = ?")
            params.append(value)
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(now())
        self.execute(f"UPDATE profile SET {', '.join(sets)} WHERE id = 1", tuple(params))

    def append_memory(self, entry: str, keep: int = 40) -> None:
        """把一条经验写入画像记忆，供下次创作参考。"""
        profile = self.get_profile()
        memory = list(profile.get("memory") or [])
        memory.append({"at": now(), "text": entry})
        memory = memory[-keep:]
        self.save_profile(memory=memory)

    # -- 热榜 ------------------------------------------------------------
    def save_hot_items(self, platform: str, items: Iterable[dict[str, Any]]) -> int:
        stamp = now()
        count = 0
        with self._lock:
            self.conn.execute("DELETE FROM hot_items WHERE platform = ?", (platform,))
            for idx, item in enumerate(items):
                self.conn.execute(
                    """INSERT INTO hot_items (platform, rank, title, url, heat, keyword, fetched_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        platform,
                        int(item.get("rank") or idx + 1),
                        str(item.get("title") or "").strip(),
                        str(item.get("url") or ""),
                        str(item.get("heat") or ""),
                        str(item.get("keyword") or ""),
                        stamp,
                    ),
                )
                count += 1
            self.conn.commit()
        return count

    def load_hot_items(
        self, platform: str, max_age_seconds: int = 600
    ) -> tuple[list[dict[str, Any]], str]:
        """读取缓存；返回 (items, fetched_at)。超过 max_age 时 items 为空。"""
        rows = self.query(
            "SELECT * FROM hot_items WHERE platform = ? ORDER BY rank ASC", (platform,)
        )
        if not rows:
            return [], ""
        fetched_at = rows[0]["fetched_at"]
        try:
            ts = datetime.strptime(fetched_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return [], ""
        if datetime.now() - ts > timedelta(seconds=max_age_seconds):
            return [], fetched_at
        return rows, fetched_at

    # -- KV --------------------------------------------------------------
    def set_kv(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def get_kv(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM kv WHERE key = ?", (key,))
        if not row:
            return default
        return _loads(row["value"], default)

    # -- 日志 ------------------------------------------------------------
    def log_action(self, platform: str, action: str, detail: str = "", post_id: int | None = None) -> None:
        self.execute(
            "INSERT INTO publish_log (post_id, platform, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (post_id, platform, action, detail, now()),
        )

    def recent_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM publish_log ORDER BY id DESC LIMIT ?", (limit,))


def _decode_content(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["titles"] = _loads(row.pop("titles_json", "[]"), [])
    row["tags"] = _loads(row.pop("tags_json", "[]"), [])
    row["media"] = _loads(row.pop("media_json", "[]"), [])
    return row


db = Database()
