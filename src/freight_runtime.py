from __future__ import annotations

import csv
import email.utils
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.request
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote, urlparse


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
APP_VERSION = "1.4.2"
REJECTED_COLUMNS = [
    "记录时间", "群号", "发布人", "消息时间", "原因", "消息文本", "媒体来源"
]
DEFAULT_DATA_LIFECYCLE = {
    "enabled": True,
    "maintenance_interval_minutes": 60,
    "processed_message_retention_days": 365,
    "rejected_message_retention_days": 180,
    "dead_letter_retention_days": 30,
    # 正式运价关系到长期趋势统计，0 表示永久保留。
    "freight_record_retention_days": 0,
    "backup_retention_days": 30,
}


def normalize_excluded_origins(
    value: object, origin_names: set[str], default_origin: str, group_id: str,
) -> list[str]:
    """Validate per-group exclusions against canonical origins, without global parser state."""
    if not isinstance(value, list):
        raise ValueError(f"群 {group_id} 不统计的始发地必须是标准始发地列表。")
    result = []
    for item in value:
        if not isinstance(item, str) or item.strip() not in origin_names:
            raise ValueError(f"群 {group_id} 不统计的始发地不存在，请使用标准始发地名称。")
        origin = item.strip()
        if origin == default_origin:
            raise ValueError(f"群 {group_id} 不能排除自己的默认始发地“{origin}”。")
        if origin not in result:
            result.append(origin)
    return result


def normalize_data_lifecycle_config(
    value: object,
    backup_retention_days: object = 30,
) -> dict:
    """规范化生命周期配置；记录类保留天数为0时表示永久保留。"""
    if value is None:
        raw = {}
    elif isinstance(value, dict):
        raw = value
    else:
        raise ValueError("数据生命周期配置必须是对象。")

    unknown = sorted(set(raw) - set(DEFAULT_DATA_LIFECYCLE))
    if unknown:
        raise ValueError(f"数据生命周期配置包含未知字段：{', '.join(unknown)}")

    result = dict(DEFAULT_DATA_LIFECYCLE)
    result["backup_retention_days"] = backup_retention_days
    result.update(raw)
    enabled = result.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("数据生命周期启用状态必须是布尔值。")
    result["enabled"] = enabled

    fields = {
        "maintenance_interval_minutes": ("维护间隔", 5, 1440),
        "processed_message_retention_days": ("消息去重记录保留天数", 0, 36500),
        "rejected_message_retention_days": ("不合格记录保留天数", 0, 36500),
        "dead_letter_retention_days": ("死信保留天数", 0, 36500),
        "freight_record_retention_days": ("正式运价保留天数", 0, 36500),
        "backup_retention_days": ("每日备份保留天数", 1, 3650),
    }
    for key, (label, minimum, maximum) in fields.items():
        raw_value = result.get(key)
        if isinstance(raw_value, bool):
            raise ValueError(f"{label}必须是整数。")
        try:
            number = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label}必须是整数。") from exc
        if isinstance(raw_value, float) and not raw_value.is_integer():
            raise ValueError(f"{label}必须是整数。")
        if isinstance(raw_value, str) and str(number) != raw_value.strip():
            raise ValueError(f"{label}必须是整数。")
        if not minimum <= number <= maximum:
            if minimum == 0:
                raise ValueError(f"{label}必须在0到{maximum}之间，0表示永久保留。")
            raise ValueError(f"{label}必须在{minimum}到{maximum}之间。")
        result[key] = number
    return result


