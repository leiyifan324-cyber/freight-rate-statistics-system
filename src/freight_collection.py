"""Durable user-controlled admission gate; queued messages may finish after pause."""
from __future__ import annotations

import math
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable


class CollectionControl:
    def __init__(self, root: str | Path) -> None:
        self.path = Path(root).resolve() / '采集控制.db'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.pending_count: Callable[[], int] = lambda: 0
        with self._session() as con:
            con.executescript('''CREATE TABLE IF NOT EXISTS control
                (id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL,
                 version INTEGER NOT NULL, changed_at TEXT NOT NULL);
                INSERT OR IGNORE INTO control VALUES(1,1,0,'');
                CREATE TABLE IF NOT EXISTS pauses
                (id INTEGER PRIMARY KEY, start REAL NOT NULL, end REAL);''')

    @contextmanager
    def _session(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as con, con:
            con.row_factory = sqlite3.Row
            yield con

    def snapshot(self) -> dict:
        with self._lock, self._session() as con:
            row = dict(con.execute('SELECT * FROM control WHERE id=1').fetchone())
            pending = self.pending_count()
        enabled = bool(row['enabled'])
        return {'enabled': enabled, 'version': row['version'], 'changed_at': row['changed_at'],
                'state': 'collecting' if enabled else ('pausing' if pending else 'paused'),
                'pending': pending, 'scope': 'all_groups'}

    def change(self, body: dict) -> dict:
        from freight_data import DataConflictError
        if type(body.get('enabled')) is not bool:
            raise ValueError('采集状态必须为开始或停止。')
        if body['enabled'] is False and body.get('confirm') is not True:
            raise ValueError('请确认停止所有群的采集；停止期间不补采。')
        with self._lock, self._session() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT * FROM control WHERE id=1').fetchone()
            if type(body.get('version')) is not int or body['version'] != row['version']:
                raise DataConflictError('采集状态已变化，请刷新后重新操作。')
            if bool(row['enabled']) != body['enabled']:
                now = time.time()
                if body['enabled']:
                    con.execute('UPDATE pauses SET end=? WHERE end IS NULL', (now,))
                else:
                    con.execute('INSERT INTO pauses(start) VALUES(?)', (now,))
                con.execute('UPDATE control SET enabled=?,version=version+1,changed_at=? WHERE id=1',
                            (int(body['enabled']), datetime.now().astimezone().isoformat(timespec='seconds')))
        return self.snapshot()

    def submit(self, event: object, callback: Callable[[], dict]) -> dict:
        """Admission and durable enqueue share the gate lock with stop/start."""
        data = event if isinstance(event, dict) else {}
        # Heartbeats and non-message events are not freight collection.
        if data.get('post_type') not in {'message', 'message_sent'}:
            return callback()
        with self._lock, self._session() as con:
            enabled = bool(con.execute('SELECT enabled FROM control WHERE id=1').fetchone()[0])
            paused_time = False
            stamp = data.get('time')
            if enabled and isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and math.isfinite(stamp):
                # OneBot timestamps have second precision; compare paused second ranges.
                paused_time = con.execute('SELECT 1 FROM pauses WHERE CAST(start AS INTEGER)<=? '
                    'AND end IS NOT NULL AND CAST(end AS INTEGER)>? LIMIT 1', (stamp, stamp)).fetchone() is not None
            if not enabled or paused_time:
                return {'status': 'ignored', 'group_id': str(data.get('group_id', '')),
                        'reason': '已停止采集，新消息不入库' if not enabled else '暂停期间的历史消息不补采',
                        'collection_paused': True}
            return callback()
