from __future__ import annotations

import csv
import email.utils
import json
import logging
import os
import re
import shutil
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


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
REJECTED_COLUMNS = [
    "记录时间", "群号", "发布人", "消息时间", "原因", "消息文本", "媒体来源"
]


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
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
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
                """
            )

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

    def fetch_rejections(self) -> list[dict]:
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT created_at, group_id, sender, message_time, reason,
                       message_text, media_source
                FROM rejected_messages ORDER BY id DESC
                """
            ).fetchall()
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


def create_daily_backup(
    output_dir: str,
    database_file: str,
    extra_files: list[str] | None = None,
    retention_days: int = 30,
    backup_date: date | None = None,
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

    cutoff = effective_date - timedelta(days=max(1, int(retention_days)))
    for path in Path(backup_dir).glob("*_物流运价备份.zip"):
        try:
            file_date = datetime.strptime(path.name[:10], "%Y-%m-%d").date()
            if file_date < cutoff:
                path.unlink()
        except (ValueError, OSError):
            continue
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
            self._data.update(values)
            self._data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            self._persist_locked()

    def update_group(self, group_id: str, **values) -> None:
        with self._lock:
            group = self._data.setdefault("groups", {}).setdefault(str(group_id), {})
            group.update(values)
            self._data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            self._persist_locked()

    def add_error(self, message: str) -> None:
        with self._lock:
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
            self._data["errors"] = []
            self._data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            self._persist_locked()

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data, ensure_ascii=False))

    def persist(self) -> None:
        with self._lock:
            self._persist_locked()

    def _persist_locked(self) -> None:
        temp_file = self.status_file + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as stream:
            json.dump(self._data, stream, ensure_ascii=False, indent=2)
        os.replace(temp_file, self.status_file)


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
        finally:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass

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

    @staticmethod
    def _normalize_groups(rows, origin_names: set[str]) -> tuple[list[str], dict, dict]:
        if not isinstance(rows, list) or not rows:
            raise ValueError("至少需要保留一个QQ群。")
        group_ids = []
        group_names = {}
        default_origins = {}
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise ValueError(f"QQ群第{index}行格式不正确。")
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
        return group_ids, group_names, default_origins

    def snapshot(self) -> dict:
        with self._lock:
            live = self._read_json(self.live_config_path)
            rules = self._read_json(self.rules_config_path)
        group_ids = [str(value) for value in live.get("group_ids", [])]
        names = live.get("group_names", {})
        origins_by_group = live.get("group_default_origins", {})
        return {
            "connection": {
                "ws_url": str(live.get("ws_url", "ws://127.0.0.1:3001")),
                "access_token": str(live.get("access_token", "")),
                "napcat_launcher": str(live.get("napcat_launcher", "")),
            },
            "groups": [
                {
                    "id": group_id,
                    "name": str(names.get(group_id, "")),
                    "default_origin": str(origins_by_group.get(group_id, "")),
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
            "board_types": list(rules.get("board_types", [])),
            "price_threshold": rules.get("price_threshold", 1000),
        }

    def apply(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("提交内容必须是JSON对象。")
        connection = payload.get("connection", {})
        if not isinstance(connection, dict):
            raise ValueError("NapCat连接配置格式不正确。")
        ws_url = str(connection.get("ws_url", "")).strip()
        if not re.match(r"^wss?://", ws_url, flags=re.IGNORECASE):
            raise ValueError("OneBot地址必须以 ws:// 或 wss:// 开头。")
        access_token = str(connection.get("access_token", "")).strip()
        napcat_launcher = str(connection.get("napcat_launcher", "")).strip()
        if len(ws_url) > 300 or len(access_token) > 500 or len(napcat_launcher) > 500:
            raise ValueError("NapCat连接配置内容过长。")
        origins = self._normalize_locations(payload.get("origins"), "始发地")
        destinations = self._normalize_locations(payload.get("destinations"), "目的地")
        group_ids, group_names, group_origins = self._normalize_groups(
            payload.get("groups"), set(origins)
        )
        board_types = self._split_aliases(payload.get("board_types", []))
        if not board_types:
            raise ValueError("至少需要保留一种货物种类。")
        if any(len(value) > 40 for value in board_types):
            raise ValueError("货物种类名称过长。")
        try:
            price_threshold = float(payload.get("price_threshold"))
        except (TypeError, ValueError) as exc:
            raise ValueError("价格上限必须是数字。") from exc
        if not 0 < price_threshold <= 1_000_000_000:
            raise ValueError("价格上限必须大于0且不超过10亿。")

        with self._lock:
            old_live = self._read_json(self.live_config_path)
            old_rules = self._read_json(self.rules_config_path)
            new_live = json.loads(json.dumps(old_live, ensure_ascii=False))
            new_rules = json.loads(json.dumps(old_rules, ensure_ascii=False))
            new_live["group_ids"] = group_ids
            new_live["group_names"] = group_names
            new_live["group_default_origins"] = group_origins
            new_live["ws_url"] = ws_url
            new_live["access_token"] = access_token
            new_live["napcat_launcher"] = napcat_launcher
            new_rules["origin_groups"] = origins
            new_rules["destination_groups"] = destinations
            new_rules["board_types"] = board_types
            new_rules["price_threshold"] = (
                int(price_threshold) if price_threshold.is_integer() else price_threshold
            )
            if str(new_rules.get("default_origin", "")) not in origins:
                new_rules["default_origin"] = next(iter(origins))

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
                self._atomic_write_json(self.rules_config_path, new_rules)
                self._atomic_write_json(self.live_config_path, new_live)
                if self.validate_callback:
                    self.validate_callback()
            except Exception:
                self._atomic_write_json(self.rules_config_path, old_rules)
                self._atomic_write_json(self.live_config_path, old_live)
                if self.validate_callback:
                    try:
                        self.validate_callback()
                    except Exception:
                        pass
                raise

        if self.logger:
            self.logger.info(
                "页面配置已保存: %s个群, %s个始发地, %s个目的地",
                len(group_ids), len(origins), len(destinations),
            )
        return {
            "ok": True,
            "message": "配置已保存，采集器正在自动重启并应用。",
            "backup_dir": backup_dir,
            "group_count": len(group_ids),
            "origin_count": len(origins),
            "destination_count": len(destinations),
        }

    def request_restart(self) -> None:
        if not self.restart_callback:
            return

        def delayed_restart():
            time.sleep(1.5)
            try:
                self.restart_callback()
            except Exception:
                if self.logger:
                    self.logger.exception("应用页面配置时重启采集器失败")

        threading.Thread(
            target=delayed_restart,
            name="freight-config-restart",
            daemon=True,
        ).start()


DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>物流运价系统</title><style>
*{box-sizing:border-box}body{font-family:"Microsoft YaHei",sans-serif;background:#f4f7fb;color:#1f2937;margin:0;padding:24px}
.wrap{max-width:1120px;margin:auto}.top{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
h1{margin:0}.tabs{display:flex;gap:8px}.tab,.primary,.secondary,.danger{border:0;border-radius:9px;padding:10px 16px;font:inherit;cursor:pointer}
.tab{background:#e8edf5}.tab.active,.primary{background:#2563eb;color:white}.secondary{background:#e8edf5}.danger{background:#fee2e2;color:#b91c1c;padding:7px 11px}
.card{background:white;border-radius:14px;padding:20px;margin:14px 0;box-shadow:0 8px 30px #1f293712}
.section-title{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}.section-title h2{margin:0;font-size:19px}
.ok{color:#07883e}.bad{color:#c62828}.muted{color:#6b7280}.hidden{display:none!important}
.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse}th,td{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:middle}
input,textarea,select{width:100%;border:1px solid #cbd5e1;border-radius:8px;padding:9px 10px;font:inherit;background:white}textarea{min-height:70px;resize:vertical}
.name{min-width:130px}.aliases{min-width:350px}.group-id{min-width:145px}.group-name{min-width:180px}.origin-select{min-width:140px}
.form-grid{display:grid;grid-template-columns:2fr 1fr;gap:16px}.actions{display:flex;align-items:center;gap:12px;position:sticky;bottom:12px}.actions .primary{min-width:150px}
.notice{padding:12px 14px;border-radius:9px;background:#eff6ff;color:#1d4ed8}.notice.bad{background:#fef2f2;color:#b91c1c}
@media(max-width:700px){body{padding:12px}.form-grid{grid-template-columns:1fr}.card{padding:14px}th,td{padding:7px}}
</style></head><body><div class="wrap">
<div class="top"><h1>物流运价系统</h1><div class="tabs"><button class="tab active" data-view="status">运行状态</button><button class="tab" data-view="config">群与线路管理</button></div></div>
<main id="statusView"><div id="statusApp" class="card">正在读取...</div></main>
<main id="configView" class="hidden">
<div class="notice">保存后系统会自动备份配置并重启采集器，通常约15～30秒恢复。历史数据不会删除。</div>
<section class="card"><div class="section-title"><h2>NapCat连接</h2></div><p class="muted">OneBot地址和Token必须与NapCat WebUI中的WebSocket服务器一致；启动器路径可留空。</p><div class="form-grid"><label>OneBot WebSocket地址<input id="wsUrl" placeholder="ws://127.0.0.1:3001"></label><label>Access Token<input id="accessToken" type="password" autocomplete="off" placeholder="未设置时留空"></label></div><label>NapCat启动器路径<input id="napcatLauncher" placeholder="例如 C:/NapCatQQ/launcher-user.bat"></label></section>
<section class="card"><div class="section-title"><h2>QQ群</h2><button id="addGroup" class="secondary">＋ 添加群</button></div><p class="muted">群内消息未写始发地时，按这里选择的默认始发地统计；每个群仍独立输出。</p><div class="table-wrap"><table><thead><tr><th>群号</th><th>群名称</th><th>默认始发地</th><th></th></tr></thead><tbody id="groupRows"></tbody></table></div></section>
<section class="card"><div class="section-title"><h2>始发地范围</h2><button id="addOrigin" class="secondary">＋ 添加始发地</button></div><p class="muted">标准名称用于统计；别名可填写市、区、县、镇等写法，用逗号分隔。</p><div class="table-wrap"><table><thead><tr><th>标准始发地</th><th>识别别名</th><th></th></tr></thead><tbody id="originRows"></tbody></table></div></section>
<section class="card"><div class="section-title"><h2>目的地范围（线路）</h2><button id="addDestination" class="secondary">＋ 添加目的地</button></div><p class="muted">新增标准目的地后，它会与所有始发地自动形成可统计线路。</p><div class="table-wrap"><table><thead><tr><th>标准目的地</th><th>识别的城市/区县别名</th><th></th></tr></thead><tbody id="destinationRows"></tbody></table></div></section>
<section class="card"><div class="form-grid"><label>货物种类（逗号分隔）<textarea id="boardTypes"></textarea></label><label>价格上限（超过后不进入均价）<input id="priceThreshold" type="number" min="0.001" step="0.001"></label></div></section>
<div class="card actions"><button id="saveConfig" class="primary">保存并自动应用</button><span id="saveMessage" class="muted">修改只在点击保存后生效。</span></div>
</main></div>
<script>
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
function esc(v){return String(v??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]))}
function options(selected=''){const names=$$('#originRows .loc-name').map(x=>x.value.trim()).filter(Boolean);if(selected&&!names.includes(selected))names.push(selected);return names.map(x=>`<option ${x===selected?'selected':''}>${esc(x)}</option>`).join('')}
function groupRow(g={}){const tr=document.createElement('tr');tr.innerHTML=`<td><input class="group-id" inputmode="numeric" value="${esc(g.id||'')}"></td><td><input class="group-name" value="${esc(g.name||'')}"></td><td><select class="origin-select">${options(g.default_origin||'')}</select></td><td><button class="danger remove">删除</button></td>`;tr.querySelector('.remove').onclick=()=>tr.remove();return tr}
function locationRow(item={},kind){const tr=document.createElement('tr');tr.innerHTML=`<td><input class="loc-name name" value="${esc(item.name||'')}"></td><td><input class="loc-aliases aliases" value="${esc((item.aliases||[]).join('，'))}"></td><td><button class="danger remove">删除</button></td>`;tr.querySelector('.remove').onclick=()=>{tr.remove();if(kind==='origin')refreshOriginSelects()};tr.querySelector('.loc-name').addEventListener('change',refreshOriginSelects);return tr}
function refreshOriginSelects(){const names=$$('#originRows .loc-name').map(x=>x.value.trim()).filter(Boolean);$$('.origin-select').forEach(sel=>{const old=sel.value;sel.innerHTML=names.map(x=>`<option ${x===old?'selected':''}>${esc(x)}</option>`).join('')})}
async function refreshStatus(){try{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error(r.status);const s=await r.json();const groups=Object.values(s.groups||{});const rows=groups.map(g=>`<tr><td>${esc(g.name)}</td><td>${esc(g.active_records)}</td><td>${esc(g.pending_records)}</td><td>${esc(g.rejected_records)}</td><td>${esc(g.last_message||'-')}</td></tr>`).join('');$('#statusApp').innerHTML=`<p>连接：<b class="${s.connection==='connected'?'ok':'bad'}">${esc(s.connection)}</b></p><p>更新时间：${esc(s.updated_at)}</p><p>可信时间：${esc((s.clock||{}).trusted_now)}（${esc((s.clock||{}).method)}）</p><div class="table-wrap"><table><thead><tr><th>群</th><th>正式</th><th>缓存</th><th>未识别</th><th>最后消息</th></tr></thead><tbody>${rows}</tbody></table></div><p class="muted">错误数：${(s.errors||[]).length}</p>`}catch(e){$('#statusApp').innerHTML='<span class="bad">状态读取失败：'+esc(e)+'</span>'}}
async function loadConfig(){const r=await fetch('/api/config',{cache:'no-store'});if(!r.ok){const x=await r.json().catch(()=>({}));throw Error(x.error||'配置读取失败')}const c=await r.json();const n=c.connection||{};$('#wsUrl').value=n.ws_url||'ws://127.0.0.1:3001';$('#accessToken').value=n.access_token||'';$('#napcatLauncher').value=n.napcat_launcher||'';$('#originRows').replaceChildren(...c.origins.map(x=>locationRow(x,'origin')));$('#destinationRows').replaceChildren(...c.destinations.map(x=>locationRow(x,'destination')));$('#groupRows').replaceChildren(...c.groups.map(groupRow));$('#boardTypes').value=(c.board_types||[]).join('，');$('#priceThreshold').value=c.price_threshold;refreshOriginSelects()}
function splitValues(v){return String(v||'').replaceAll(String.fromCharCode(10),'，').replaceAll(String.fromCharCode(13),'，').split(/[,，、;；]+/).map(x=>x.trim()).filter(Boolean)}
function collectLocations(selector){return $$(selector+' tr').map(tr=>({name:tr.querySelector('.loc-name').value.trim(),aliases:splitValues(tr.querySelector('.loc-aliases').value)}))}
function collectConfig(){return {connection:{ws_url:$('#wsUrl').value.trim(),access_token:$('#accessToken').value.trim(),napcat_launcher:$('#napcatLauncher').value.trim()},groups:$$('#groupRows tr').map(tr=>({id:tr.querySelector('.group-id').value.trim(),name:tr.querySelector('.group-name').value.trim(),default_origin:tr.querySelector('.origin-select').value})),origins:collectLocations('#originRows'),destinations:collectLocations('#destinationRows'),board_types:splitValues($('#boardTypes').value),price_threshold:$('#priceThreshold').value}}
async function waitForRestart(){let attempts=0;const timer=setInterval(async()=>{attempts++;try{const r=await fetch('/api/config',{cache:'no-store'});if(r.ok){clearInterval(timer);await loadConfig();await refreshStatus();$('#saveConfig').disabled=false;$('#saveMessage').className='ok';$('#saveMessage').textContent='配置已应用，采集器已恢复运行。'}}catch(e){}if(attempts>30){clearInterval(timer);$('#saveConfig').disabled=false;$('#saveMessage').className='bad';$('#saveMessage').textContent='等待重启超时，请查看运行状态或启动器。'}},2000)}
async function saveConfig(){const button=$('#saveConfig');button.disabled=true;$('#saveMessage').className='muted';$('#saveMessage').textContent='正在校验并保存...';try{const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(collectConfig())});const x=await r.json();if(!r.ok)throw Error(x.error||'保存失败');$('#saveMessage').className='ok';$('#saveMessage').textContent=x.message;setTimeout(waitForRestart,2000)}catch(e){button.disabled=false;$('#saveMessage').className='bad';$('#saveMessage').textContent=e.message}}
$$('.tab').forEach(b=>b.onclick=async()=>{$$('.tab').forEach(x=>x.classList.toggle('active',x===b));const config=b.dataset.view==='config';$('#statusView').classList.toggle('hidden',config);$('#configView').classList.toggle('hidden',!config);if(config)try{await loadConfig()}catch(e){$('#saveMessage').className='bad';$('#saveMessage').textContent=e.message}});
$('#addGroup').onclick=()=>$('#groupRows').append(groupRow());$('#addOrigin').onclick=()=>{$('#originRows').append(locationRow({},'origin'));refreshOriginSelects()};$('#addDestination').onclick=()=>$('#destinationRows').append(locationRow({},'destination'));$('#saveConfig').onclick=saveConfig;
refreshStatus();setInterval(refreshStatus,5000);if(new URLSearchParams(location.search).get('view')==='config')document.querySelector('[data-view="config"]').click();
</script></body></html>"""


def start_status_dashboard(
    status: RuntimeStatus,
    host: str = "127.0.0.1",
    port: int = 8765,
    logger: logging.Logger | None = None,
    configuration: FreightConfigurationManager | None = None,
) -> ThreadingHTTPServer | None:
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status_code: int, value: dict) -> None:
            payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            request_path = self.path.split("?", 1)[0]
            if request_path == "/api/status":
                self.send_json(200, status.snapshot())
                return
            if request_path == "/api/config":
                if not configuration:
                    self.send_json(404, {"error": "配置管理未启用。"})
                    return
                try:
                    self.send_json(200, configuration.snapshot())
                except Exception as exc:
                    self.send_json(500, {"error": f"配置读取失败：{exc}"})
                return
            payload = DASHBOARD_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            if self.path.split("?", 1)[0] != "/api/config" or not configuration:
                self.send_json(404, {"error": "接口不存在。"})
                return
            origin = self.headers.get("Origin", "")
            actual_port = int(self.server.server_address[1])
            allowed_origins = {
                f"http://127.0.0.1:{actual_port}",
                f"http://localhost:{actual_port}",
            }
            if origin and origin not in allowed_origins:
                self.send_json(403, {"error": "只允许从本机管理页面修改配置。"})
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
                body = self.rfile.read(content_length).decode("utf-8")
                result = configuration.apply(json.loads(body))
                self.send_json(200, result)
                configuration.request_restart()
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.send_json(400, {"error": "JSON格式不正确。"})
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
            except Exception as exc:
                if logger:
                    logger.exception("页面保存配置失败")
                self.send_json(500, {"error": f"保存失败：{exc}"})

        def log_message(self, format, *args):
            return

    try:
        server = ThreadingHTTPServer((host, int(port)), Handler)
    except OSError as exc:
        if logger:
            logger.warning("状态面板启动失败: %s", exc)
        return None
    thread = threading.Thread(target=server.serve_forever, name="freight-dashboard", daemon=True)
    thread.start()
    return server