def normalize_group_data_lifecycle_configs(
    value: object,
    default_policy: dict,
    group_ids: object,
) -> dict[str, dict]:
    """规范化群专属策略；映射中不存在的群由调用方继承默认模板。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("群专属生命周期配置必须是以群号为键的对象。")
    allowed_group_ids = {str(group_id).strip() for group_id in group_ids}
    result = {}
    for raw_group_id, raw_policy in value.items():
        group_id = str(raw_group_id).strip()
        if not group_id.isdigit() or len(group_id) > 20:
            raise ValueError(f"群专属生命周期中的群号不正确：{group_id}")
        if group_id not in allowed_group_ids:
            raise ValueError(f"群 {group_id} 已配置专属生命周期，但不在QQ群列表中。")
        if group_id in result:
            raise ValueError(f"群 {group_id} 的专属生命周期配置重复。")
        if not isinstance(raw_policy, dict):
            raise ValueError(f"群 {group_id} 的专属生命周期配置必须是对象。")
        merged = dict(default_policy)
        merged.update(raw_policy)
        result[group_id] = normalize_data_lifecycle_config(
            merged,
            default_policy.get("backup_retention_days", 30),
        )
    return result


class ConfigurationConflictError(ValueError):
    """提交基于旧配置版本，不能覆盖较新的配置。"""


class ConfigurationBusyError(RuntimeError):
    """配置正在保存或重载，调用方应稍后重试。"""


def validate_websocket_url(value: object) -> str:
    """严格校验 OneBot WebSocket 地址，避免保存后重载才失败。"""
    ws_url = str(value or "").strip()
    if len(ws_url) > 300:
        raise ValueError("OneBot地址过长。")
    parsed = urlparse(ws_url)
    if parsed.scheme.lower() not in {"ws", "wss"}:
        raise ValueError("OneBot地址必须使用 ws:// 或 wss://。")
    if not parsed.hostname:
        raise ValueError("OneBot地址必须包含主机名或IP地址。")
    if parsed.username or parsed.password:
        raise ValueError("OneBot地址不能在URL中包含用户名或密码。")
    if parsed.fragment:
        raise ValueError("OneBot地址不能包含片段标识。")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("OneBot地址端口不正确。") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("OneBot地址端口必须在1到65535之间。")
    return ws_url


def validate_dashboard_host(value: object) -> str:
    """管理页当前只允许绑定本机回环地址。"""
    host = str(value or "127.0.0.1").strip().lower()
    if host == "localhost":
        return host
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("管理页面地址必须是localhost或回环IP。") from exc
    if not address.is_loopback or address.version != 4:
        raise ValueError("管理页面只允许绑定本机IPv4回环地址。")
    return host


def validate_tcp_port(value: object, label: str = "端口") -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是整数。") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{label}必须在1到65535之间。")
    return port


def configure_logging(log_dir: str, name: str = "freight") -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "物流运价系统.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


class NetworkClock:
    """优先校时Windows；无权限时用联网Date头维护程序内部时间偏差。"""

    def __init__(self) -> None:
        self.offset_seconds = 0.0
        self.method = "system_clock"
        self.synchronized = False
        self.message = "尚未执行联网校时"
        self.checked_at = None

    def now(self) -> datetime:
        return datetime.now().astimezone() + timedelta(seconds=self.offset_seconds)

    def today(self) -> date:
        return self.now().date()

    def snapshot(self) -> dict:
        return {
            "synchronized": self.synchronized,
            "method": self.method,
            "offset_seconds": round(self.offset_seconds, 3),
            "message": self.message,
            "checked_at": self.checked_at,
            "trusted_now": self.now().isoformat(timespec="seconds"),
        }

    def sync(self, urls: list[str] | None = None) -> dict:
        self.checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
        if os.name == "nt":
            try:
                result = subprocess.run(
                    ["w32tm", "/resync", "/force"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                    creationflags=CREATE_NO_WINDOW,
                )
                if result.returncode == 0:
                    self.offset_seconds = 0.0
                    self.method = "windows_w32time"
                    self.synchronized = True
                    self.message = "Windows时间服务重新同步成功"
                    return self.snapshot()
            except (OSError, subprocess.SubprocessError):
                pass

        candidates = urls or [
            "https://www.microsoft.com",
            "https://www.qq.com",
            "https://www.baidu.com",
        ]
        errors = []
        for url in candidates:
            try:
                request = urllib.request.Request(
                    url,
                    method="HEAD",
                    headers={"User-Agent": "FreightStatisticsClock/1.0"},
                )
                started = time.monotonic()
                with urllib.request.urlopen(request, timeout=8) as response:
                    date_header = response.headers.get("Date")
                elapsed = time.monotonic() - started
                if not date_header:
                    raise ValueError("响应没有Date头")
                server_utc = email.utils.parsedate_to_datetime(date_header)
                if server_utc.tzinfo is None:
                    server_utc = server_utc.replace(tzinfo=timezone.utc)
                midpoint_local_utc = datetime.now(timezone.utc) - timedelta(seconds=elapsed / 2)
                self.offset_seconds = (server_utc - midpoint_local_utc).total_seconds()
                self.method = f"http_date:{url}"
                self.synchronized = True
                self.message = "Windows校时无权限或失败，已使用联网时间偏差保护程序日期"
                return self.snapshot()
            except Exception as exc:
                errors.append(f"{url}: {exc}")

        self.offset_seconds = 0.0
        self.method = "system_clock_unsynchronized"
        self.synchronized = False
        self.message = "联网校时失败: " + " | ".join(errors)
        return self.snapshot()


class FreightDatabase:
    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(path)
        self._queue_lock = threading.RLock()
        self._queue_connection: sqlite3.Connection | None = None
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.initialize()

    def connect(self, check_same_thread: bool = True) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=10,
            check_same_thread=check_same_thread,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    @contextmanager
    def session(self):
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.session() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS freight_records (
                    dedup_key TEXT PRIMARY KEY,
                    effective_date TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    first_message_key TEXT,
                    source TEXT NOT NULL DEFAULT 'qq',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_freight_effective_date
                    ON freight_records(effective_date);

                CREATE TABLE IF NOT EXISTS processed_messages (
                    message_key TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS rejected_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rejection_key TEXT UNIQUE NOT NULL,
                    message_key TEXT,
                    group_id TEXT,
                    sender TEXT,
                    message_time TEXT,
                    reason TEXT NOT NULL,
                    message_text TEXT,
                    media_source TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS deleted_data (
                    kind TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    row_json TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    deleted_at TEXT NOT NULL,
                    PRIMARY KEY(kind, record_id)
                );
                CREATE TRIGGER IF NOT EXISTS prevent_deleted_freight_replay
                BEFORE INSERT ON freight_records
                WHEN EXISTS(SELECT 1 FROM deleted_data WHERE kind='freight' AND record_id=NEW.dedup_key)
                BEGIN SELECT RAISE(IGNORE); END;

                CREATE TABLE IF NOT EXISTS inbound_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT UNIQUE NOT NULL,
                    event_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    received_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_inbound_events_ready
                    ON inbound_events(status, next_attempt_at, id);
                """
            )

    def _persistent_queue_connection(self) -> sqlite3.Connection:
        with self._queue_lock:
            if self._queue_connection is None:
                self._queue_connection = self.connect(check_same_thread=False)
            return self._queue_connection

    def close_inbound_event_queue(self) -> None:
        with self._queue_lock:
            if self._queue_connection is None:
                return
            self._queue_connection.close()
            self._queue_connection = None

    def enqueue_inbound_event(self, event_key: str, event: dict) -> bool:
        payload = json.dumps(event, ensure_ascii=False, sort_keys=True)
        with self._queue_lock:
            connection = self._persistent_queue_connection()
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO inbound_events(
                    event_key, event_json, status, received_at
                ) VALUES (?, ?, 'pending', ?)
                """,
                (
                    str(event_key),
                    payload,
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                ),
            )
            connection.commit()
        return cursor.rowcount == 1

    def reset_processing_inbound_events(self) -> int:
        with self._queue_lock:
            connection = self._persistent_queue_connection()
            cursor = connection.execute(
                """
                UPDATE inbound_events
                SET status = 'pending', next_attempt_at = 0
                WHERE status = 'processing'
                """
            )
            connection.commit()
        return max(0, int(cursor.rowcount))

    def claim_next_inbound_event(self) -> dict | None:
        with self._queue_lock:
            connection = self._persistent_queue_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT id, event_json, attempts, received_at
                    FROM inbound_events
                    WHERE status = 'pending' AND next_attempt_at <= ?
                    ORDER BY id
                    LIMIT 1
                    """,
                    (time.time(),),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                connection.execute(
                    "UPDATE inbound_events SET status = 'processing' WHERE id = ?",
                    (row["id"],),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        try:
            event = json.loads(row["event_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            self.fail_inbound_event(int(row["id"]), f"事件JSON损坏: {exc}", max_attempts=1)
            return None
        return {
            "id": int(row["id"]),
            "event": event,
            "attempts": int(row["attempts"]),
            "received_at": str(row["received_at"]),
        }

    def complete_inbound_event(self, event_id: int) -> None:
        with self._queue_lock:
            connection = self._persistent_queue_connection()
            connection.execute("DELETE FROM inbound_events WHERE id = ?", (int(event_id),))
            connection.commit()

    def fail_inbound_event(
        self,
        event_id: int,
        error: str,
        max_attempts: int = 5,
    ) -> str:
        with self._queue_lock:
            connection = self._persistent_queue_connection()
            row = connection.execute(
                "SELECT attempts FROM inbound_events WHERE id = ?",
                (int(event_id),),
            ).fetchone()
            if row is None:
                return "missing"
            attempts = int(row["attempts"]) + 1
            if attempts >= max(1, int(max_attempts)):
                state = "dead_letter"
                next_attempt_at = 0.0
            else:
                state = "pending"
                next_attempt_at = time.time() + min(60.0, float(2 ** attempts))
            connection.execute(
                """
                UPDATE inbound_events
                SET status = ?, attempts = ?, next_attempt_at = ?, last_error = ?
                WHERE id = ?
                """,
                (state, attempts, next_attempt_at, str(error)[:1000], int(event_id)),
            )
            connection.commit()
        return state

    def count_pending_inbound_events(self) -> int:
        with self._queue_lock:
            connection = self._persistent_queue_connection()
            return int(connection.execute(
                "SELECT COUNT(*) FROM inbound_events WHERE status IN ('pending', 'processing')"
            ).fetchone()[0])

    def count_dead_letter_inbound_events(self) -> int:
        with self._queue_lock:
            connection = self._persistent_queue_connection()
            return int(connection.execute(
                "SELECT COUNT(*) FROM inbound_events WHERE status = 'dead_letter'"
            ).fetchone()[0])

    def has_processed_message(self, message_key: str) -> bool:
        with self.session() as connection:
            row = connection.execute(
                "SELECT 1 FROM processed_messages WHERE message_key = ?",
                (message_key,),
            ).fetchone()
        return row is not None

    def mark_processed_message(self, message_key: str) -> None:
        with self.session() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO processed_messages(message_key, processed_at) VALUES (?, ?)",
                (message_key, datetime.now().astimezone().isoformat(timespec="seconds")),
            )

    def insert_records(
        self,
        records: list[tuple[str, dict]],
        message_key: str = "",
        source: str = "qq",
    ) -> tuple[int, int]:
        inserted = 0
        duplicates = 0
        now_text = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.session() as connection:
            for dedup_key, record in records:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO freight_records(
                        dedup_key, effective_date, record_json,
                        first_message_key, source, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dedup_key,
                        str(record.get("日期", "")),
                        json.dumps(record, ensure_ascii=False, sort_keys=True),
                        message_key,
                        source,
                        now_text,
                    ),
                )
                if cursor.rowcount == 1:
                    inserted += 1
                else:
                    duplicates += 1
        return inserted, duplicates

    def fetch_records(self) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute(
                "SELECT record_json FROM freight_records ORDER BY effective_date, created_at"
            ).fetchall()
        return [json.loads(row["record_json"]) for row in rows]

    def fetch_recent_records(self, limit: int = 100) -> list[dict]:
        safe_limit = max(1, min(int(limit), 500))
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT record_json, created_at, source
                FROM freight_records
                ORDER BY created_at DESC, effective_date DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            {
                "record": json.loads(row["record_json"]),
                "created_at": row["created_at"],
                "source": row["source"],
            }
            for row in rows
        ]

    def count_records(self) -> int:
        with self.session() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM freight_records").fetchone()[0])

    def existing_record_keys(self, keys: list[str]) -> set[str]:
        if not keys:
            return set()
        placeholders = ",".join("?" for _ in keys)
        with self.session() as connection:
            rows = connection.execute(
                f"SELECT dedup_key FROM freight_records WHERE dedup_key IN ({placeholders})",
                keys,
            ).fetchall()
        return {str(row["dedup_key"]) for row in rows}

    def add_rejection(
        self,
        rejection_key: str,
        message_key: str,
        group_id: str,
        sender: str,
        message_time: str,
        reason: str,
        message_text: str,
        media_source: str = "",
    ) -> bool:
        with self.session() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO rejected_messages(
                    rejection_key, message_key, group_id, sender, message_time,
                    reason, message_text, media_source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rejection_key,
                    message_key,
                    group_id,
                    sender,
                    message_time,
                    reason,
                    message_text,
                    media_source,
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                ),
            )
        return cursor.rowcount == 1

    def fetch_rejections(self, limit: int | None = None) -> list[dict]:
        query = (
            "SELECT created_at, group_id, sender, message_time, reason, "
            "message_text, media_source FROM rejected_messages ORDER BY id DESC"
        )
        parameters = ()
        if limit is not None:
            query += " LIMIT ?"
            parameters = (max(1, min(int(limit), 500)),)
        with self.session() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            {
                "记录时间": row["created_at"],
                "群号": row["group_id"],
                "发布人": row["sender"],
                "消息时间": row["message_time"],
                "原因": row["reason"],
                "消息文本": row["message_text"],
                "媒体来源": row["media_source"],
            }
            for row in rows
        ]

    def export_rejections_csv(self, output_path: str) -> None:
        rows = self.fetch_rejections()
        with open(output_path, "w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=REJECTED_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    def backup_to(self, output_path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with self.session() as source:
            destination = sqlite3.connect(output_path)
            try:
                source.backup(destination)
            finally:
                destination.close()

    def cleanup_lifecycle(
        self,
        *,
        reference_time: datetime | None = None,
        processed_message_retention_days: int = 365,
        rejected_message_retention_days: int = 180,
        dead_letter_retention_days: int = 30,
        freight_record_retention_days: int = 0,
    ) -> dict:
        """在短事务中清理过期数据；0表示永久保留，待处理队列不参与清理。"""
        current = reference_time or datetime.now().astimezone()
        if current.tzinfo is None:
            current = current.astimezone()
        policies = {
            "processed_messages": int(processed_message_retention_days),
            "rejected_messages": int(rejected_message_retention_days),
            "dead_letters": int(dead_letter_retention_days),
            "freight_records": int(freight_record_retention_days),
        }
        if any(days < 0 for days in policies.values()):
            raise ValueError("生命周期保留天数不能为负数。")

        deleted = {key: 0 for key in (
            "freight_records",
            "processed_messages",
            "rejected_messages",
            "dead_letters",
        )}
        with self.session() as connection:
            freight_days = policies["freight_records"]
            if freight_days > 0:
                cutoff_date = (current - timedelta(days=freight_days)).date().isoformat()
                cursor = connection.execute(
                    "DELETE FROM freight_records WHERE date(effective_date) < date(?)",
                    (cutoff_date,),
                )
                deleted["freight_records"] = max(0, int(cursor.rowcount))

            for table, result_key, timestamp_column in (
                ("processed_messages", "processed_messages", "processed_at"),
                ("rejected_messages", "rejected_messages", "created_at"),
            ):
                days = policies[result_key]
                if days <= 0:
                    continue
                cutoff = (current - timedelta(days=days)).isoformat(timespec="seconds")
                cursor = connection.execute(
                    f"DELETE FROM {table} WHERE julianday({timestamp_column}) < julianday(?)",
                    (cutoff,),
                )
                deleted[result_key] = max(0, int(cursor.rowcount))

            dead_days = policies["dead_letters"]
            if dead_days > 0:
                cutoff = (current - timedelta(days=dead_days)).isoformat(
                    timespec="seconds"
                )
                cursor = connection.execute(
                    """
                    DELETE FROM inbound_events
                    WHERE status = 'dead_letter'
                      AND julianday(received_at) < julianday(?)
                    """,
                    (cutoff,),
                )
                deleted["dead_letters"] = max(0, int(cursor.rowcount))

            remaining = {
                "freight_records": int(connection.execute(
                    "SELECT COUNT(*) FROM freight_records"
                ).fetchone()[0]),
                "processed_messages": int(connection.execute(
                    "SELECT COUNT(*) FROM processed_messages"
                ).fetchone()[0]),
                "rejected_messages": int(connection.execute(
                    "SELECT COUNT(*) FROM rejected_messages"
                ).fetchone()[0]),
                "dead_letters": int(connection.execute(
                    "SELECT COUNT(*) FROM inbound_events WHERE status = 'dead_letter'"
                ).fetchone()[0]),
                "pending_events": int(connection.execute(
                    """
                    SELECT COUNT(*) FROM inbound_events
                    WHERE status IN ('pending', 'processing')
                    """
                ).fetchone()[0]),
            }
            # 只优化查询计划并允许SQLite复用空闲页，不在在线任务中执行阻塞式VACUUM。
            connection.execute("PRAGMA optimize")

        return {
            "reference_time": current.isoformat(timespec="seconds"),
            "deleted": deleted,
            "deleted_total": sum(deleted.values()),
            "remaining": remaining,
            "database_bytes": os.path.getsize(self.path) if os.path.exists(self.path) else 0,
        }


def prune_daily_backups(
    output_dir: str,
    retention_days: int = 30,
    effective_date: date | None = None,
) -> int:
    """删除超出滚动保留期的标准每日备份并返回删除数量。"""
    backup_dir = os.path.join(output_dir, "备份")
    cutoff = (effective_date or date.today()) - timedelta(
        days=max(1, int(retention_days))
    )
    deleted = 0
    for path in Path(backup_dir).glob("*_物流运价备份.zip"):
        try:
            file_date = datetime.strptime(path.name[:10], "%Y-%m-%d").date()
            if file_date < cutoff:
                path.unlink()
                deleted += 1
        except (ValueError, OSError):
            continue
    return deleted


def create_daily_backup(
    output_dir: str,
    database_file: str,
    extra_files: list[str] | None = None,
    retention_days: int = 30,
    backup_date: date | None = None,
    prune_expired: bool = True,
) -> str:
    backup_dir = os.path.join(output_dir, "备份")
    os.makedirs(backup_dir, exist_ok=True)
    effective_date = backup_date or date.today()
    today_text = effective_date.isoformat()
    final_zip = os.path.join(backup_dir, f"{today_text}_物流运价备份.zip")
    temp_zip = final_zip + ".tmp"

    with tempfile.TemporaryDirectory(prefix="freight-backup-") as temp_dir:
        database_snapshot = os.path.join(temp_dir, "运价数据.db")
        if os.path.exists(database_file):
            FreightDatabase(database_file).backup_to(database_snapshot)
        with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            if os.path.exists(database_snapshot):
                archive.write(database_snapshot, "运价数据.db")
            for path in extra_files or []:
                if os.path.isfile(path):
                    archive.write(path, os.path.basename(path))
        os.replace(temp_zip, final_zip)

    if prune_expired:
        prune_daily_backups(output_dir, retention_days, effective_date)
    return final_zip


class WindowsOcr:
    def __init__(self, script_path: str, enabled: bool = True, timeout: int = 30) -> None:
        self.script_path = script_path
        self.enabled = enabled and os.path.exists(script_path)
        self.timeout = timeout

    def recognize(self, source: str) -> tuple[str, str]:
        if not self.enabled:
            return "", "Windows OCR未启用或脚本不存在"

        temp_path = None
        image_path = source
        try:
            if source.startswith("base64://"):
                import base64

                payload = base64.b64decode(source[len("base64://"):], validate=False)
                handle = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                handle.write(payload)
                handle.close()
                temp_path = handle.name
                image_path = temp_path
            elif source.lower().startswith(("http://", "https://")):
                request = urllib.request.Request(
                    source,
                    headers={"User-Agent": "FreightStatisticsOCR/1.0"},
                )
                with urllib.request.urlopen(request, timeout=15) as response:
                    payload = response.read(15 * 1024 * 1024 + 1)
                if len(payload) > 15 * 1024 * 1024:
                    return "", "图片超过15MB限制"
                handle = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                handle.write(payload)
                handle.close()
                temp_path = handle.name
                image_path = temp_path
            elif source.startswith("file:///"):
                image_path = urllib.request.url2pathname(source[8:])

            if not os.path.isfile(image_path):
                return "", f"图片文件不存在: {image_path}"

            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    self.script_path,
                    "-ImagePath",
                    image_path,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                creationflags=CREATE_NO_WINDOW,
            )
            text = result.stdout.strip()
            text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)
            if result.returncode != 0:
                return "", result.stderr.strip() or f"OCR退出码 {result.returncode}"
            return text, ""
        except Exception as exc:
            return "", str(exc)
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass


