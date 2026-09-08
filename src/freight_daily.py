"""Explicit daily report snapshots, paired PNG/XLSX output, and stale detection."""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

FOLDER = '每日图表'


def json_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class DailyReportService:
    def __init__(self, data_service) -> None:
        self.data = data_service
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        # An interrupted job is not a successfully generated report.
        for profile in self.data.profiles.values():
            path = self._root(profile) / '.job.json'
            if path.exists():
                try:
                    job = json.loads(path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    job = {'state': 'interrupted', 'error': '上次生成状态文件无法读取，请重新生成。'}
                    json_write(path, job)
                if job.get('state') == 'running':
                    job.update(state='interrupted', error='上次生成被程序退出中断，请重新生成。')
                    json_write(path, job)

    @staticmethod
    def _root(profile: dict) -> Path:
        return Path(profile['output_dir']).resolve() / FOLDER

    def selection(self, params: dict) -> dict:
        import qq_freight_parser as parser
        from freight_archive import parse_day
        self.data.profile(str(params.get('group_id', '')))
        day = parse_day(params.get('day'))
        if day > parser.trusted_today().isoformat():
            raise ValueError('不能为未来日期生成当日图表。')
        result = {'group_id': str(params['group_id']), 'day': day}
        for key in ('origin', 'destination'):
            value = params.get(key, '')
            if not isinstance(value, str) or len(value) > 100 or any(ord(c) < 32 for c in value):
                raise ValueError('线路筛选无效。')
            result[key] = value.strip()
        return result

    def snapshot(self, selected: dict) -> dict:
        import qq_freight_parser as parser
        from freight_archive import active_records, context_now, read_year
        from freight_runtime import FreightDatabase
        profile = self.data.profile(selected['group_id'])
        # Same order as report/deletion locks. The DB transaction gives one record snapshot.
        with parser.group_output_lock(profile), parser.QQ_OUTPUT_BUILD_LOCK:
            with FreightDatabase(profile['database_file']).session() as con:
                con.execute('BEGIN')
                records = [parser.normalize_record_cargo(json.loads(r[0])) for r in con.execute(
                    'SELECT record_json FROM freight_records WHERE effective_date=? ORDER BY dedup_key', (selected['day'],))]
            records = [r for r in records if (not selected['origin'] or r['始发地'] == selected['origin'])
                       and (not selected['destination'] or r['目的城市'] == selected['destination'])]
            records = active_records(profile, records)
            records = [r for r in records if parser.validate_cargo_route(r)[0]]
            context = context_now()
            rows = parser.summarize_daily_quote(records)
            year = read_year(profile.get('archive_database'))
            year_name = (year.get('current') or {}).get('name', '历史统计模式')
            payload = {'selection': selected, 'records': records, 'context': context,
                       'scope_rules': parser.CARGO_ROUTE_SCOPES, 'year_name': year_name,
                       'group_name': profile['group_name']}
            fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return {**payload, 'rows': rows, 'fingerprint': fingerprint,
                'quote_count': sum(r['报价数量'] for r in rows)}

    def generate(self, body: dict) -> dict:
        from freight_data import DataConflictError
        selected = self.selection(body)
        if self.data.collection_status().get('pending', 0):
            raise DataConflictError('已接收消息还在处理中，请等待队列完成后生成图表。')
        with self._lock:
            if self._worker and self._worker.is_alive():
                raise DataConflictError('已有图表正在生成，请完成后再操作。')
            snapshot = self.snapshot(selected)
            if not snapshot['rows']:
                raise ValueError('该日期和线路没有参与当前年度统计的有效报价，未生成空图。')
            stamp = datetime.now().astimezone().isoformat(timespec='seconds')
            job = {'id': uuid.uuid4().hex, 'state': 'running', 'selection': selected,
                   'generated_at': stamp, 'created_ns': time.time_ns(), 'error': ''}
            root = self._root(self.data.profile(selected['group_id']))
            json_write(root / '.job.json', job)
            self._worker = threading.Thread(target=self._export, args=(root, snapshot, job),
                                            name='freight-daily-export', daemon=True)
            self._worker.start()
        return job

    def _export(self, root: Path, snapshot: dict, job: dict) -> None:
        import qq_freight_parser as parser
        from freight_archive import tables_for, write_reports
        day = job['selection']['day']
        staging = root / ('.pending-' + job['id'])
        target = root / day / job['id']
        try:
            staging.mkdir(parents=True)
            cycle = {'name': f'{snapshot["group_name"]} · {day} 日报', 'start_day': day,
                     'end_day': day, 'state': 'closed'}
            tables = tables_for(snapshot['records'], cycle, snapshot['context'], day)
            tables['生成说明'] = [{'统计日期': day, '生成时间': job['generated_at'],
                '群名称': snapshot['group_name'], '参与报价': snapshot['quote_count'],
                '说明': '固定快照；后续数据变化需重新生成；与图片使用同一份报价。'}]
            write_reports(staging, tables)
            (staging / '年度统计汇总.xlsx').rename(staging / '当日报价与统计.xlsx')
            # Matplotlib is shared with the legacy report writers.
            with parser.QQ_OUTPUT_BUILD_LOCK:
                images = draw_daily(staging, snapshot, job)
            metadata = {k: v for k, v in snapshot.items() if k not in ('records', 'context', 'scope_rules')}
            metadata.update(id=job['id'], generated_at=job['generated_at'], created_ns=job['created_ns'],
                            files=[*images, '当日报价与统计.xlsx'])
            json_write(staging / 'snapshot.json', metadata)
            target.parent.mkdir(parents=True, exist_ok=True)
            staging.rename(target)  # Readers never see a half-written report set.
            job.update(state='ready', path=f'{FOLDER}/{day}/{job["id"]}')
        except Exception as exc:
            job.update(state='error', error=f'生成失败（{type(exc).__name__}），请检查磁盘空间或日志后重试。')
            if self.data.status:
                self.data.status.add_error('生成每日图表失败: ' + str(exc)[:300])
        finally:
            json_write(root / '.job.json', job)

    def status(self, params: dict) -> dict:
        selected = self.selection(params)
        root = self._root(self.data.profile(selected['group_id']))
        current = self.snapshot(selected)
        reports, errors = [], []
        for path in (root / selected['day']).glob('*/snapshot.json'):
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                continue
            try:
                metadata = json.loads(path.read_text(encoding='utf-8'))
                if metadata['selection'] != selected:
                    continue
                metadata['stale'] = metadata['fingerprint'] != current['fingerprint']
                metadata['missing'] = any(not (path.parent / name).is_file() for name in metadata['files'])
                metadata['base_path'] = path.parent.relative_to(root.parent).as_posix()
                reports.append(metadata)
            except (OSError, ValueError, KeyError):
                errors.append('部分日报元数据无法读取，请检查磁盘或重新生成。')
        reports.sort(key=lambda row: (row.get('created_ns', 0), row['generated_at'], row['id']), reverse=True)
        job_path = root / '.job.json'
        try:
            job = json.loads(job_path.read_text(encoding='utf-8')) if job_path.exists() else {'state': 'idle'}
        except (OSError, ValueError):
            job = {'state': 'error', 'error': '生成状态文件无法读取，请重新生成。'}
        return {'job': job, 'reports': reports[:20], 'total': len(reports), 'errors': errors,
                'current_quote_count': current['quote_count'], 'day': selected['day']}

    def wait_idle(self, timeout: float = 30) -> bool:
        worker = self._worker
        if worker:
            worker.join(timeout)
        return worker is None or not worker.is_alive()


def draw_daily(folder: Path, snapshot: dict, job: dict) -> list[str]:
    """Route/cargo comparisons, not a fictitious one-day time trend."""
    from matplotlib import rc_context
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    rows = snapshot['rows']
    pages = math.ceil(len(rows) / 12)
    files = []
    with rc_context({'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'DejaVu Sans'],
                     'axes.unicode_minus': False, 'text.parse_math': False}):
        for page in range(pages):
            values = rows[page * 12:(page + 1) * 12]
            figure = Figure(figsize=(12, max(5, 2.8 + len(values) * 0.52)), dpi=160, facecolor='#f5f8fa')
            FigureCanvasAgg(figure)
            ax = figure.add_axes([0.26, 0.23, 0.68, 0.52])
            labels = [f'{r["始发地"]} → {r["目的城市"]} · {r["货物"]}' for r in values]
            y = list(range(len(values)))
            means = [r['平均运价'] for r in values]
            ax.barh(y, means, color='#216b73', height=0.5)
            for index, row in enumerate(values):
                ax.text(row['平均运价'] + max(means) * .025, index,
                        f'{row["平均运价"]:.3f}  /  {row["报价数量"]}条', va='center', fontsize=10, color='#173c48')
            ax.set_yticks(y, labels, fontsize=10)
            ax.invert_yaxis()
            ax.set_xlim(0, max(means) * 1.4)
            ax.set_xlabel('平均运价（元/吨，沿用当前吨价统计口径）', fontsize=10)
            ax.grid(axis='x', alpha=.16)
            ax.set_axisbelow(True)
            for spine in ax.spines.values():
                spine.set_visible(False)
            figure.text(.05, .91, job['selection']['day'] + '  当日运价统计', fontsize=22, weight='bold', color='#143e49')
            name = snapshot['group_name']
            figure.text(.05, .84, name[:55] + f'  ·  共 {snapshot["quote_count"]} 条有效报价  ·  {page+1}/{pages}', fontsize=11)
            figure.text(.05, .10, '统计范围：' + (job['selection']['origin'] or '全部始发地') + ' → '
                        + (job['selection']['destination'] or '全部目的城市') + '；按货物大类分别计算日均价。', fontsize=10)
            figure.text(.05, .045, '固定快照 · 生成时间 ' + job['generated_at'] + ' · 后续补录/删除需重新生成', fontsize=9, color='#62727a')
            name = f'当日运价_{page+1:02d}.png'
            figure.savefig(folder / name, dpi=160)
            figure.clear()
            files.append(name)
    return files