class RuntimeStatus:
    def __init__(self, output_root: str) -> None:
        self.output_root = output_root
        self.status_file = os.path.join(output_root, "系统状态.json")
        self._lock = threading.Lock()
        self._persist_interval_seconds = 0.25
        self._last_persist_monotonic = 0.0
        self._persist_timer = None
        self._closed = False
        self._data = {
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "connection": "starting",
            "groups": {},
            "errors": [],
        }
        os.makedirs(output_root, exist_ok=True)
        self.persist()

    def update(self, **values) -> None:
        with self._lock:
            if self._closed:
                return
            self._data.update(values)
            self._data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            self._persist_locked()

    def update_group(self, group_id: str, **values) -> None:
        with self._lock:
            if self._closed:
                return
            group = self._data.setdefault("groups", {}).setdefault(str(group_id), {})
            group.update(values)
            self._data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            self._persist_locked()

    def increment_group(self, group_id: str, **increments) -> None:
        with self._lock:
            if self._closed:
                return
            group = self._data.setdefault("groups", {}).setdefault(str(group_id), {})
            for key, amount in increments.items():
                try:
                    current = int(group.get(key, 0) or 0)
                    delta = int(amount or 0)
                except (TypeError, ValueError):
                    continue
                group[key] = max(0, current + delta)
            self._data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            self._persist_locked()

    def add_error(self, message: str) -> None:
        with self._lock:
            if self._closed:
                return
            errors = self._data.setdefault("errors", [])
            errors.append({
                "time": datetime.now().astimezone().isoformat(timespec="seconds"),
                "message": str(message),
            })
            self._data["errors"] = errors[-50:]
            self._data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            self._persist_locked()

    def clear_errors(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._data["errors"] = []
            self._data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            self._persist_locked()

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data, ensure_ascii=False))

    def persist(self) -> None:
        with self._lock:
            self._persist_locked(force=True)

    def close(self, persist: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            if persist:
                self._persist_locked(force=True)
            elif self._persist_timer is not None:
                self._persist_timer.cancel()
                self._persist_timer = None
            self._closed = True

    def _flush_deferred(self) -> None:
        with self._lock:
            self._persist_timer = None
            if self._closed:
                return
            self._persist_locked(force=True)

    def _persist_locked(self, force: bool = False) -> None:
        if self._closed:
            return
        if force and self._persist_timer is not None:
            self._persist_timer.cancel()
            self._persist_timer = None
        elapsed = time.monotonic() - self._last_persist_monotonic
        if not force and elapsed < self._persist_interval_seconds:
            if self._persist_timer is None:
                delay = self._persist_interval_seconds - elapsed
                self._persist_timer = threading.Timer(delay, self._flush_deferred)
                self._persist_timer.daemon = True
                self._persist_timer.start()
            return
        temp_file = self.status_file + ".tmp"
        try:
            with open(temp_file, "w", encoding="utf-8") as stream:
                json.dump(self._data, stream, ensure_ascii=False, indent=2)
            os.replace(temp_file, self.status_file)
            self._last_persist_monotonic = time.monotonic()
        except OSError:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except OSError:
                pass


class FreightConfigurationManager:
    """为本机管理页面提供安全的群与线路配置读写。"""

    def __init__(
        self,
        live_config_path: str,
        rules_config_path: str,
        validate_callback: Callable[[], None] | None = None,
        restart_callback: Callable[[], None] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.live_config_path = os.path.abspath(live_config_path)
        self.rules_config_path = os.path.abspath(rules_config_path)
        self.validate_callback = validate_callback
        self.restart_callback = restart_callback
        self.logger = logger
        self._lock = threading.Lock()
        self._operation_lock = threading.Lock()
        self._restart_requested = threading.Event()
        self.transaction_path = os.path.join(
            os.path.dirname(self.live_config_path),
            ".freight_config_transaction.json",
        )
        self._recover_incomplete_transaction()

    @staticmethod
    def _read_json(path: str) -> dict:
        with open(path, "r", encoding="utf-8") as stream:
            value = json.load(stream)
        if not isinstance(value, dict):
            raise ValueError(f"配置文件必须是JSON对象: {path}")
        return value

    @staticmethod
    def _atomic_write_json(path: str, value: dict) -> None:
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        temp_path = os.path.join(
            directory,
            f".{os.path.basename(path)}.{os.getpid()}.{threading.get_ident()}.tmp",
        )
        try:
            with open(temp_path, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
            FreightConfigurationManager._fsync_directory(directory)
        finally:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass

    @staticmethod
    def _fsync_directory(directory: str) -> None:
        """尽力把目录元数据刷盘；Windows不支持目录句柄时安全跳过。"""
        flags = getattr(os, "O_RDONLY", 0)
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        try:
            descriptor = os.open(directory, flags)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    @classmethod
    def _remove_file_durable(cls, path: str) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            return
        cls._fsync_directory(os.path.dirname(os.path.abspath(path)))

    @staticmethod
    def _configuration_version(live: dict, rules: dict) -> str:
        live_generation = str(live.get("_config_generation", "")).strip()
        rules_generation = str(rules.get("_config_generation", "")).strip()
        if live_generation and live_generation == rules_generation:
            return live_generation
        canonical = json.dumps(
            {"live": live, "rules": rules},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _recover_incomplete_transaction(self) -> None:
        if not os.path.exists(self.transaction_path):
            return
        try:
            transaction = self._read_json(self.transaction_path)
            old_live = transaction["old_live"]
            old_rules = transaction["old_rules"]
            new_generation = str(transaction.get("new_generation", ""))
            current_live = self._read_json(self.live_config_path)
            current_rules = self._read_json(self.rules_config_path)
            committed = bool(new_generation) and (
                current_live.get("_config_generation") == new_generation
                and current_rules.get("_config_generation") == new_generation
            )
            if not committed:
                self._atomic_write_json(self.rules_config_path, old_rules)
                self._atomic_write_json(self.live_config_path, old_live)
                if self.logger:
                    self.logger.warning("检测到未完成配置事务，已自动恢复旧配置。")
            elif self.logger:
                self.logger.info("检测到已完成但未清理的配置事务，保留新配置。")
            self._remove_file_durable(self.transaction_path)
        except Exception as exc:
            raise RuntimeError(f"配置事务恢复失败：{exc}") from exc

    @staticmethod
    def _reject_unknown_fields(value: dict, allowed: set[str], label: str) -> None:
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"{label}包含未知字段：{', '.join(unknown)}")

    @staticmethod
    def _split_aliases(value) -> list[str]:
        if isinstance(value, list):
            raw_values = value
        else:
            raw_values = re.split(r"[,，、;；\n\r]+", str(value or ""))
        result = []
        for raw in raw_values:
            alias = str(raw).strip()
            if alias and alias not in result:
                result.append(alias)
        return result

    @classmethod
    def _normalize_locations(cls, rows, label: str) -> dict[str, list[str]]:
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"至少需要保留一个{label}。")
        result = {}
        alias_owners = {}
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise ValueError(f"{label}第{index}行格式不正确。")
            cls._reject_unknown_fields(row, {"name", "aliases"}, f"{label}第{index}行")
            name = str(row.get("name", "")).strip()
            if not name:
                raise ValueError(f"{label}第{index}行缺少标准名称。")
            if len(name) > 40:
                raise ValueError(f"{label}“{name[:20]}...”名称过长。")
            if name in result:
                raise ValueError(f"{label}标准名称重复：{name}")
            aliases = cls._split_aliases(row.get("aliases", []))
            aliases = [name] + [alias for alias in aliases if alias != name]
            for alias in aliases:
                if len(alias) > 60:
                    raise ValueError(f"{label}“{name}”中的别名过长。")
                owner = alias_owners.get(alias)
                if owner and owner != name:
                    raise ValueError(
                        f"{label}别名“{alias}”同时属于“{owner}”和“{name}”。"
                    )
                alias_owners[alias] = name
            result[name] = aliases
        return result

    @classmethod
    def _normalize_cargo_rules(
        cls,
        rows,
        default_subcategory,
        default_category,
        scopes,
        origin_names: set[str],
        destination_names: set[str],
    ) -> tuple[list[dict], str, str, list[dict]]:
        if not isinstance(rows, list) or not rows:
            raise ValueError("至少需要保留一种货物小类。")
        if len(rows) > 200:
            raise ValueError("货物小类不能超过200种。")

        normalized_default_category = str(default_category or "").strip() or "板材"
        if len(normalized_default_category) > 40:
            raise ValueError("默认货物大类名称过长。")

        normalized_rows = []
        cargo_names = set()
        category_names = set()
        alias_owners = {}
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise ValueError(f"货物小类第{index}行格式不正确。")
            cls._reject_unknown_fields(
                row,
                {"name", "aliases", "category"},
                f"货物小类第{index}行",
            )
            name = str(row.get("name", "")).strip()
            category = str(row.get("category", "")).strip()
            effective_category = category or normalized_default_category
            if not name:
                raise ValueError(f"货物小类第{index}行缺少标准名称。")
            if len(name) > 40 or len(effective_category) > 40:
                raise ValueError(f"货物“{name[:20]}”的小类或大类名称过长。")
            if name in cargo_names:
                raise ValueError(f"货物小类标准名称重复：{name}")
            aliases = [name] + [
                alias
                for alias in cls._split_aliases(row.get("aliases", []))
                if alias != name
            ]
            if len(aliases) > 100:
                raise ValueError(f"货物“{name}”的识别别名不能超过100个。")
            for alias in aliases:
                if len(alias) > 60:
                    raise ValueError(f"货物“{name}”中的识别别名过长。")
                owner = alias_owners.get(alias)
                if owner and owner != name:
                    raise ValueError(
                        f"货物别名“{alias}”同时属于“{owner}”和“{name}”。"
                    )
                alias_owners[alias] = name
            cargo_names.add(name)
            category_names.add(effective_category)
            normalized_rows.append({
                "name": name,
                "aliases": aliases,
                "category": category,
            })

        normalized_default_subcategory = str(default_subcategory or "").strip()
        if not normalized_default_subcategory:
            normalized_default_subcategory = normalized_rows[0]["name"]
        if normalized_default_subcategory not in cargo_names:
            raise ValueError("默认货物小类必须引用现有货物小类。")

        if scopes is None:
            scopes = []
        if not isinstance(scopes, list):
            raise ValueError("货物线路筛选规则格式不正确。")
        if len(scopes) > 500:
            raise ValueError("货物线路筛选规则不能超过500条。")
        normalized_scopes = []
        scope_keys = set()
        for index, scope in enumerate(scopes, start=1):
            if not isinstance(scope, dict):
                raise ValueError(f"货物线路规则第{index}行格式不正确。")
            cls._reject_unknown_fields(
                scope,
                {"level", "cargo", "routes"},
                f"货物线路规则第{index}行",
            )
            level = str(scope.get("level", "")).strip()
            cargo = str(scope.get("cargo", "")).strip()
            if level not in {"subcategory", "category"}:
                raise ValueError(f"货物线路规则第{index}行的筛选层级无效。")
            valid_cargos = cargo_names if level == "subcategory" else category_names
            if cargo not in valid_cargos:
                level_name = "小类" if level == "subcategory" else "大类"
                raise ValueError(
                    f"货物线路规则第{index}行引用了不存在的货物{level_name}“{cargo}”。"
                )
            scope_key = (level, cargo)
            if scope_key in scope_keys:
                raise ValueError(f"货物“{cargo}”存在重复的线路筛选规则。")
            scope_keys.add(scope_key)
            raw_routes = scope.get("routes")
            if not isinstance(raw_routes, list) or not raw_routes:
                raise ValueError(f"货物“{cargo}”至少需要选择一条允许线路。")
            maximum_routes = len(origin_names) * len(destination_names)
            if len(raw_routes) > maximum_routes:
                raise ValueError(
                    f"货物“{cargo}”的允许线路数量超过当前全部可用线路。"
                )
            normalized_routes = []
            seen_routes = set()
            for route_index, route in enumerate(raw_routes, start=1):
                if not isinstance(route, dict):
                    raise ValueError(
                        f"货物“{cargo}”的第{route_index}条线路格式不正确。"
                    )
                cls._reject_unknown_fields(
                    route,
                    {"origin", "destination"},
                    f"货物“{cargo}”的第{route_index}条线路",
                )
                origin = str(route.get("origin", "")).strip()
                destination = str(route.get("destination", "")).strip()
                if origin not in origin_names:
                    raise ValueError(f"货物“{cargo}”引用了不存在的始发地“{origin}”。")
                if destination not in destination_names:
                    raise ValueError(
                        f"货物“{cargo}”引用了不存在的目的地“{destination}”。"
                    )
                route_key = (origin, destination)
                if route_key not in seen_routes:
                    seen_routes.add(route_key)
                    normalized_routes.append({
                        "origin": origin,
                        "destination": destination,
                    })
            normalized_scopes.append({
                "level": level,
                "cargo": cargo,
                "routes": normalized_routes,
            })

        return (
            normalized_rows,
            normalized_default_subcategory,
            normalized_default_category,
            normalized_scopes,
        )

    @staticmethod
    def _normalize_groups(
        rows, origin_names: set[str], current_exclusions: dict | None = None,
    ) -> tuple[list[str], dict, dict, dict]:
        if not isinstance(rows, list) or not rows:
            raise ValueError("至少需要保留一个QQ群。")
        group_ids = []
        group_names = {}
        default_origins = {}
        exclusions = {}
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise ValueError(f"QQ群第{index}行格式不正确。")
            FreightConfigurationManager._reject_unknown_fields(
                row,
                {"id", "name", "default_origin", "excluded_origins"},
                f"QQ群第{index}行",
            )
            group_id = str(row.get("id", "")).strip()
            name = str(row.get("name", "")).strip()
            default_origin = str(row.get("default_origin", "")).strip()
            if not group_id.isdigit() or len(group_id) > 20:
                raise ValueError(f"QQ群第{index}行的群号必须是纯数字。")
            if group_id in group_names:
                raise ValueError(f"QQ群号重复：{group_id}")
            if not name:
                raise ValueError(f"QQ群 {group_id} 缺少群名称。")
            if len(name) > 80:
                raise ValueError(f"QQ群 {group_id} 的群名称过长。")
            if default_origin not in origin_names:
                raise ValueError(
                    f"QQ群 {group_id} 的默认始发地“{default_origin}”不存在。"
                )
            group_ids.append(group_id)
            group_names[group_id] = name
            default_origins[group_id] = default_origin
            # Omitted field from an older client keeps the guard; [] explicitly clears it.
            blocked = normalize_excluded_origins(
                row.get("excluded_origins", (current_exclusions or {}).get(group_id, [])),
                origin_names, default_origin, group_id,
            )
            if blocked:
                exclusions[group_id] = blocked
        return group_ids, group_names, default_origins, exclusions

    def snapshot(self) -> dict:
        with self._lock:
            live = self._read_json(self.live_config_path)
            rules = self._read_json(self.rules_config_path)
        group_ids = [str(value) for value in live.get("group_ids", [])]
        names = live.get("group_names", {})
        origins_by_group = live.get("group_default_origins", {})
        exclusions_by_group = live.get("group_excluded_origins", {})
        lifecycle = normalize_data_lifecycle_config(
            live.get("data_lifecycle"),
            live.get("backup_retention_days", 30),
        )
        group_lifecycles = normalize_group_data_lifecycle_configs(
            live.get("group_data_lifecycle"),
            lifecycle,
            group_ids,
        )
        default_cargo_category = str(
            rules.get("default_cargo_category", "板材")
        ).strip() or "板材"
        raw_cargo_types = rules.get("cargo_types")
        if not isinstance(raw_cargo_types, list) or not raw_cargo_types:
            raw_cargo_types = [
                {
                    "name": str(name),
                    "aliases": [str(name)],
                    "category": "",
                }
                for name in rules.get("board_types", [])
                if str(name).strip()
            ]
        cargo_types = []
        for item in raw_cargo_types:
            if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                continue
            name = str(item["name"]).strip()
            aliases = self._split_aliases(item.get("aliases", []))
            cargo_types.append({
                "name": name,
                "aliases": [name] + [alias for alias in aliases if alias != name],
                "category": str(item.get("category", "")).strip(),
            })
        default_cargo_subcategory = str(
            rules.get("default_cargo_subcategory", "")
        ).strip()
        cargo_names = {item["name"] for item in cargo_types}
        if default_cargo_subcategory not in cargo_names:
            default_cargo_subcategory = cargo_types[0]["name"] if cargo_types else ""
        cargo_route_scopes = rules.get("cargo_route_scopes", [])
        if not isinstance(cargo_route_scopes, list):
            cargo_route_scopes = []
        return {
            "version": self._configuration_version(live, rules),
            "connection": {
                "ws_url": str(live.get("ws_url", "ws://127.0.0.1:3001")),
                "access_token": "",
                "access_token_configured": bool(str(live.get("access_token", ""))),
                "napcat_launcher": str(live.get("napcat_launcher", "")),
            },
            "processing": {
                "reconnect_seconds": live.get("reconnect_seconds", 5),
                "heartbeat_seconds": live.get("heartbeat_seconds", 15),
                "excel_batch_seconds": live.get("excel_batch_seconds", 0.5),
                "excel_full_refresh_seconds": live.get(
                    "excel_full_refresh_seconds", 60
                ),
            },
            "lifecycle": lifecycle,
            "group_lifecycles": group_lifecycles,
            "groups": [
                {
                    "id": group_id,
                    "name": str(names.get(group_id, "")),
                    "default_origin": str(origins_by_group.get(group_id, "")),
                    "excluded_origins": list(exclusions_by_group.get(group_id, [])),
                }
                for group_id in group_ids
            ],
            "origins": [
                {"name": str(name), "aliases": list(aliases)}
                for name, aliases in rules.get("origin_groups", {}).items()
            ],
            "destinations": [
                {"name": str(name), "aliases": list(aliases)}
                for name, aliases in rules.get("destination_groups", {}).items()
            ],
            "default_origin": str(rules.get("default_origin", "")),
            "board_types": [item["name"] for item in cargo_types],
            "cargo_types": cargo_types,
            "default_cargo_subcategory": default_cargo_subcategory,
            "default_cargo_category": default_cargo_category,
            "cargo_route_scopes": cargo_route_scopes,
            "price_threshold": rules.get("price_threshold", 1000),
        }

    def apply(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("提交内容必须是JSON对象。")
        self._reject_unknown_fields(
            payload,
            {
                "version", "connection", "processing", "groups", "origins",
                "destinations", "default_origin", "board_types", "price_threshold",
                "lifecycle", "group_lifecycles",
                "cargo_types", "default_cargo_subcategory",
                "default_cargo_category", "cargo_route_scopes",
            },
            "配置",
        )
        connection = payload.get("connection", {})
        if not isinstance(connection, dict):
            raise ValueError("NapCat连接配置格式不正确。")
        self._reject_unknown_fields(
            connection,
            {
                "ws_url", "access_token", "access_token_configured",
                "clear_access_token", "napcat_launcher",
            },
            "NapCat连接配置",
        )
        ws_url = validate_websocket_url(connection.get("ws_url"))
        submitted_access_token = str(connection.get("access_token", "")).strip()
        clear_access_token = bool(connection.get("clear_access_token", False))
        napcat_launcher = str(connection.get("napcat_launcher", "")).strip()
        if len(submitted_access_token) > 500 or len(napcat_launcher) > 500:
            raise ValueError("NapCat连接配置内容过长。")
        processing = payload.get("processing", {})
        if not isinstance(processing, dict):
            raise ValueError("处理与重连参数格式不正确。")
        self._reject_unknown_fields(
            processing,
            {
                "reconnect_seconds", "heartbeat_seconds",
                "excel_batch_seconds", "excel_full_refresh_seconds",
            },
            "处理与重连参数",
        )
        try:
            reconnect_seconds = float(processing.get("reconnect_seconds", 5))
            heartbeat_seconds = float(processing.get("heartbeat_seconds", 15))
            excel_batch_seconds = float(processing.get("excel_batch_seconds", 0.5))
            excel_full_refresh_seconds = float(
                processing.get("excel_full_refresh_seconds", 60)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("处理与重连参数必须是数字。") from exc
        if not 1 <= reconnect_seconds <= 300:
            raise ValueError("断线重连间隔必须在1到300秒之间。")
        if not 5 <= heartbeat_seconds <= 300:
            raise ValueError("心跳检测间隔必须在5到300秒之间。")
        if not 0 <= excel_batch_seconds <= 10:
            raise ValueError("Excel批量追加等待必须在0到10秒之间。")
        if not 10 <= excel_full_refresh_seconds <= 3600:
            raise ValueError("Excel汇总刷新间隔必须在10到3600秒之间。")
        with self._lock:
            current_live_for_lifecycle = self._read_json(self.live_config_path)
            current_rules_for_cargo = self._read_json(self.rules_config_path)
        raw_lifecycle = payload.get(
            "lifecycle",
            current_live_for_lifecycle.get("data_lifecycle"),
        )
        lifecycle = normalize_data_lifecycle_config(
            raw_lifecycle,
            current_live_for_lifecycle.get("backup_retention_days", 30),
        )
        origins = self._normalize_locations(payload.get("origins"), "始发地")
        destinations = self._normalize_locations(payload.get("destinations"), "目的地")
        raw_default_origin = payload.get("default_origin")
        if raw_default_origin is None:
            with self._lock:
                current_rules = self._read_json(self.rules_config_path)
            raw_default_origin = current_rules.get("default_origin", "")
        default_origin = str(raw_default_origin).strip()
        if not default_origin and origins:
            default_origin = next(iter(origins))
        if default_origin not in origins:
            raise ValueError("手动文本模式默认始发地必须引用现有标准始发地。")
        group_ids, group_names, group_origins, group_exclusions = self._normalize_groups(
            payload.get("groups"), set(origins),
            current_live_for_lifecycle.get("group_excluded_origins", {}),
        )
        raw_group_lifecycles = payload.get("group_lifecycles")
        if raw_group_lifecycles is None:
            current_group_lifecycles = current_live_for_lifecycle.get(
                "group_data_lifecycle", {}
            )
            if isinstance(current_group_lifecycles, dict):
                raw_group_lifecycles = {
                    str(group_id): policy
                    for group_id, policy in current_group_lifecycles.items()
                    if str(group_id) in group_ids
                }
        group_lifecycles = normalize_group_data_lifecycle_configs(
            raw_group_lifecycles,
            lifecycle,
            group_ids,
        )
        submitted_cargo_types = payload.get("cargo_types")
        if submitted_cargo_types is None:
            if "board_types" in payload:
                legacy_board_types = self._split_aliases(payload.get("board_types", []))
                submitted_cargo_types = [
                    {
                        "name": name,
                        "aliases": [name],
                        "category": "",
                    }
                    for name in legacy_board_types
                ]
                submitted_scopes = []
            else:
                submitted_cargo_types = current_rules_for_cargo.get("cargo_types")
                submitted_scopes = current_rules_for_cargo.get(
                    "cargo_route_scopes", []
                )
        else:
            submitted_scopes = payload.get(
                "cargo_route_scopes",
                current_rules_for_cargo.get("cargo_route_scopes", []),
            )
        cargo_types, default_cargo_subcategory, default_cargo_category, cargo_scopes = (
            self._normalize_cargo_rules(
                submitted_cargo_types,
                payload.get(
                    "default_cargo_subcategory",
                    current_rules_for_cargo.get("default_cargo_subcategory", ""),
                ),
                payload.get(
                    "default_cargo_category",
                    current_rules_for_cargo.get("default_cargo_category", "板材"),
                ),
                submitted_scopes,
                set(origins),
                set(destinations),
            )
        )
        board_types = [item["name"] for item in cargo_types]
        try:
            price_threshold = float(payload.get("price_threshold"))
        except (TypeError, ValueError) as exc:
            raise ValueError("价格上限必须是数字。") from exc
        if not 0 < price_threshold <= 1_000_000_000:
            raise ValueError("价格上限必须大于0且不超过10亿。")

        with self._lock:
            old_live = self._read_json(self.live_config_path)
            old_rules = self._read_json(self.rules_config_path)
            submitted_version = str(payload.get("version", "")).strip()
            current_version = self._configuration_version(old_live, old_rules)
            if submitted_version and submitted_version != current_version:
                raise ConfigurationConflictError(
                    "配置已被其他操作修改，请重新加载页面后再保存。"
                )
            new_live = json.loads(json.dumps(old_live, ensure_ascii=False))
            new_rules = json.loads(json.dumps(old_rules, ensure_ascii=False))
            generation = secrets.token_hex(16)
            new_live["_config_generation"] = generation
            new_rules["_config_generation"] = generation
            new_live["group_ids"] = group_ids
            new_live["group_names"] = group_names
            new_live["group_default_origins"] = group_origins
            new_live["group_excluded_origins"] = group_exclusions
            new_live["ws_url"] = ws_url
            if clear_access_token:
                new_live["access_token"] = ""
            elif submitted_access_token:
                new_live["access_token"] = submitted_access_token
            else:
                new_live["access_token"] = str(old_live.get("access_token", ""))
            new_live["napcat_launcher"] = napcat_launcher
            new_live["reconnect_seconds"] = reconnect_seconds
            new_live["heartbeat_seconds"] = heartbeat_seconds
            new_live["excel_batch_seconds"] = excel_batch_seconds
            new_live["excel_full_refresh_seconds"] = excel_full_refresh_seconds
            new_live["data_lifecycle"] = lifecycle
            new_live["group_data_lifecycle"] = group_lifecycles
            # 保留旧字段，兼容现有备份调用和旧版本配置。
            new_live["backup_retention_days"] = lifecycle["backup_retention_days"]
            new_rules["origin_groups"] = origins
            new_rules["destination_groups"] = destinations
            new_rules["default_origin"] = default_origin
            new_rules["board_types"] = board_types
            new_rules["cargo_types"] = cargo_types
            new_rules["default_cargo_subcategory"] = default_cargo_subcategory
            new_rules["default_cargo_category"] = default_cargo_category
            new_rules["cargo_route_scopes"] = cargo_scopes
            new_rules["price_threshold"] = (
                int(price_threshold) if price_threshold.is_integer() else price_threshold
            )
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup_dir = os.path.join(
                os.path.dirname(self.live_config_path), "配置备份", stamp
            )
            os.makedirs(backup_dir, exist_ok=False)
            shutil.copy2(
                self.live_config_path,
                os.path.join(backup_dir, os.path.basename(self.live_config_path)),
            )
            shutil.copy2(
                self.rules_config_path,
                os.path.join(backup_dir, os.path.basename(self.rules_config_path)),
            )

            try:
                self._atomic_write_json(
                    self.transaction_path,
                    {
                        "state": "prepared",
                        "created_at": datetime.now().astimezone().isoformat(
                            timespec="seconds"
                        ),
                        "new_generation": generation,
                        "old_live": old_live,
                        "old_rules": old_rules,
                    },
                )
                self._atomic_write_json(self.rules_config_path, new_rules)
                self._atomic_write_json(self.live_config_path, new_live)
                if self.validate_callback:
                    self.validate_callback()
            except Exception:
                self._atomic_write_json(self.rules_config_path, old_rules)
                self._atomic_write_json(self.live_config_path, old_live)
                self._remove_file_durable(self.transaction_path)
                if self.validate_callback:
                    try:
                        self.validate_callback()
                    except Exception:
                        pass
                raise
            self._remove_file_durable(self.transaction_path)
            self._prune_configuration_backups()

        if self.logger:
            self.logger.info(
                "页面配置已保存: %s个群, %s个始发地, %s个目的地",
                len(group_ids), len(origins), len(destinations),
            )
        return {
            "ok": True,
            "message": "配置已保存，采集器正在排空队列并自动重新加载。",
            "backup_dir": backup_dir,
            "group_count": len(group_ids),
            "origin_count": len(origins),
            "destination_count": len(destinations),
            "group_lifecycle_override_count": len(group_lifecycles),
            "version": generation,
        }

    def _prune_configuration_backups(self, keep: int = 50) -> None:
        backup_root = os.path.join(
            os.path.dirname(self.live_config_path),
            "配置备份",
        )
        try:
            directories = sorted(
                (
                    entry for entry in os.scandir(backup_root)
                    if entry.is_dir(follow_symlinks=False)
                ),
                key=lambda entry: entry.name,
                reverse=True,
            )
        except OSError:
            return
        for entry in directories[max(1, int(keep)):]:
            try:
                shutil.rmtree(entry.path)
            except OSError:
                if self.logger:
                    self.logger.warning("旧配置备份清理失败: %s", entry.path)

    def apply_and_request_restart(self, payload: dict) -> dict:
        """把重载闸门和配置提交串成单飞操作，消除并发竞态。"""
        with self._operation_lock:
            if self.restart_pending():
                raise ConfigurationBusyError("采集器正在重新加载，请稍后再保存。")
            result = self.apply(payload)
            self.request_restart()
            return result

    def request_restart(self) -> None:
        if not self.restart_callback:
            return
        if self._restart_requested.is_set():
            return
        self._restart_requested.set()

        def delayed_restart():
            time.sleep(1.5)
            try:
                self.restart_callback()
            except Exception:
                self._restart_requested.clear()
                if self.logger:
                    self.logger.exception("应用页面配置时重启采集器失败")

        threading.Thread(
            target=delayed_restart,
            name="freight-config-restart",
            daemon=True,
        ).start()

    def restart_pending(self) -> bool:
        return self._restart_requested.is_set()


DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>物流运价系统</title><style>
*{box-sizing:border-box}body{font-family:"Microsoft YaHei",sans-serif;background:#f4f7fb;color:#1f2937;margin:0;padding:24px}
.wrap{max-width:1400px;margin:auto}.top{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
h1{margin:0}.tabs{display:flex;gap:8px}.tab,.primary,.secondary,.danger{border:0;border-radius:9px;padding:10px 16px;font:inherit;cursor:pointer}
.tab{background:#e8edf5}.tab.active,.primary{background:#2563eb;color:white}.secondary{background:#e8edf5}.danger{background:#fee2e2;color:#b91c1c;padding:7px 11px}
.card{background:white;border-radius:14px;padding:20px;margin:14px 0;box-shadow:0 8px 30px #1f293712}
.section-title{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}.section-title h2{margin:0;font-size:19px}
.ok{color:#07883e}.bad{color:#c62828}.muted{color:#6b7280}.hidden{display:none!important}
.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse}th,td{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:middle}
input,textarea,select{width:100%;border:1px solid #cbd5e1;border-radius:8px;padding:9px 10px;font:inherit;background:white}textarea{min-height:70px;resize:vertical}
.name{min-width:130px}.aliases{min-width:350px}.group-id{min-width:145px}.group-name{min-width:180px}.origin-select{min-width:140px}.group-excluded{min-width:200px}
#groupLifecycleRows .gl-group{min-width:150px;white-space:nowrap}#groupLifecycleRows input[type=number]{min-width:86px}
.form-grid{display:grid;grid-template-columns:2fr 1fr;gap:16px}.actions{display:flex;align-items:center;gap:12px;position:sticky;bottom:12px}.actions .primary{min-width:150px}
.notice{padding:12px 14px;border-radius:9px;background:#eff6ff;color:#1d4ed8}.notice.bad{background:#fef2f2;color:#b91c1c}
.badge{display:inline-block;border-radius:999px;padding:3px 9px;font-size:13px;white-space:nowrap}.badge.active{background:#dcfce7;color:#166534}.badge.pending{background:#fef3c7;color:#92400e}.badge.rejected{background:#fee2e2;color:#991b1b}.detail-text{max-width:360px;white-space:normal;word-break:break-word}
.cargo-name{min-width:130px}.cargo-aliases{min-width:260px}.cargo-category{min-width:150px}.scope-level{min-width:120px}.scope-cargo{min-width:150px}.route-summary{min-width:180px;color:#475569}.inline-actions{display:flex;gap:8px;flex-wrap:wrap}.compact{padding:7px 11px}
dialog{width:min(780px,calc(100vw - 32px));max-height:82vh;border:0;border-radius:14px;padding:0;box-shadow:0 24px 80px #0f172a44}dialog::backdrop{background:#0f172a66}.dialog-head,.dialog-actions{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px 20px}.dialog-head{border-bottom:1px solid #e5e7eb}.dialog-head h3{margin:0}.dialog-body{padding:16px 20px;max-height:55vh;overflow:auto}.dialog-actions{border-top:1px solid #e5e7eb;justify-content:flex-end}.route-check{width:auto}.route-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:9px}.route-option{display:flex;align-items:center;gap:9px;border:1px solid #e2e8f0;border-radius:9px;padding:10px}
@media(max-width:700px){body{padding:12px}.form-grid{grid-template-columns:1fr}.card{padding:14px}th,td{padding:7px}}
</style></head><body><div class="wrap">
<div class="top"><h1>物流运价系统</h1><div class="tabs"><button class="tab active" data-view="status">运行状态</button><button class="tab" data-view="config">群与线路管理</button></div></div>
<main id="statusView"><div id="statusApp" class="card">正在读取...</div><div id="recentApp" class="card">正在读取消息明细...</div></main>
<main id="configView" class="hidden">
<div class="notice">保存后系统会自动备份配置，停止接收新消息并排空群队列和Excel队列，再在同一进程内重新加载。正式运价默认永久保留；只有显式修改下方生命周期后才会清理。</div>
<section class="card"><div class="section-title"><h2>NapCat连接</h2></div><p class="muted">OneBot地址和Token必须与NapCat WebUI中的WebSocket服务器一致；已保存的Token不会回传到页面，留空表示保留。</p><div class="form-grid"><label>OneBot WebSocket地址<input id="wsUrl" placeholder="ws://127.0.0.1:3001"></label><label>Access Token<input id="accessToken" type="password" autocomplete="new-password" placeholder="留空保留当前Token"><span id="tokenState" class="muted"></span><span><input id="clearAccessToken" type="checkbox" style="width:auto"> 清除已保存Token</span></label></div><label>NapCat启动器路径<input id="napcatLauncher" placeholder="例如 C:/NapCatQQ/launcher-user.bat"></label></section>
<section class="card"><div class="section-title"><h2>实时处理与Excel</h2></div><p class="muted">消息先按群独立入队并实时入库；Excel短暂合并后追加，统计汇总按较低频率校准。</p><div class="form-grid"><label>断线重连间隔（秒）<input id="reconnectSeconds" type="number" min="1" max="300" step="1"></label><label>心跳检测间隔（秒）<input id="heartbeatSeconds" type="number" min="5" max="300" step="1"></label><label>Excel批量追加等待（秒）<input id="excelBatchSeconds" type="number" min="0" max="10" step="0.1"></label><label>Excel汇总刷新间隔（秒）<input id="excelFullRefreshSeconds" type="number" min="10" max="3600" step="10"></label></div></section>
<section class="card"><div class="section-title"><h2>默认数据生命周期模板</h2><label><input id="lifecycleEnabled" type="checkbox" style="width:auto"> 启用自动维护</label></div><p class="muted">所有未设置专属策略的现有群和以后新增群都自动继承本模板。正式运价、消息去重、不合格和死信填0表示永久保留；待处理和处理中的消息永远不会被清理。</p><div class="form-grid"><label>维护间隔（分钟）<input id="lifecycleIntervalMinutes" type="number" min="5" max="1440" step="1"></label><label>正式运价保留天数（0=永久）<input id="freightRetentionDays" type="number" min="0" max="36500" step="1"></label><label>消息去重记录保留天数（0=永久）<input id="processedRetentionDays" type="number" min="0" max="36500" step="1"></label><label>不合格记录保留天数（0=永久）<input id="rejectedRetentionDays" type="number" min="0" max="36500" step="1"></label><label>死信保留天数（0=永久）<input id="deadLetterRetentionDays" type="number" min="0" max="36500" step="1"></label><label>每日备份保留天数<input id="backupRetentionDays" type="number" min="1" max="3650" step="1"></label></div></section>
<section class="card"><div class="section-title"><h2>QQ群</h2><button id="addGroup" class="secondary">＋ 添加群</button></div><p class="muted">未写始发地时使用本群默认值。可填写“不统计的始发地”（标准名称，多个用逗号分隔；留空不额外限制）。别名先归为标准始发地再过滤，不会只因正文提到地名而拦截。仅影响保存后处理的新消息，不改历史报价。</p><div class="table-wrap"><table><thead><tr><th>群号</th><th>群名称</th><th>默认始发地</th><th>不统计的始发地</th><th></th></tr></thead><tbody id="groupRows"></tbody></table></div></section>
<section class="card"><div class="section-title"><h2>群专属生命周期（可选）</h2></div><p class="muted">默认关闭，表示继承上方模板。只有确实需要不同保留策略的群才开启专属配置；关闭后立即恢复继承。</p><div class="table-wrap"><table><thead><tr><th>群</th><th>专属</th><th>自动维护</th><th>间隔/分钟</th><th>正式/天</th><th>去重/天</th><th>不合格/天</th><th>死信/天</th><th>备份/天</th></tr></thead><tbody id="groupLifecycleRows"></tbody></table></div></section>
<section class="card"><div class="section-title"><h2>始发地范围</h2><button id="addOrigin" class="secondary">＋ 添加始发地</button></div><p class="muted">标准名称用于统计；别名可填写市、区、县、镇等写法，用逗号分隔。</p><div class="table-wrap"><table><thead><tr><th>标准始发地</th><th>识别别名</th><th></th></tr></thead><tbody id="originRows"></tbody></table></div><div class="form-grid"><label>手动文本模式默认始发地<select id="manualDefaultOrigin"></select></label></div></section>
<section class="card"><div class="section-title"><h2>目的地范围（线路）</h2><button id="addDestination" class="secondary">＋ 添加目的地</button></div><p class="muted">新增标准目的地后，它会与所有始发地自动形成可统计线路。</p><div class="table-wrap"><table><thead><tr><th>标准目的地</th><th>识别的城市/区县别名</th><th></th></tr></thead><tbody id="destinationRows"></tbody></table></div></section>
<section class="card"><div class="section-title"><h2>货物归类</h2><button id="addCargo" class="secondary">＋ 添加货物小类</button></div><p class="muted">标准小类用于明细和去重；识别别名按全局最长名称匹配。归属大类留空时继承默认大类，日/周/月/年统计按大类分别汇总。</p><div class="form-grid"><label>默认货物大类<input id="defaultCargoCategory" placeholder="例如：板材"></label><label>消息未写货物时默认小类<select id="defaultCargoSubcategory"></select></label></div><div class="table-wrap"><table><thead><tr><th>标准小类</th><th>识别别名</th><th>归属大类（留空继承默认）</th><th></th></tr></thead><tbody id="cargoRows"></tbody></table></div></section>
<section class="card"><div class="section-title"><h2>货物线路筛选（可选）</h2><button id="addCargoScope" class="secondary">＋ 添加筛选规则</button></div><p class="muted">不添加规则表示所有线路都统计。规则是允许线路白名单；小类规则优先于大类规则，且只影响所选货物，不影响其他货物和线路。未开放线路的消息会进入“不合格”并显示原因。</p><div class="table-wrap"><table><thead><tr><th>筛选层级</th><th>货物</th><th>允许线路</th><th></th></tr></thead><tbody id="cargoScopeRows"></tbody></table></div></section>
<section class="card"><div class="form-grid"><label>价格上限（超过后不进入均价）<input id="priceThreshold" type="number" min="0.001" step="0.001"></label></div></section>
<div class="card actions"><button id="saveConfig" class="primary">保存并自动应用</button><span id="saveMessage" class="muted">修改只在点击保存后生效。</span></div>
</main><dialog id="routeDialog"><div class="dialog-head"><h3>选择允许统计的线路</h3><button id="closeRouteDialog" class="secondary compact" type="button">关闭</button></div><div class="dialog-body"><div class="inline-actions"><button id="selectAllRoutes" class="secondary compact" type="button">全选</button><button id="clearAllRoutes" class="secondary compact" type="button">清空</button></div><p class="muted">只有勾选的线路会统计当前货物；未勾选线路的该货物消息会记为不合格。</p><div id="routeOptions" class="route-grid"></div></div><div class="dialog-actions"><button id="cancelRoutes" class="secondary" type="button">取消</button><button id="confirmRoutes" class="primary" type="button">确认线路</button></div></dialog></div>
<script>
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
let CSRF_TOKEN=__FREIGHT_CSRF_TOKEN__;
let CONFIG_VERSION='';
let NEXT_GROUP_ROW_KEY=0;
let ACTIVE_SCOPE_ROW=null;
function esc(v){return String(v??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]))}
function options(selected=''){const names=$$('#originRows .loc-name').map(x=>x.value.trim()).filter(Boolean);if(selected&&!names.includes(selected))names.push(selected);return names.map(x=>`<option ${x===selected?'selected':''}>${esc(x)}</option>`).join('')}
function groupRow(g={}){const tr=document.createElement('tr');tr.dataset.rowKey=String(++NEXT_GROUP_ROW_KEY);tr.innerHTML=`<td><input class="group-id" inputmode="numeric" value="${esc(g.id||'')}"></td><td><input class="group-name" value="${esc(g.name||'')}"></td><td><select class="origin-select">${options(g.default_origin||'')}</select></td><td><input class="group-excluded" aria-label="不统计的始发地" value="${esc((g.excluded_origins||[]).join('，'))}" placeholder="标准名称；留空不限制"></td><td><button class="danger remove">删除</button></td>`;tr.querySelector('.remove').onclick=()=>{tr.remove();syncGroupLifecycleRows()};tr.querySelector('.group-id').addEventListener('input',()=>updateGroupLifecycleLabel(tr));tr.querySelector('.group-name').addEventListener('input',()=>updateGroupLifecycleLabel(tr));return tr}
function defaultLifecycleFromForm(){return {enabled:$('#lifecycleEnabled').checked,maintenance_interval_minutes:$('#lifecycleIntervalMinutes').value,freight_record_retention_days:$('#freightRetentionDays').value,processed_message_retention_days:$('#processedRetentionDays').value,rejected_message_retention_days:$('#rejectedRetentionDays').value,dead_letter_retention_days:$('#deadLetterRetentionDays').value,backup_retention_days:$('#backupRetentionDays').value}}
function lifecyclePolicyFromRow(tr){return {enabled:tr.querySelector('.gl-enabled').checked,maintenance_interval_minutes:tr.querySelector('.gl-interval').value,freight_record_retention_days:tr.querySelector('.gl-freight').value,processed_message_retention_days:tr.querySelector('.gl-processed').value,rejected_message_retention_days:tr.querySelector('.gl-rejected').value,dead_letter_retention_days:tr.querySelector('.gl-dead').value,backup_retention_days:tr.querySelector('.gl-backup').value}}
function applyLifecyclePolicyToRow(tr,p){tr.querySelector('.gl-enabled').checked=p.enabled!==false;tr.querySelector('.gl-interval').value=p.maintenance_interval_minutes??60;tr.querySelector('.gl-freight').value=p.freight_record_retention_days??0;tr.querySelector('.gl-processed').value=p.processed_message_retention_days??365;tr.querySelector('.gl-rejected').value=p.rejected_message_retention_days??180;tr.querySelector('.gl-dead').value=p.dead_letter_retention_days??30;tr.querySelector('.gl-backup').value=p.backup_retention_days??30}
function setLifecycleRowMode(tr,useOverride){tr.querySelectorAll('.gl-policy').forEach(x=>x.disabled=!useOverride);if(!useOverride)applyLifecyclePolicyToRow(tr,defaultLifecycleFromForm())}
function groupLifecycleRow(groupTr,useOverride=false,policy={}){const tr=document.createElement('tr');tr.dataset.rowKey=groupTr.dataset.rowKey;tr.innerHTML=`<td class="gl-group"></td><td><input class="gl-use" type="checkbox" style="width:auto"></td><td><input class="gl-policy gl-enabled" type="checkbox" style="width:auto"></td><td><input class="gl-policy gl-interval" type="number" min="5" max="1440"></td><td><input class="gl-policy gl-freight" type="number" min="0" max="36500"></td><td><input class="gl-policy gl-processed" type="number" min="0" max="36500"></td><td><input class="gl-policy gl-rejected" type="number" min="0" max="36500"></td><td><input class="gl-policy gl-dead" type="number" min="0" max="36500"></td><td><input class="gl-policy gl-backup" type="number" min="1" max="3650"></td>`;tr.querySelector('.gl-use').checked=useOverride;applyLifecyclePolicyToRow(tr,useOverride?policy:defaultLifecycleFromForm());setLifecycleRowMode(tr,useOverride);tr.querySelector('.gl-use').onchange=()=>setLifecycleRowMode(tr,tr.querySelector('.gl-use').checked);updateGroupLifecycleLabel(groupTr,tr);return tr}
function updateGroupLifecycleLabel(groupTr,lifecycleTr){const target=lifecycleTr||$(`#groupLifecycleRows tr[data-row-key="${groupTr.dataset.rowKey}"]`);if(!target)return;const id=groupTr.querySelector('.group-id').value.trim()||'未填写群号';const name=groupTr.querySelector('.group-name').value.trim();target.querySelector('.gl-group').textContent=name?`${name}（${id}）`:id}
function syncGroupLifecycleRows(reset=false,configured={}){const saved={};if(!reset)$$('#groupLifecycleRows tr').forEach(tr=>saved[tr.dataset.rowKey]={use:tr.querySelector('.gl-use').checked,policy:lifecyclePolicyFromRow(tr)});const rows=$$('#groupRows tr').map(groupTr=>{const old=saved[groupTr.dataset.rowKey];const groupId=groupTr.querySelector('.group-id').value.trim();const configuredPolicy=configured[groupId];return groupLifecycleRow(groupTr,old?old.use:!!configuredPolicy,old?old.policy:(configuredPolicy||{}))});$('#groupLifecycleRows').replaceChildren(...rows)}
function refreshInheritedLifecycleRows(){$$('#groupLifecycleRows tr').forEach(tr=>{if(!tr.querySelector('.gl-use').checked)applyLifecyclePolicyToRow(tr,defaultLifecycleFromForm())})}
function locationRow(item={},kind){const tr=document.createElement('tr');tr.innerHTML=`<td><input class="loc-name name" value="${esc(item.name||'')}"></td><td><input class="loc-aliases aliases" value="${esc((item.aliases||[]).join('，'))}"></td><td><button class="danger remove">删除</button></td>`;tr.querySelector('.remove').onclick=()=>{tr.remove();if(kind==='origin')refreshOriginSelects()};if(kind==='origin')tr.querySelector('.loc-name').addEventListener('change',()=>refreshOriginSelects());return tr}
function refreshOriginSelects(manualSelected){const names=$$('#originRows .loc-name').map(x=>x.value.trim()).filter(Boolean);$$('.origin-select').forEach(sel=>{const old=sel.value;sel.innerHTML=names.map(x=>`<option ${x===old?'selected':''}>${esc(x)}</option>`).join('')});const manual=$('#manualDefaultOrigin');const oldManual=manualSelected??manual.value;manual.innerHTML=names.map(x=>`<option ${x===oldManual?'selected':''}>${esc(x)}</option>`).join('')}
function cargoRow(item={}){const tr=document.createElement('tr');tr.innerHTML=`<td><input class="cargo-name" value="${esc(item.name||'')}"></td><td><input class="cargo-aliases" value="${esc((item.aliases||[]).filter(x=>x!==(item.name||'')).join('，'))}" placeholder="多个别名用逗号分隔"></td><td><input class="cargo-category" value="${esc(item.category||'')}" placeholder="继承默认大类"></td><td><button class="danger remove">删除</button></td>`;tr.querySelector('.remove').onclick=()=>{tr.remove();syncCargoReferences()};tr.querySelector('.cargo-name').addEventListener('input',()=>syncCargoReferences());tr.querySelector('.cargo-category').addEventListener('input',()=>syncCargoReferences());return tr}
function collectCargoTypes(){return $$('#cargoRows tr').map(tr=>({name:tr.querySelector('.cargo-name').value.trim(),aliases:splitValues(tr.querySelector('.cargo-aliases').value),category:tr.querySelector('.cargo-category').value.trim()}))}
function cargoChoices(level){const rows=collectCargoTypes();if(level==='subcategory')return [...new Set(rows.map(x=>x.name).filter(Boolean))];const fallback=$('#defaultCargoCategory').value.trim()||'板材';return [...new Set(rows.map(x=>x.category||fallback).filter(Boolean))]}
function refreshDefaultCargoSelect(selected){const select=$('#defaultCargoSubcategory');const old=selected??select.value;const names=cargoChoices('subcategory');select.innerHTML=names.map(x=>`<option ${x===old?'selected':''}>${esc(x)}</option>`).join('')}
function refreshScopeCargoSelect(tr,selected){const select=tr.querySelector('.scope-cargo');const old=selected??select.value;const names=cargoChoices(tr.querySelector('.scope-level').value);select.innerHTML=names.map(x=>`<option ${x===old?'selected':''}>${esc(x)}</option>`).join('');if(old&&!names.includes(old)){const option=document.createElement('option');option.value=old;option.textContent=old+'（已不存在）';option.selected=true;select.append(option)}}
function syncCargoReferences(defaultSelected){refreshDefaultCargoSelect(defaultSelected);$$('#cargoScopeRows tr').forEach(tr=>refreshScopeCargoSelect(tr))}
function routeSummary(tr){const count=(tr._routes||[]).length;tr.querySelector('.route-summary').textContent=count?`${count} 条允许线路`:'尚未选择线路'}
function cargoScopeRow(rule={}){const tr=document.createElement('tr');tr._routes=Array.isArray(rule.routes)?rule.routes.map(x=>({origin:String(x.origin||''),destination:String(x.destination||'')})):[];tr.innerHTML=`<td><select class="scope-level"><option value="subcategory">货物小类</option><option value="category">货物大类</option></select></td><td><select class="scope-cargo"></select></td><td><div class="inline-actions"><span class="route-summary"></span><button class="secondary compact choose-routes" type="button">选择线路</button></div></td><td><button class="danger remove" type="button">删除</button></td>`;tr.querySelector('.scope-level').value=rule.level||'subcategory';refreshScopeCargoSelect(tr,rule.cargo||'');routeSummary(tr);tr.querySelector('.scope-level').onchange=()=>refreshScopeCargoSelect(tr,'');tr.querySelector('.choose-routes').onclick=()=>openRouteDialog(tr);tr.querySelector('.remove').onclick=()=>tr.remove();return tr}
function currentRoutes(){const origins=$$('#originRows .loc-name').map(x=>x.value.trim()).filter(Boolean);const destinations=$$('#destinationRows .loc-name').map(x=>x.value.trim()).filter(Boolean);return origins.flatMap(origin=>destinations.map(destination=>({origin,destination})))}
function openRouteDialog(tr){ACTIVE_SCOPE_ROW=tr;const selected=new Set((tr._routes||[]).map(x=>JSON.stringify([x.origin,x.destination])));const options=currentRoutes().map(route=>{const label=document.createElement('label');label.className='route-option';const input=document.createElement('input');input.type='checkbox';input.className='route-check';input.dataset.origin=route.origin;input.dataset.destination=route.destination;input.checked=selected.has(JSON.stringify([route.origin,route.destination]));label.append(input,document.createTextNode(`${route.origin} → ${route.destination}`));return label});$('#routeOptions').replaceChildren(...options);if(!options.length)$('#routeOptions').textContent='请先至少配置一个始发地和一个目的地。';$('#routeDialog').showModal()}
function closeRouteDialog(){$('#routeDialog').close();ACTIVE_SCOPE_ROW=null}
function collectCargoScopes(){return $$('#cargoScopeRows tr').map(tr=>({level:tr.querySelector('.scope-level').value,cargo:tr.querySelector('.scope-cargo').value,routes:(tr._routes||[]).map(x=>({origin:x.origin,destination:x.destination}))}))}
async function refreshStatus(){try{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error(r.status);const s=await r.json();const groups=Object.values(s.groups||{});const rows=groups.map(g=>`<tr><td>${esc(g.name)}</td><td>${esc(g.active_records??0)}</td><td>${esc(g.pending_records??0)}</td><td>${esc(g.rejected_records??0)}</td><td>${esc(g.queue_state||'idle')} / ${esc(g.queue_depth??0)}</td><td>${esc(g.excel_state||'-')} / ${esc(g.excel_update_mode||'-')} / ${esc(g.aggregate_state||'-')}</td><td>${g.lifecycle_policy_source==='group_override'?'群专属':'默认模板'} / ${esc(g.lifecycle_state||'-')} / ${esc(g.lifecycle_last_run_at||'-')} / 本次${esc(g.lifecycle_last_deleted_total??0)} / 累计${esc(g.lifecycle_deleted_total??0)}</td><td>${esc(g.last_event_at||g.last_message||'-')}</td><td>${esc(g.last_event_status||'-')}</td><td>${esc(g.last_event_reason||'-')}</td></tr>`).join('');const latest=s.last_event_at?`${esc(s.last_event_at)} / ${esc(s.last_event_status||'-')} / ${esc(s.last_event_type||'-')} / 群${esc(s.last_group_id||'-')}`:'-';$('#statusApp').innerHTML=`<p>WebSocket连接：<b class="${s.connection==='connected'?'ok':'bad'}">${esc(s.connection)}</b>　重连次数：${esc(s.websocket_reconnect_count??0)}</p><p>最近链路事件：${esc(s.websocket_last_event_at||'-')}　最近心跳：${esc(s.websocket_last_ping_at||'-')}</p><p>更新时间：${esc(s.updated_at)}</p><p>最近接收：${esc(s.last_received_at||'-')} / 群${esc(s.last_received_group_id||'-')}</p><p>最近处理：${latest}</p><p>最近说明：${esc(s.last_event_reason||'-')}</p><p>可信时间：${esc((s.clock||{}).trusted_now)}（${esc((s.clock||{}).method)}）</p><div class="table-wrap"><table><thead><tr><th>群</th><th>正式</th><th>待生效</th><th>不合格</th><th>群队列/积压</th><th>Excel/更新方式/汇总</th><th>生命周期来源/最近维护/清理数</th><th>最近处理</th><th>结果</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table></div><p class="muted">错误数：${(s.errors||[]).length}　链路错误：${esc(s.websocket_last_error||'-')}</p>`}catch(e){$('#statusApp').innerHTML='<span class="bad">状态读取失败：'+esc(e)+'</span>'}}
async function refreshRecent(){try{const r=await fetch('/api/recent?limit=80',{cache:'no-store'});if(!r.ok)throw Error(r.status);const x=await r.json();const rows=(x.items||[]).map(item=>{const cls=item.kind==='正式识别'?'active':item.kind==='待生效'?'pending':'rejected';return `<tr><td>${esc(item.group_name)}</td><td><span class="badge ${cls}">${esc(item.kind)}</span></td><td>${esc(item.received_at||'-')}</td><td>${esc(item.effective_date||'-')}</td><td>${esc(item.sender||'-')}</td><td>${esc(item.route||'-')}</td><td>${esc(item.cargo||'-')}</td><td>${esc(item.price||'-')}</td><td class="detail-text">${esc(item.detail||'-')}</td></tr>`}).join('');$('#recentApp').innerHTML=`<div class="section-title"><h2>最近消息处理明细</h2><span class="muted">自动刷新，最多80条</span></div><div class="table-wrap"><table><thead><tr><th>群</th><th>分类</th><th>接收/记录时间</th><th>生效日期</th><th>发布人</th><th>线路</th><th>货物</th><th>价格</th><th>原文/原因</th></tr></thead><tbody>${rows||'<tr><td colspan="9" class="muted">暂无数据</td></tr>'}</tbody></table></div>`}catch(e){$('#recentApp').innerHTML='<span class="bad">消息明细读取失败：'+esc(e)+'</span>'}}
async function refreshAll(){await Promise.all([refreshStatus(),refreshRecent()])}
async function loadConfig(){const r=await fetch('/api/config',{cache:'no-store'});if(!r.ok){const x=await r.json().catch(()=>({}));throw Error(x.error||'配置读取失败')}CSRF_TOKEN=r.headers.get('X-Freight-CSRF-Token')||CSRF_TOKEN;const c=await r.json();CONFIG_VERSION=c.version||'';const n=c.connection||{},p=c.processing||{},l=c.lifecycle||{};$('#wsUrl').value=n.ws_url||'ws://127.0.0.1:3001';$('#accessToken').value='';$('#clearAccessToken').checked=false;$('#tokenState').textContent=n.access_token_configured?'当前已设置Token':'当前未设置Token';$('#napcatLauncher').value=n.napcat_launcher||'';$('#reconnectSeconds').value=p.reconnect_seconds??5;$('#heartbeatSeconds').value=p.heartbeat_seconds??15;$('#excelBatchSeconds').value=p.excel_batch_seconds??0.5;$('#excelFullRefreshSeconds').value=p.excel_full_refresh_seconds??60;$('#lifecycleEnabled').checked=l.enabled!==false;$('#lifecycleIntervalMinutes').value=l.maintenance_interval_minutes??60;$('#freightRetentionDays').value=l.freight_record_retention_days??0;$('#processedRetentionDays').value=l.processed_message_retention_days??365;$('#rejectedRetentionDays').value=l.rejected_message_retention_days??180;$('#deadLetterRetentionDays').value=l.dead_letter_retention_days??30;$('#backupRetentionDays').value=l.backup_retention_days??30;$('#originRows').replaceChildren(...c.origins.map(x=>locationRow(x,'origin')));$('#destinationRows').replaceChildren(...c.destinations.map(x=>locationRow(x,'destination')));$('#groupRows').replaceChildren(...c.groups.map(groupRow));syncGroupLifecycleRows(true,c.group_lifecycles||{});$('#defaultCargoCategory').value=c.default_cargo_category||'板材';$('#cargoRows').replaceChildren(...(c.cargo_types||[]).map(cargoRow));$('#cargoScopeRows').replaceChildren(...(c.cargo_route_scopes||[]).map(cargoScopeRow));syncCargoReferences(c.default_cargo_subcategory||'');$('#priceThreshold').value=c.price_threshold;refreshOriginSelects(c.default_origin||'')}
function splitValues(v){return String(v||'').replaceAll(String.fromCharCode(10),'，').replaceAll(String.fromCharCode(13),'，').split(/[,，、;；]+/).map(x=>x.trim()).filter(Boolean)}
function collectLocations(selector){return $$(selector+' tr').map(tr=>({name:tr.querySelector('.loc-name').value.trim(),aliases:splitValues(tr.querySelector('.loc-aliases').value)}))}
function collectConfig(){const groups=$$('#groupRows tr').map(tr=>({id:tr.querySelector('.group-id').value.trim(),name:tr.querySelector('.group-name').value.trim(),default_origin:tr.querySelector('.origin-select').value,excluded_origins:splitValues(tr.querySelector('.group-excluded').value)}));const lifecycleRows=Object.fromEntries($$('#groupLifecycleRows tr').map(tr=>[tr.dataset.rowKey,tr]));const group_lifecycles={};$$('#groupRows tr').forEach(tr=>{const policyRow=lifecycleRows[tr.dataset.rowKey];const groupId=tr.querySelector('.group-id').value.trim();if(groupId&&policyRow?.querySelector('.gl-use').checked)group_lifecycles[groupId]=lifecyclePolicyFromRow(policyRow)});const cargo_types=collectCargoTypes();return {version:CONFIG_VERSION,connection:{ws_url:$('#wsUrl').value.trim(),access_token:$('#accessToken').value.trim(),clear_access_token:$('#clearAccessToken').checked,napcat_launcher:$('#napcatLauncher').value.trim()},processing:{reconnect_seconds:$('#reconnectSeconds').value,heartbeat_seconds:$('#heartbeatSeconds').value,excel_batch_seconds:$('#excelBatchSeconds').value,excel_full_refresh_seconds:$('#excelFullRefreshSeconds').value},lifecycle:defaultLifecycleFromForm(),group_lifecycles,groups,origins:collectLocations('#originRows'),destinations:collectLocations('#destinationRows'),default_origin:$('#manualDefaultOrigin').value,board_types:cargo_types.map(x=>x.name),cargo_types,default_cargo_subcategory:$('#defaultCargoSubcategory').value,default_cargo_category:$('#defaultCargoCategory').value.trim(),cargo_route_scopes:collectCargoScopes(),price_threshold:$('#priceThreshold').value}}
async function waitForRestart(){let attempts=0;const timer=setInterval(async()=>{attempts++;try{const r=await fetch('/api/config',{cache:'no-store'});if(r.ok){clearInterval(timer);await loadConfig();await refreshStatus();$('#saveConfig').disabled=false;$('#saveMessage').className='ok';$('#saveMessage').textContent='配置已应用，采集器已恢复运行。'}}catch(e){}if(attempts>30){clearInterval(timer);$('#saveConfig').disabled=false;$('#saveMessage').className='bad';$('#saveMessage').textContent='等待重启超时，请查看运行状态或启动器。'}},2000)}
async function saveConfig(){const button=$('#saveConfig');button.disabled=true;$('#saveMessage').className='muted';$('#saveMessage').textContent='正在校验并保存...';try{const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json','X-Freight-CSRF':CSRF_TOKEN},body:JSON.stringify(collectConfig())});const x=await r.json();if(!r.ok)throw Error(x.error||'保存失败');CONFIG_VERSION=x.version||CONFIG_VERSION;$('#saveMessage').className='ok';$('#saveMessage').textContent=x.message;setTimeout(waitForRestart,2000)}catch(e){button.disabled=false;$('#saveMessage').className='bad';$('#saveMessage').textContent=e.message}}
$$('.tab').forEach(b=>b.onclick=async()=>{$$('.tab').forEach(x=>x.classList.toggle('active',x===b));const config=b.dataset.view==='config';$('#statusView').classList.toggle('hidden',config);$('#configView').classList.toggle('hidden',!config);if(config)try{await loadConfig()}catch(e){$('#saveMessage').className='bad';$('#saveMessage').textContent=e.message}});
$('#addGroup').onclick=()=>{$('#groupRows').append(groupRow());syncGroupLifecycleRows()};$('#addOrigin').onclick=()=>{$('#originRows').append(locationRow({},'origin'));refreshOriginSelects()};$('#addDestination').onclick=()=>$('#destinationRows').append(locationRow({},'destination'));$('#addCargo').onclick=()=>{$('#cargoRows').append(cargoRow());syncCargoReferences()};$('#addCargoScope').onclick=()=>$('#cargoScopeRows').append(cargoScopeRow());$('#defaultCargoCategory').addEventListener('input',()=>syncCargoReferences());$('#closeRouteDialog').onclick=closeRouteDialog;$('#cancelRoutes').onclick=closeRouteDialog;$('#selectAllRoutes').onclick=()=>$$('#routeOptions .route-check').forEach(x=>x.checked=true);$('#clearAllRoutes').onclick=()=>$$('#routeOptions .route-check').forEach(x=>x.checked=false);$('#confirmRoutes').onclick=()=>{if(ACTIVE_SCOPE_ROW){ACTIVE_SCOPE_ROW._routes=$$('#routeOptions .route-check:checked').map(x=>({origin:x.dataset.origin,destination:x.dataset.destination}));routeSummary(ACTIVE_SCOPE_ROW)}closeRouteDialog()};$('#routeDialog').addEventListener('cancel',event=>{event.preventDefault();closeRouteDialog()});$('#saveConfig').onclick=saveConfig;
['lifecycleEnabled','lifecycleIntervalMinutes','freightRetentionDays','processedRetentionDays','rejectedRetentionDays','deadLetterRetentionDays','backupRetentionDays'].forEach(id=>$('#'+id).addEventListener(id==='lifecycleEnabled'?'change':'input',refreshInheritedLifecycleRows));
refreshAll();setInterval(refreshAll,2000);if(new URLSearchParams(location.search).get('view')==='config')document.querySelector('[data-view="config"]').click();
</script></body></html>"""


def start_status_dashboard(
    status: RuntimeStatus,
    host: str = "127.0.0.1",
    port: int = 8765,
    logger: logging.Logger | None = None,
    configuration: FreightConfigurationManager | None = None,
    recent_data_provider: Callable[[int], dict] | None = None,
    data_service=None,
) -> ThreadingHTTPServer | None:
    csrf_token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def send_security_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                "img-src 'self'; object-src 'none'; base-uri 'none'; "
                "frame-ancestors 'none'",
            )

        def send_json(self, status_code: int, value: dict) -> None:
            payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("X-Freight-CSRF-Token", csrf_token)
            self.send_security_headers()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def request_host_allowed(self) -> bool:
            actual_port = int(self.server.server_address[1])
            bound_host = str(self.server.server_address[0]).lower()
            allowed_hosts = {
                f"127.0.0.1:{actual_port}",
                f"localhost:{actual_port}",
                f"{bound_host}:{actual_port}",
            }
            return self.headers.get("Host", "").lower() in allowed_hosts

        def reject_bad_host(self) -> bool:
            if self.request_host_allowed():
                return False
            self.send_json(400, {"error": "请求Host不属于本机管理页面。"})
            return True

        def do_GET(self):
            if self.reject_bad_host():
                return
            parsed_url = urlparse(self.path)
            request_path = parsed_url.path
            if request_path.startswith("/api/data/"):
                if data_service is None:
                    self.send_json(503, {"error": "数据服务尚未启动。"})
                    return
                params = {key: values[-1] for key, values in parse_qs(parsed_url.query).items()}
                try:
                    if configuration and configuration.restart_pending():
                        self.send_json(409, {"error": "系统正在重新加载，请稍后刷新。"})
                        return
                    routes = {
                        "/api/data/options": lambda: data_service.options(),
                        "/api/data/records": lambda: data_service.query(params),
                        "/api/data/statistics": lambda: data_service.statistics(params),
                        "/api/data/files": lambda: data_service.files(params),
                        "/api/data/file-preview": lambda: data_service.preview_file(params),
                        "/api/data/archive-status": lambda: data_service.archive.status(),
                        "/api/data/archive-details": lambda: data_service.archive.details(params),
                        "/api/data/collection-status": lambda: data_service.collection_status(),
                        "/api/data/daily-status": lambda: data_service.daily.status(params),
                    }
                    if request_path in routes:
                        self.send_json(200, routes[request_path]())
                    elif request_path == "/api/data/file":
                        path = data_service.file_path(params)
                        mime = {".png": "image/png", ".csv": "text/csv; charset=utf-8",
                                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
                        with path.open("rb") as stream:
                            self.send_response(200)
                            self.send_header("Content-Type", mime[path.suffix.lower()])
                            disposition = "inline" if path.suffix.lower() == ".png" and params.get("download") != "1" else "attachment"
                            self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quote(path.name)}")
                            self.send_security_headers()
                            self.send_header("Content-Length", str(os.fstat(stream.fileno()).st_size))
                            self.end_headers()
                            shutil.copyfileobj(stream, self.wfile)
                    else:
                        self.send_json(404, {"error": "接口不存在。"})
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                except (OSError, sqlite3.OperationalError):
                    self.send_json(409, {"error": "数据或报表正在更新，请稍后重试。"})
                except Exception:
                    if logger:
                        logger.exception("数据查询失败")
                    self.send_json(500, {"error": "查询失败，请查看日志。"})
                return
            if request_path == "/api/health":
                current = status.snapshot()
                self.send_json(200, {
                    "ok": True,
                    "service": "freight-collector",
                    "version": APP_VERSION,
                    "connection": current.get("connection", "unknown"),
                    "configured": current.get("connection") != "setup_required",
                    "accepting_messages": current.get("connection") == "connected" and (data_service is None or data_service.collection_status()['enabled']),
                    "collection": data_service.collection_status() if data_service else None,
                    "updated_at": current.get("updated_at", ""),
                })
                return
            if request_path == "/api/status":
                self.send_json(200, status.snapshot())
                return
            if request_path == "/api/recent":
                if not recent_data_provider:
                    self.send_json(404, {"error": "消息明细未启用。"})
                    return
                try:
                    raw_limit = parse_qs(parsed_url.query).get("limit", ["80"])[0]
                    limit = int(raw_limit)
                    if not 1 <= limit <= 200:
                        raise ValueError
                    self.send_json(200, recent_data_provider(limit))
                except (TypeError, ValueError):
                    self.send_json(400, {"error": "limit必须是1到200之间的整数。"})
                except Exception as exc:
                    if logger:
                        logger.exception("消息明细读取失败")
                    self.send_json(500, {"error": "消息明细读取失败。"})
                return
            if request_path == "/api/config":
                if not configuration:
                    self.send_json(404, {"error": "配置管理未启用。"})
                    return
                if configuration.restart_pending():
                    self.send_json(503, {"error": "采集器正在排空队列并重新加载配置。"})
                    return
                try:
                    self.send_json(200, configuration.snapshot())
                except Exception:
                    if logger:
                        logger.exception("配置读取失败")
                    self.send_json(500, {"error": "配置读取失败。"})
                return
            if request_path != "/":
                self.send_json(404, {"error": "接口不存在。"})
                return
            from freight_data_ui import extend_dashboard
            from freight_archive_ui import extend_archive_dashboard
            from freight_daily_ui import extend_daily_dashboard
            html = extend_daily_dashboard(extend_archive_dashboard(extend_dashboard(DASHBOARD_HTML))).replace(
                "__FREIGHT_CSRF_TOKEN__",
                json.dumps(csrf_token),
            )
            payload = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("X-Freight-CSRF-Token", csrf_token)
            self.send_security_headers()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            if self.reject_bad_host():
                return
            request_path = self.path.split("?", 1)[0]
            data_action = request_path in {"/api/data/delete-preview", "/api/data/delete", '/api/data/archive-open', '/api/data/archive-preview', '/api/data/archive-close', '/api/data/archive-retry', '/api/data/collection-change', '/api/data/daily-generate'}
            if not ((request_path == "/api/config" and configuration) or (data_action and data_service)):
                self.send_json(404, {"error": "接口不存在。"})
                return
            if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
                self.send_json(415, {"error": "请求必须使用application/json。"})
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                content_length = 0
            if content_length <= 0 or content_length > 1_000_000:
                self.send_json(413, {"error": "配置内容为空或过大。"})
                return
            try:
                # Windows在服务端带着未读请求体直接关闭连接时可能发送RST，
                # 先有界读取再做CSRF判断，确保调用方稳定收到4xx响应。
                self.connection.settimeout(10)
                raw_body = self.rfile.read(content_length)
            except socket.timeout:
                self.send_json(408, {"error": "读取配置请求超时。"})
                return
            if configuration and configuration.restart_pending():
                self.send_json(409, {"error": "采集器正在重新加载，请稍后再保存。"})
                return
            origin = self.headers.get("Origin", "")
            actual_port = int(self.server.server_address[1])
            allowed_origins = {
                f"http://127.0.0.1:{actual_port}",
                f"http://localhost:{actual_port}",
                f"http://{str(self.server.server_address[0]).lower()}:{actual_port}",
            }
            if origin not in allowed_origins:
                self.send_json(403, {"error": "只允许从本机管理页面修改配置。"})
                return
            if not secrets.compare_digest(
                self.headers.get("X-Freight-CSRF", ""),
                csrf_token,
            ):
                self.send_json(403, {"error": "管理页面安全令牌无效，请刷新页面。"})
                return
            try:
                body = raw_body.decode("utf-8")
                submitted = json.loads(body)
                if data_action:
                    from freight_data import DataConflictError
                    if not isinstance(submitted, dict):
                        raise ValueError("请求内容必须是对象。")
                    try:
                        actions = {'/api/data/delete-preview': data_service.preview_delete,
                            '/api/data/delete':data_service.delete,
                            '/api/data/archive-open':data_service.archive.open,
                            '/api/data/archive-preview':data_service.archive.preview_close,
                            '/api/data/archive-close':data_service.archive.close,
                            '/api/data/archive-retry':data_service.archive.retry,
                            '/api/data/collection-change':data_service.collection_change,
                            '/api/data/daily-generate':data_service.daily.generate}
                        result = actions[request_path](submitted)
                    except DataConflictError as exc:
                        self.send_json(409, {"error": str(exc)})
                        return
                    self.send_json(200, result)
                    return
                if not isinstance(submitted, dict) or not submitted.get("version"):
                    self.send_json(428, {"error": "缺少配置版本，请刷新页面后重试。"})
                    return
                result = configuration.apply_and_request_restart(submitted)
                self.send_json(200, result)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.send_json(400, {"error": "JSON格式不正确。"})
            except ConfigurationConflictError as exc:
                self.send_json(409, {"error": str(exc)})
            except ConfigurationBusyError as exc:
                self.send_json(409, {"error": str(exc)})
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
            except Exception:
                if logger:
                    logger.exception("页面保存配置失败")
                self.send_json(500, {"error": "保存失败，请查看系统日志。"})

        def do_OPTIONS(self):
            self.send_json(405, {"error": "不支持此请求方法。"})

        def log_message(self, format, *args):
            return

    try:
        validated_host = validate_dashboard_host(host)
        validated_port = int(port)
        if not 0 <= validated_port <= 65535:
            raise ValueError("管理页面端口必须在1到65535之间。")
        server = ThreadingHTTPServer((validated_host, validated_port), Handler)
    except (OSError, OverflowError, TypeError, ValueError) as exc:
        if logger:
            logger.warning("状态面板启动失败: %s", exc)
        return None
    thread = threading.Thread(target=server.serve_forever, name="freight-dashboard", daemon=True)
    thread.start()
    return server
