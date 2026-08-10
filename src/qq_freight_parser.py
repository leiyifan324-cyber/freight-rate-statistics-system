import re
import csv
import os
import sys
import time
import json
import hashlib
import argparse
import calendar
from collections import defaultdict
from statistics import mean
from datetime import date, datetime, timedelta

import pandas as pd
import matplotlib.pyplot as plt
from openpyxl import load_workbook

from freight_runtime import (
    FreightDatabase,
    FreightConfigurationManager,
    NetworkClock,
    RuntimeStatus,
    WindowsOcr,
    configure_logging,
    create_daily_backup,
    start_status_dashboard,
)

# =========================
# 1. 配置区
# =========================

# 贵港群中未写始发地时，默认按贵港统计
DEFAULT_ORIGIN = "贵港"

# 同一价格区内的地点统一归并为标准始发地
ORIGIN_GROUPS = {
    "贵港": ["贵港市", "贵港", "港南区", "港南", "港北区", "港北", "覃塘区", "覃塘", "东龙镇", "东龙"],
    "南宁": ["南宁市", "南宁"],
    "武鸣": ["武鸣区", "武鸣"]
}

# 计划采集的 10 个目的城市及其常见区县
DEST_GROUPS = {
    "佛山": ["佛山", "南海", "顺德", "三水", "高明"],
    "杭州": ["杭州", "余杭", "萧山", "富阳", "临安", "建德"],
    "成都": ["成都", "双流", "龙泉驿", "龙泉", "郫都", "新都", "温江", "青白江"],
    "重庆": ["重庆", "渝北", "江北", "九龙坡", "沙坪坝", "巴南", "涪陵", "永川", "合川", "江津"],
    "长沙": ["长沙", "长沙县", "浏阳", "宁乡", "望城"],
    "南昌": ["南昌", "新建", "进贤", "安义"],
    "武汉": ["武汉", "汉口", "武昌", "汉阳", "东西湖", "蔡甸", "江夏", "黄陂", "新洲"],
    "西安": ["西安", "长安", "临潼", "高陵", "鄠邑", "蓝田", "周至"],
    "福州": ["福州", "福清", "长乐", "闽侯", "连江", "罗源", "闽清", "永泰"],
    "临沂": ["临沂", "兰山", "罗庄", "河东", "郯城", "兰陵", "沂水", "沂南", "平邑", "费县", "蒙阴", "莒南", "临沭"]
}

# 只识别这三种板
BOARD_TYPES = ["大板", "红板", "拼板"]

# 大于这个值，视为整车价，不进入每日平均运价
PRICE_THRESHOLD = 1000

# 所有最终价格统计统一保留三位小数
DECIMAL_PLACES = 3
CSV_FLOAT_FORMAT = f"%.{DECIMAL_PLACES}f"
EXCEL_DECIMAL_FORMAT = "0." + ("0" * DECIMAL_PLACES)
PRICE_OUTPUT_COLUMNS = {
    "最低报价", "最高报价", "平均报价",
    "平均运价", "最低运价", "最高运价",
    "7日均线", "价格指数", "数据覆盖率", "月份覆盖率"
}

# 无效消息关键词
INVALID_LINE_KEYWORDS = [
    "不要标价", "有车了", "求车", "找车", "联系", "有车联系"
]

# 常见前缀
LINE_PREFIX_WORDS = [
    "今明", "明天", "后天", "今天", "急", "现货", "计划"
]

HEADER_PATTERN = re.compile(
    r'^(?P<sender>.+?):\s*(?:(?P<year>\d{4})-)?(?P<month>\d{2})-(?P<day>\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2})$'
)

# 支持：
# # 03-12
# 03-12
# 3/12
# 3月12日
DATE_MARKER_PATTERN = re.compile(
    r'^(?:#\s*)?(?:(?P<year>\d{4})[-/年])?(?P<month>\d{1,2})[-/月](?P<day>\d{1,2})(?:日)?$'
)

# 支持：
# 03-12 武鸣到佛山南海 大板 120
INLINE_DATE_PATTERN = re.compile(
    r'^(?:(?P<year>\d{4})[-/])?(?P<month>\d{1,2})[-/](?P<day>\d{1,2})\s+(?P<body>.+)$'
)

INPUT_FILE = "qq_chat.txt"
FORMATTED_INPUT_FILE = "格式化后_qq_chat.txt"
DETAIL_CSV = "报价明细.csv"
PENDING_CSV = "待生效运价.csv"
DAILY_CSV = "每日平均运价.csv"
HISTORY_DAILY_CSV = "历史每日平均运价.csv"
WEEKLY_CSV = "每周平均运价.csv"
MONTHLY_CSV = "每月平均运价.csv"
YEARLY_CSV = "每年平均运价.csv"
REPORT_XLSX = "物流报价汇总.xlsx"
PENDING_REPORT_XLSX = "物流报价汇总_待更新.xlsx"
CHART_FOLDER = "charts"
OBSERVATION_CONFIG_FILE = "运价观察周期.json"
QQ_LIVE_CONFIG_FILE = "qq_live_config.json"
QQ_LIVE_STATE_FILE = ".qq_live_state.json"
RULES_CONFIG_FILE = "freight_rules.json"
DATABASE_FILE = "运价数据.db"
REJECTED_CSV = "未识别消息.csv"
MAX_SEEN_QQ_MESSAGES = 10000

WEEKLY_COLUMNS = [
    "周序号", "周期开始", "周期结束", "始发地", "目的城市", "货物",
    "平均运价", "最低运价", "最高运价", "数据天数", "观察天数",
    "数据覆盖率", "报价数量", "周期状态"
]
MONTHLY_COLUMNS = [
    "月份序号", "月份", "周期开始", "周期结束", "实际观察开始", "实际观察结束",
    "自然月天数", "观察天数", "始发地", "目的城市", "货物", "平均运价",
    "最低运价", "最高运价", "数据天数", "数据覆盖率", "报价数量", "周期状态"
]
YEARLY_COLUMNS = [
    "观察年序号", "观察年", "周期开始", "周期结束", "实际观察结束", "始发地",
    "目的城市", "货物", "平均运价", "最低运价", "最高运价", "数据月份数",
    "已过月份数", "月份覆盖率", "数据天数", "报价数量", "周期状态"
]

DEBUG = False
CLOCK = NetworkClock()
LOGGER = None


def application_dir() -> str:
    """返回源码运行或PyInstaller发行版的可写应用目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def trusted_today() -> date:
    return CLOCK.today()


# =========================
# 2. 调试输出
# =========================

def debug(msg):
    if DEBUG:
        print(msg)


# =========================
# 3. 文本处理
# =========================

def normalize_text(text: str) -> str:
    text = text.strip()
    text = text.replace("　", " ")
    text = text.replace("→", "到").replace("->", "到")
    text = text.replace("～", "-")
    text = re.sub(r"\s+", " ", text)
    return text


def preprocess_line(line: str) -> str:
    text = line.strip()
    for w in LINE_PREFIX_WORDS:
        if text.startswith(w):
            text = text[len(w):].strip()
    return text


# =========================
# 4. 文件处理
# =========================

def delete_outputs_except_history():
    old_files = [
        DETAIL_CSV, PENDING_CSV, DAILY_CSV, WEEKLY_CSV, MONTHLY_CSV, YEARLY_CSV,
    ]
    for file in old_files:
        if os.path.exists(file):
            try:
                os.remove(file)
                print(f"已删除旧文件: {file}")
            except Exception as e:
                print(f"删除失败 {file}: {e}")

    if os.path.exists(CHART_FOLDER):
        for name in os.listdir(CHART_FOLDER):
            path = os.path.join(CHART_FOLDER, name)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except Exception as e:
                    print(f"删除旧图片失败 {path}: {e}")
    else:
        os.makedirs(CHART_FOLDER, exist_ok=True)


# =========================
# 5. 消息头解析
# =========================

def parse_header(line: str):
    m = HEADER_PATTERN.match(line)
    if not m:
        return None
    year = int(m.group("year") or trusted_today().year)
    month = int(m.group("month"))
    day = int(m.group("day"))
    try:
        parsed_date = date(year, month, day)
    except ValueError:
        return None
    return {
        "sender": m.group("sender"),
        "date": parsed_date.isoformat(),
        "time": m.group("time")
    }


def parse_date_marker(line: str):
    m = DATE_MARKER_PATTERN.match(line.strip())
    if not m:
        return None
    year = int(m.group("year") or trusted_today().year)
    month = int(m.group("month"))
    day = int(m.group("day"))
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_inline_date_line(line: str):
    m = INLINE_DATE_PATTERN.match(line.strip())
    if not m:
        return None
    year = int(m.group("year") or trusted_today().year)
    month = int(m.group("month"))
    day = int(m.group("day"))
    body = m.group("body").strip()
    try:
        parsed_date = date(year, month, day).isoformat()
    except ValueError:
        return None
    return parsed_date, body


RELATIVE_DATE_PATTERNS = [
    (re.compile(r"明天\s*(?:和|及|与|、|/)?\s*后天|明后(?:两天|天)?"), (1, 2)),
    (re.compile(r"今明(?:两天)?"), (0, 1)),
    (re.compile(r"后天"), (2,)),
    (re.compile(r"明天|明日"), (1,)),
    (re.compile(r"今天|今日"), (0,)),
]

WEEKDAY_MAP = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
EXPLICIT_CHINESE_DATE_PATTERN = re.compile(
    r"(?:(?P<year>\d{4})年)?(?P<month>\d{1,2})月(?P<day>\d{1,2})日?"
)
EXPLICIT_SLASH_DATE_PATTERN = re.compile(
    r"(?:(?P<year>\d{4})[-/])?(?P<month>\d{1,2})[-/](?P<day>\d{1,2})"
)
WEEKDAY_PATTERN = re.compile(
    r"(?P<prefix>下周|下星期|下礼拜|本周|本星期|本礼拜|周|星期|礼拜)"
    r"(?P<weekday>[一二三四五六日天])"
)


def load_rules_config(config_path: str = RULES_CONFIG_FILE) -> dict:
    """加载可编辑业务规则；文件不存在时继续使用代码内置默认值。"""
    global DEFAULT_ORIGIN, ORIGIN_GROUPS, DEST_GROUPS, BOARD_TYPES
    global PRICE_THRESHOLD, INVALID_LINE_KEYWORDS, RELATIVE_DATE_PATTERNS

    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as stream:
        config = json.load(stream)

    if isinstance(config.get("origin_groups"), dict) and config["origin_groups"]:
        ORIGIN_GROUPS = {
            str(name): [str(alias) for alias in aliases]
            for name, aliases in config["origin_groups"].items()
        }
    if isinstance(config.get("destination_groups"), dict) and config["destination_groups"]:
        DEST_GROUPS = {
            str(name): [str(alias) for alias in aliases]
            for name, aliases in config["destination_groups"].items()
        }
    if isinstance(config.get("board_types"), list) and config["board_types"]:
        BOARD_TYPES = [str(value) for value in config["board_types"]]
    if isinstance(config.get("invalid_line_keywords"), list):
        INVALID_LINE_KEYWORDS = [str(value) for value in config["invalid_line_keywords"]]
    if config.get("price_threshold") is not None:
        PRICE_THRESHOLD = float(config["price_threshold"])
    configured_default = str(config.get("default_origin", DEFAULT_ORIGIN)).strip()
    if configured_default in ORIGIN_GROUPS:
        DEFAULT_ORIGIN = configured_default

    aliases = config.get("relative_date_aliases")
    if isinstance(aliases, dict):
        def compile_aliases(name: str, fallback: list[str]) -> re.Pattern:
            values = aliases.get(name, fallback)
            values = sorted({str(value) for value in values if str(value)}, key=len, reverse=True)
            return re.compile("|".join(re.escape(value) for value in values))

        RELATIVE_DATE_PATTERNS = [
            (compile_aliases("tomorrow_and_day_after", ["明后天"]), (1, 2)),
            (compile_aliases("today_and_tomorrow", ["今明"]), (0, 1)),
            (compile_aliases("day_after_tomorrow", ["后天"]), (2,)),
            (compile_aliases("tomorrow", ["明天", "明日"]), (1,)),
            (compile_aliases("today", ["今天", "今日"]), (0,)),
        ]
    return config


def expand_relative_freight_dates(line: str, base_date_text: str) -> list[tuple[str, str]]:
    """把相对日期报价展开为一个或多个生效日期，并移除日期提示词。"""
    try:
        base_date = datetime.strptime(base_date_text, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return []

    normalized_line = normalize_text(line)
    for pattern, offsets in RELATIVE_DATE_PATTERNS:
        match = pattern.search(normalized_line)
        if not match:
            continue
        freight_text = normalize_text(
            normalized_line[:match.start()] + " " + normalized_line[match.end():]
        )
        return [
            ((base_date + timedelta(days=offset)).isoformat(), freight_text)
            for offset in offsets
        ]

    for pattern in (EXPLICIT_CHINESE_DATE_PATTERN, EXPLICIT_SLASH_DATE_PATTERN):
        match = pattern.search(normalized_line)
        if not match:
            continue
        try:
            explicit_date = date(
                int(match.group("year") or base_date.year),
                int(match.group("month")),
                int(match.group("day")),
            )
        except ValueError:
            return []
        freight_text = normalize_text(
            normalized_line[:match.start()] + " " + normalized_line[match.end():]
        )
        return [(explicit_date.isoformat(), freight_text)]

    weekday_match = WEEKDAY_PATTERN.search(normalized_line)
    if weekday_match:
        target_weekday = WEEKDAY_MAP[weekday_match.group("weekday")]
        prefix = weekday_match.group("prefix")
        week_start = base_date - timedelta(days=base_date.weekday())
        if prefix.startswith("下"):
            effective_date = week_start + timedelta(days=7 + target_weekday)
        elif prefix.startswith("本"):
            effective_date = week_start + timedelta(days=target_weekday)
        else:
            effective_date = base_date + timedelta(
                days=(target_weekday - base_date.weekday()) % 7
            )
        freight_text = normalize_text(
            normalized_line[:weekday_match.start()] + " " + normalized_line[weekday_match.end():]
        )
        return [(effective_date.isoformat(), freight_text)]

    return [(base_date.isoformat(), normalized_line)]


# =========================
# 6. 筛选规则
# =========================

def is_invalid_line(line: str) -> bool:
    return any(k in line for k in INVALID_LINE_KEYWORDS)


def normalize_origin(origin: str):
    """把贵港下辖片区等别名统一为标准始发地。"""
    text = re.sub(r"\s+", "", origin or "")
    for city, aliases in ORIGIN_GROUPS.items():
        for alias in sorted(aliases, key=len, reverse=True):
            if alias in text:
                return city
    return None


def extract_origin_prefix(text: str):
    """识别未使用“到”字时写在行首的始发地。"""
    compact = text.lstrip()
    for city, aliases in ORIGIN_GROUPS.items():
        for alias in sorted(aliases, key=len, reverse=True):
            if compact.startswith(alias):
                remaining = compact[len(alias):].lstrip(" 市区县镇-—至到:：,，")
                return city, remaining
    return None, text


def normalize_destination_city(dest: str):
    for city, keywords in DEST_GROUPS.items():
        for keyword in sorted(keywords, key=len, reverse=True):
            if keyword in dest:
                return city
    return None


# =========================
# 7. 路线提取
# =========================

def extract_route(line: str, default_origin: str = DEFAULT_ORIGIN):
    """
    支持：
    武鸣到佛山南海 大板 120
    武鸣到佛山南海九江大板125/130
    南宁到成都双流大板210
    """
    text = preprocess_line(line)

    if "到" in text:
        left, right = text.split("到", 1)
        origin = normalize_origin(left)
        if not origin:
            return None, f"始发地不在范围内: {left.strip()}"
        rest_all = right.strip()
    else:
        origin, rest_all = extract_origin_prefix(text)
        if not origin:
            origin = default_origin
            rest_all = text

    # 选择最靠前的目的地关键词；位置相同时优先匹配更长的区县名。
    destination_matches = []
    for city, keywords in DEST_GROUPS.items():
        for keyword in keywords:
            idx = rest_all.find(keyword)
            if idx != -1:
                destination_matches.append((idx, -len(keyword), city, keyword))

    if not destination_matches:
        return None, f"目的地不在范围内: {rest_all}"

    dest_start, _, matched_city, matched_keyword = min(destination_matches)

    # 目的地原文截止到货物名称或第一个价格数字，保留“佛山南海九江”等明细地点。
    end_candidates = []
    for cargo in BOARD_TYPES:
        cargo_pos = rest_all.find(cargo, dest_start + len(matched_keyword))
        if cargo_pos != -1:
            end_candidates.append(cargo_pos)

    price_match = re.search(r"\d", rest_all[dest_start + len(matched_keyword):])
    if price_match:
        end_candidates.append(dest_start + len(matched_keyword) + price_match.start())

    dest_end = min(end_candidates) if end_candidates else len(rest_all)
    matched_dest_raw = rest_all[dest_start:dest_end].strip(" -—:：,，、;；")
    matched_dest_raw = re.sub(r"\s+", "", matched_dest_raw) or matched_keyword
    rest = rest_all[dest_end:].strip()

    return (origin, matched_dest_raw, matched_city, rest), None


# =========================
# 8. 货物提取
# =========================

def extract_cargo(rest: str):
    for cargo in BOARD_TYPES:
        if cargo in rest:
            return cargo
    return None


# =========================
# 9. 报价提取
# =========================

def is_vehicle_size_number(num_str: str) -> bool:
    try:
        val = float(num_str)
    except ValueError:
        return False

    vehicle_sizes = [13, 13.5, 13.75, 14, 16, 17.5]
    return any(abs(val - x) < 0.01 for x in vehicle_sizes)


def parse_price_text(price_text: str):
    nums = re.findall(r"\d+(?:\.\d+)?", price_text)
    if not nums:
        return None

    values = [float(x) for x in nums]
    return {
        "price_text": price_text,
        "price_min": round(min(values), DECIMAL_PLACES),
        "price_max": round(max(values), DECIMAL_PLACES),
        "price_avg": round(sum(values) / len(values), DECIMAL_PLACES)
    }


def extract_price(rest: str):
    """
    支持：
    118
    120/125
    160-170
    3400/3450
    过滤 13.75 / 17.5 等车型长度
    """
    text = re.sub(r"1\d{10}", " ", rest)

    candidates = []
    pattern = r"\d+(?:\.\d+)?(?:[/-]\d+(?:\.\d+)?)*"
    for m in re.finditer(pattern, text):
        token = m.group()
        nums = re.findall(r"\d+(?:\.\d+)?", token)

        if nums and all(is_vehicle_size_number(x) for x in nums):
            continue

        candidates.append((m.start(), token))

    if not candidates:
        return None

    for _, token in reversed(candidates):
        parsed = parse_price_text(token)
        if not parsed:
            continue
        if parsed["price_avg"] < 20:
            continue
        return parsed

    return None


# =========================
# 10. 自动整理输入
# =========================

def auto_format_freight_line(line: str, default_origin: str = DEFAULT_ORIGIN):
    """
    把原始货运文本整理成程序容易识别的格式：
    始发地到目的地 货物 价格

    规则：
    - 用现有 extract_route / extract_price 识别
    - 若没写货物种类，默认按 大板 处理
    """
    line = normalize_text(line)
    line = preprocess_line(line)

    if not line or is_invalid_line(line):
        return None

    route, err = extract_route(line, default_origin)
    if not route:
        return None

    origin, dest_raw, dest_city, rest = route

    price_info = extract_price(rest)
    if not price_info:
        return None

    cargo = extract_cargo(rest)
    if cargo is None:
        cargo = "大板"

    return f"{origin}到{dest_raw} {cargo} {price_info['price_text']}"


def auto_format_input_file(
    raw_file_path: str,
    formatted_output_file: str = FORMATTED_INPUT_FILE,
    default_origin: str = DEFAULT_ORIGIN
):
    """
    把原始 qq_chat.txt 自动整理成程序可识别格式，并输出到 FORMATTED_INPUT_FILE
    """
    if not os.path.exists(raw_file_path):
        print(f"未找到输入文件: {raw_file_path}")
        return raw_file_path

    output_lines = []
    current_date = None
    current_sender = "系统整理"
    current_time = "00:00:00"

    def append_freight_line(raw_freight_line: str, base_date_text: str) -> None:
        for effective_date, freight_text in expand_relative_freight_dates(
            raw_freight_line,
            base_date_text
        ):
            formatted = auto_format_freight_line(freight_text, default_origin)
            if not formatted:
                continue
            output_lines.append(f"{current_sender}: {effective_date} {current_time}")
            output_lines.append(formatted)

    with open(raw_file_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = normalize_text(raw_line)
            if not line:
                continue

            # 1. 原始 QQ 头
            header = parse_header(line)
            if header:
                current_date = header["date"]
                current_sender = header["sender"]
                current_time = header["time"]
                continue

            # 2. 日期块
            marker_date = parse_date_marker(line)
            if marker_date:
                current_date = marker_date
                current_time = "00:00:00"
                continue

            # 3. 行内日期
            inline_date = parse_inline_date_line(line)
            if inline_date:
                current_date, body = inline_date
                current_time = "00:00:00"
                append_freight_line(body, current_date)
                continue

            # 4. 普通货运行
            if current_date:
                append_freight_line(line, current_date)

    with open(formatted_output_file, "w", encoding="utf-8") as f:
        for line in output_lines:
            f.write(line + "\n")

    print(f"已自动整理输入文件：{formatted_output_file}")
    return formatted_output_file


# =========================
# 11. 单条消息解析
# =========================

def parse_freight_line(
    line: str,
    current_date: str,
    current_sender: str,
    default_origin: str = DEFAULT_ORIGIN
):
    if is_invalid_line(line):
        return None

    route, err = extract_route(line, default_origin)
    if not route:
        debug(f"跳过 | {err} | {line}")
        return None

    origin, dest_raw, dest_city, rest = route

    price_info = extract_price(rest)
    if not price_info:
        return None

    cargo = extract_cargo(rest)
    if cargo is None:
        return None

    is_full_truck_price = price_info["price_avg"] > PRICE_THRESHOLD

    return {
        "日期": current_date,
        "发布人": current_sender,
        "始发地": origin,
        "目的地": dest_raw,
        "目的城市": dest_city,
        "货物小类": cargo,
        "货物大类": "板材",
        "报价文本": price_info["price_text"],
        "最低报价": price_info["price_min"],
        "最高报价": price_info["price_max"],
        "平均报价": price_info["price_avg"],
        "是否整车价": "是" if is_full_truck_price else "否",
        "原始消息": line
    }


# =========================
# 12. 读取聊天文件
# =========================

def parse_chat_file(file_path: str, default_origin: str = DEFAULT_ORIGIN):
    if not os.path.exists(file_path):
        print(f"未找到输入文件: {file_path}")
        return []

    records = []
    current_sender = None
    current_date = None

    with open(file_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = normalize_text(raw_line)
            if not line:
                continue

            header = parse_header(line)
            if header:
                current_sender = header["sender"]
                current_date = header["date"]
                continue

            if not current_date:
                continue

            rec = parse_freight_line(line, current_date, current_sender, default_origin)
            if rec:
                records.append(rec)

    return records


# =========================
# 13. 同日同线路报价去重
# =========================


def normalize_location_for_dedup(value: str) -> str:
    """统一地点写法，消除空格及“市/区/县/镇”等行政区后缀差异。"""
    text = re.sub(r"\s+", "", str(value or ""))
    for suffix in ["省", "市", "区", "县", "镇"]:
        text = text.replace(suffix, "")
    return text


def build_destination_dedup_key(record: dict) -> tuple[str, str]:
    """把“佛山南海”和“南海”等同一目的地写法归为同一个键。"""
    city = normalize_location_for_dedup(record.get("目的城市", ""))
    destination = normalize_location_for_dedup(record.get("目的地", ""))
    if city and destination.startswith(city):
        destination = destination[len(city):]
    return city, destination


def build_price_dedup_key(record: dict) -> tuple[float, ...]:
    """按完整报价数字查重，并兼容 120/125、120-125 等不同分隔写法。"""
    numbers = re.findall(r"\d+(?:\.\d+)?", str(record.get("报价文本", "")))
    if numbers:
        return tuple(sorted(round(float(number), DECIMAL_PLACES) for number in numbers))

    # 兼容缺少原始报价文本的旧记录。
    fallback = [
        record.get("最低报价"),
        record.get("最高报价"),
        record.get("平均报价")
    ]
    return tuple(round(float(value), DECIMAL_PLACES) for value in fallback if value is not None)


def build_record_content_dedup_key(record: dict) -> str:
    """正式数据和未来缓存共用同一内容查重键，发布人不参与。"""
    normalized_origin = normalize_origin(record.get("始发地", ""))
    if not normalized_origin:
        normalized_origin = normalize_location_for_dedup(record.get("始发地", ""))
    key_payload = {
        "date": str(record.get("日期", "")),
        "origin": normalized_origin,
        "destination": build_destination_dedup_key(record),
        "cargo": str(record.get("货物小类", "")).strip(),
        "prices": build_price_dedup_key(record),
    }
    serialized = json.dumps(key_payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def deduplicate_records(records: list[dict]) -> list[dict]:
    """
    去重规则：
    日期 + 标准化始发地 + 具体目的地 + 货物小类 + 完整报价数字

    发布人不参与查重；同一天不同人转发相同货运信息时，只保留最先出现的一条。
    """
    unique_map = {}
    for r in records:
        key = build_record_content_dedup_key(r)
        if key not in unique_map:
            unique_map[key] = r

    return list(unique_map.values())


# =========================
# 14. 当次每日平均运价
# =========================

def summarize_daily_quote(records):
    """
    新逻辑：
    大板/红板/拼板 统一算作 板材
    按 日期 + 始发地 + 目的城市 分组
    整车价 (>1000) 不参与统计
    """
    groups = defaultdict(list)

    for r in records:
        if r["平均报价"] > PRICE_THRESHOLD:
            continue
        key = (r["日期"], r["始发地"], r["目的城市"])
        groups[key].append(r["平均报价"])

    result = []
    for (date, origin, city), prices in groups.items():
        result.append({
            "日期": date,
            "始发地": origin,
            "目的城市": city,
            "货物": "板材",
            "平均运价": round(mean(prices), DECIMAL_PLACES),
            "最低运价": round(min(prices), DECIMAL_PLACES),
            "最高运价": round(max(prices), DECIMAL_PLACES),
            "报价数量": len(prices)
        })

    result.sort(key=lambda x: (
        datetime.strptime(x["日期"], "%Y-%m-%d"),
        x["始发地"],
        x["目的城市"]
    ))
    return result


# =========================
# 15. 增加历史指标
# =========================

def enrich_history_indicators(history_df):
    if history_df is None or history_df.empty:
        return history_df

    df = history_df.copy()
    df["日期排序"] = pd.to_datetime(df["日期"], format="%Y-%m-%d", errors="coerce")

    result_list = []

    if "始发地" not in df.columns:
        df["始发地"] = "历史未区分"

    for (origin, city), sub in df.groupby(["始发地", "目的城市"]):
        sub = sub.sort_values("日期排序").copy()
        sub["7日均线"] = sub["平均运价"].rolling(window=7, min_periods=1).mean().round(DECIMAL_PLACES)

        first_price = sub["平均运价"].iloc[0]
        if first_price and first_price != 0:
            sub["价格指数"] = (sub["平均运价"] / first_price * 100).round(DECIMAL_PLACES)
        else:
            sub["价格指数"] = 100.00

        result_list.append(sub)

    result = pd.concat(result_list, ignore_index=True)
    result = result.drop(columns=["日期排序"])
    return result


# =========================
# 16. 历史日均价合并
# =========================

def merge_daily_history(new_summary):
    new_df = pd.DataFrame(new_summary)
    base_columns = [
        "日期", "始发地", "目的城市", "货物", "平均运价", "最低运价", "最高运价", "报价数量"
    ]

    if new_df.empty:
        if not os.path.exists(HISTORY_DAILY_CSV):
            empty_df = pd.DataFrame(columns=base_columns)
            empty_df = enrich_history_indicators(empty_df)
            empty_df.to_csv(
                HISTORY_DAILY_CSV,
                index=False,
                encoding="utf-8-sig",
                float_format=CSV_FLOAT_FORMAT
            )
        history_df = pd.read_csv(HISTORY_DAILY_CSV)
        if "始发地" not in history_df.columns:
            history_df["始发地"] = "历史未区分"
        return enrich_history_indicators(history_df)

    if os.path.exists(HISTORY_DAILY_CSV):
        old_df = pd.read_csv(HISTORY_DAILY_CSV)
        if "始发地" not in old_df.columns:
            old_df["始发地"] = "历史未区分"
        for column in base_columns:
            if column not in old_df.columns:
                old_df[column] = None
        old_df = old_df[base_columns]
    else:
        old_df = pd.DataFrame(columns=base_columns)

    merged = pd.concat([old_df, new_df], ignore_index=True)
    merged = merged.drop_duplicates(subset=["日期", "始发地", "目的城市"], keep="last")

    merged["日期排序"] = pd.to_datetime(merged["日期"], format="%Y-%m-%d", errors="coerce")
    merged = merged.sort_values(["日期排序", "始发地", "目的城市"]).drop(columns=["日期排序"])

    merged = enrich_history_indicators(merged)
    merged.to_csv(
        HISTORY_DAILY_CSV,
        index=False,
        encoding="utf-8-sig",
        float_format=CSV_FLOAT_FORMAT
    )
    print(f"已更新历史日均价文件: {HISTORY_DAILY_CSV}")

    return merged


# =========================
# 17. 部署日起的周、月、年统计
# =========================

def add_months(month_date: date, months: int) -> date:
    """在自然月层面移动月份，返回目标月 1 日。"""
    month_index = month_date.year * 12 + (month_date.month - 1) + months
    year, zero_based_month = divmod(month_index, 12)
    return date(year, zero_based_month + 1, 1)


def get_month_end(month_start: date) -> date:
    last_day = calendar.monthrange(month_start.year, month_start.month)[1]
    return date(month_start.year, month_start.month, last_day)


def get_or_create_observation_start(
    config_file: str = OBSERVATION_CONFIG_FILE,
    default_date: date | None = None
) -> date:
    """首次部署时固定观察起点，后续重建不随运行日期漂移。"""
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = json.load(f)
            return datetime.strptime(
                str(config["observation_start_date"]), "%Y-%m-%d"
            ).date()
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"观察周期配置无效: {config_file}；"
                "请确认 observation_start_date 使用 YYYY-MM-DD 格式。"
            ) from exc

    observation_start = default_date or trusted_today()
    config_dir = os.path.dirname(os.path.abspath(config_file))
    os.makedirs(config_dir, exist_ok=True)
    temp_file = config_file + ".tmp"
    config = {
        "observation_start_date": observation_start.isoformat(),
        "week_rule": "从观察起点每7个自然日为一周",
        "month_rule": "按自然月，首月不足整月也计入",
        "year_rule": "从部署所在月起连续12个观察月为一年"
    }
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, config_file)
    print(f"已建立运价观察周期，起始日期: {observation_start}")
    return observation_start


def prepare_observation_daily_data(
    history_df: pd.DataFrame,
    observation_start: date,
    as_of_date: date
) -> pd.DataFrame:
    if history_df is None or history_df.empty:
        return pd.DataFrame()

    df = history_df.copy()
    if "始发地" not in df.columns:
        df["始发地"] = "历史未区分"
    if "报价数量" not in df.columns:
        df["报价数量"] = 0

    df["日期排序"] = pd.to_datetime(df["日期"], errors="coerce")
    df = df.dropna(subset=["日期排序", "平均运价"])
    start_timestamp = pd.Timestamp(observation_start)
    end_timestamp = pd.Timestamp(as_of_date)
    df = df[(df["日期排序"] >= start_timestamp) & (df["日期排序"] <= end_timestamp)]

    for column in ["平均运价", "最低运价", "最高运价", "报价数量"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["平均运价", "最低运价", "最高运价"])
    df["报价数量"] = df["报价数量"].fillna(0)
    return df


def aggregate_weekly_statistics(
    daily_df: pd.DataFrame,
    observation_start: date,
    as_of_date: date
) -> pd.DataFrame:
    """按部署日起每 7 个自然日聚合，只输出已走满 7 天的周。"""
    if daily_df.empty:
        return pd.DataFrame(columns=WEEKLY_COLUMNS)

    df = daily_df.copy()
    start_timestamp = pd.Timestamp(observation_start)
    df["周序号"] = ((df["日期排序"] - start_timestamp).dt.days // 7 + 1).astype(int)

    rows = []
    group_columns = ["周序号", "始发地", "目的城市"]
    for (week_number, origin, city), sub in df.groupby(group_columns):
        period_start = observation_start + timedelta(days=(int(week_number) - 1) * 7)
        period_end = period_start + timedelta(days=6)
        if period_end > as_of_date:
            continue

        data_days = int(sub["日期排序"].dt.date.nunique())
        rows.append({
            "周序号": int(week_number),
            "周期开始": period_start.isoformat(),
            "周期结束": period_end.isoformat(),
            "始发地": origin,
            "目的城市": city,
            "货物": "板材",
            "平均运价": round(float(sub["平均运价"].mean()), DECIMAL_PLACES),
            "最低运价": round(float(sub["最低运价"].min()), DECIMAL_PLACES),
            "最高运价": round(float(sub["最高运价"].max()), DECIMAL_PLACES),
            "数据天数": data_days,
            "观察天数": 7,
            "数据覆盖率": round(data_days / 7 * 100, DECIMAL_PLACES),
            "报价数量": int(sub["报价数量"].sum()),
            "周期状态": "完整"
        })

    return pd.DataFrame(rows, columns=WEEKLY_COLUMNS).sort_values(
        ["周序号", "始发地", "目的城市"], ignore_index=True
    ) if rows else pd.DataFrame(columns=WEEKLY_COLUMNS)


def aggregate_monthly_statistics(
    daily_df: pd.DataFrame,
    observation_start: date,
    as_of_date: date
) -> pd.DataFrame:
    """按真实自然月聚合，首月和当前月不足整月也保留。"""
    if daily_df.empty:
        return pd.DataFrame(columns=MONTHLY_COLUMNS)

    df = daily_df.copy()
    start_month = date(observation_start.year, observation_start.month, 1)
    df["月份序号"] = (
        (df["日期排序"].dt.year - start_month.year) * 12
        + (df["日期排序"].dt.month - start_month.month)
        + 1
    ).astype(int)

    rows = []
    group_columns = ["月份序号", "始发地", "目的城市"]
    for (month_number, origin, city), sub in df.groupby(group_columns):
        month_start = add_months(start_month, int(month_number) - 1)
        month_end = get_month_end(month_start)
        actual_start = max(observation_start, month_start)
        actual_end = min(as_of_date, month_end)
        observation_days = (actual_end - actual_start).days + 1
        data_days = int(sub["日期排序"].dt.date.nunique())
        rows.append({
            "月份序号": int(month_number),
            "月份": month_start.strftime("%Y-%m"),
            "周期开始": month_start.isoformat(),
            "周期结束": month_end.isoformat(),
            "实际观察开始": actual_start.isoformat(),
            "实际观察结束": actual_end.isoformat(),
            "自然月天数": calendar.monthrange(month_start.year, month_start.month)[1],
            "观察天数": observation_days,
            "始发地": origin,
            "目的城市": city,
            "货物": "板材",
            "平均运价": round(float(sub["平均运价"].mean()), DECIMAL_PLACES),
            "最低运价": round(float(sub["最低运价"].min()), DECIMAL_PLACES),
            "最高运价": round(float(sub["最高运价"].max()), DECIMAL_PLACES),
            "数据天数": data_days,
            "数据覆盖率": round(data_days / observation_days * 100, DECIMAL_PLACES),
            "报价数量": int(sub["报价数量"].sum()),
            "周期状态": "完整" if as_of_date >= month_end else "进行中"
        })

    return pd.DataFrame(rows, columns=MONTHLY_COLUMNS).sort_values(
        ["月份序号", "始发地", "目的城市"], ignore_index=True
    ) if rows else pd.DataFrame(columns=MONTHLY_COLUMNS)


def aggregate_yearly_statistics(
    monthly_df: pd.DataFrame,
    observation_start: date,
    as_of_date: date
) -> pd.DataFrame:
    """每连续 12 个观察月形成一个观察年，年均价等权平均各月均价。"""
    if monthly_df.empty:
        return pd.DataFrame(columns=YEARLY_COLUMNS)

    df = monthly_df.copy()
    df["观察年序号"] = ((df["月份序号"] - 1) // 12 + 1).astype(int)
    start_month = date(observation_start.year, observation_start.month, 1)
    rows = []

    group_columns = ["观察年序号", "始发地", "目的城市"]
    for (year_number, origin, city), sub in df.groupby(group_columns):
        year_start_month = add_months(start_month, (int(year_number) - 1) * 12)
        year_end_month = add_months(year_start_month, 11)
        period_start = observation_start if int(year_number) == 1 else year_start_month
        period_end = get_month_end(year_end_month)
        actual_end = min(as_of_date, period_end)
        elapsed_months = min(
            12,
            (actual_end.year - year_start_month.year) * 12
            + actual_end.month - year_start_month.month + 1
        )
        data_months = int(sub["月份"].nunique())
        year_label = (
            f"第{int(year_number)}观察年 "
            f"({year_start_month:%Y-%m}至{year_end_month:%Y-%m})"
        )

        rows.append({
            "观察年序号": int(year_number),
            "观察年": year_label,
            "周期开始": period_start.isoformat(),
            "周期结束": period_end.isoformat(),
            "实际观察结束": actual_end.isoformat(),
            "始发地": origin,
            "目的城市": city,
            "货物": "板材",
            "平均运价": round(float(sub["平均运价"].mean()), DECIMAL_PLACES),
            "最低运价": round(float(sub["最低运价"].min()), DECIMAL_PLACES),
            "最高运价": round(float(sub["最高运价"].max()), DECIMAL_PLACES),
            "数据月份数": data_months,
            "已过月份数": elapsed_months,
            "月份覆盖率": round(data_months / elapsed_months * 100, DECIMAL_PLACES),
            "数据天数": int(sub["数据天数"].sum()),
            "报价数量": int(sub["报价数量"].sum()),
            "周期状态": "完整" if as_of_date >= period_end else "进行中"
        })

    return pd.DataFrame(rows, columns=YEARLY_COLUMNS).sort_values(
        ["观察年序号", "始发地", "目的城市"], ignore_index=True
    ) if rows else pd.DataFrame(columns=YEARLY_COLUMNS)


def build_period_statistics(
    history_df: pd.DataFrame,
    observation_start: date,
    as_of_date: date | None = None
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    current_date = as_of_date or trusted_today()
    daily_df = prepare_observation_daily_data(history_df, observation_start, current_date)
    weekly_df = aggregate_weekly_statistics(daily_df, observation_start, current_date)
    monthly_df = aggregate_monthly_statistics(daily_df, observation_start, current_date)
    yearly_df = aggregate_yearly_statistics(monthly_df, observation_start, current_date)
    return weekly_df, monthly_df, yearly_df


def save_period_csv(period_df: pd.DataFrame, filename: str, columns: list[str]) -> None:
    output_df = period_df.copy() if period_df is not None else pd.DataFrame(columns=columns)
    for column in columns:
        if column not in output_df.columns:
            output_df[column] = None
    output_df = output_df[columns]
    output_df.to_csv(
        filename,
        index=False,
        encoding="utf-8-sig",
        float_format=CSV_FLOAT_FORMAT
    )


# =========================
# 18. 保存 CSV
# =========================

def format_csv_row(row: dict) -> dict:
    """让价格结果在 CSV 中固定显示三位小数。"""
    formatted = row.copy()
    for column in PRICE_OUTPUT_COLUMNS:
        value = formatted.get(column)
        if value is None or value == "":
            continue
        try:
            formatted[column] = f"{float(value):.{DECIMAL_PLACES}f}"
        except (TypeError, ValueError):
            continue
    return formatted


def save_csv(data, filename):
    if not data:
        if filename in {DETAIL_CSV, PENDING_CSV}:
            headers = [
                "日期", "发布人", "始发地", "目的地", "目的城市", "货物小类", "货物大类",
                "报价文本", "最低报价", "最高报价", "平均报价", "是否整车价", "原始消息"
            ]
        else:
            headers = ["日期", "始发地", "目的城市", "货物", "平均运价", "最低运价", "最高运价", "报价数量"]

        with open(filename, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
        return

    fieldnames = list(data[0].keys())
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(format_csv_row(row) for row in data)


# =========================
# 18. 保存 Excel
# =========================

def auto_adjust_excel_width_and_date(filename):
    wb = load_workbook(filename)

    for ws in wb.worksheets:
        if ws["A1"].value == "日期":
            ws.column_dimensions["A"].width = 15

        header_columns = {
            cell.value: cell.column
            for cell in ws[1]
            if cell.value is not None
        }
        for header in PRICE_OUTPUT_COLUMNS:
            column_index = header_columns.get(header)
            if column_index is None:
                continue
            for row_index in range(2, ws.max_row + 1):
                ws.cell(row=row_index, column=column_index).number_format = EXCEL_DECIMAL_FORMAT

        for col in ws.columns:
            col_letter = col[0].column_letter
            max_length = 0
            for cell in col:
                value = "" if cell.value is None else str(cell.value)
                max_length = max(max_length, len(value))
            current_width = ws.column_dimensions[col_letter].width or 0
            ws.column_dimensions[col_letter].width = max(current_width, max_length + 2, 12)

    wb.save(filename)


def save_excel(
    detail_data,
    history_daily_data,
    weekly_data,
    monthly_data,
    yearly_data,
    filename,
    pending_data=None
):
    with pd.ExcelWriter(filename, engine="openpyxl") as writer:
        detail_df = pd.DataFrame(detail_data)
        history_df = pd.DataFrame(history_daily_data)
        weekly_df = pd.DataFrame(weekly_data)
        monthly_df = pd.DataFrame(monthly_data)
        yearly_df = pd.DataFrame(yearly_data)
        pending_df = pd.DataFrame(pending_data or [])

        if detail_df.empty:
            detail_df = pd.DataFrame(columns=[
                "日期", "发布人", "始发地", "目的地", "目的城市",
                "货物小类", "货物大类", "报价文本", "最低报价", "最高报价",
                "平均报价", "是否整车价", "原始消息"
            ])

        if history_df.empty:
            history_df = pd.DataFrame(columns=[
                "日期", "始发地", "目的城市", "货物", "平均运价", "最低运价", "最高运价", "报价数量", "7日均线", "价格指数"
            ])

        if pending_df.empty:
            pending_df = pd.DataFrame(columns=[
                "日期", "发布人", "始发地", "目的地", "目的城市",
                "货物小类", "货物大类", "报价文本", "最低报价", "最高报价",
                "平均报价", "是否整车价", "原始消息"
            ])

        detail_columns = [
            "日期", "发布人", "始发地", "目的地", "目的城市",
            "货物小类", "货物大类", "报价文本", "最低报价", "最高报价",
            "平均报价", "是否整车价", "原始消息"
        ]
        daily_columns = [
            "日期", "始发地", "目的城市", "货物", "平均运价", "最低运价", "最高运价", "报价数量", "7日均线", "价格指数"
        ]

        detail_df = detail_df[detail_columns]
        pending_df = pending_df[detail_columns]
        history_df = history_df[daily_columns]
        weekly_df = weekly_df.reindex(columns=WEEKLY_COLUMNS)
        monthly_df = monthly_df.reindex(columns=MONTHLY_COLUMNS)
        yearly_df = yearly_df.reindex(columns=YEARLY_COLUMNS)

        detail_df.to_excel(writer, index=False, sheet_name="报价明细")
        pending_df.to_excel(writer, index=False, sheet_name="待生效运价")
        history_df.to_excel(writer, index=False, sheet_name="历史每日平均运价")
        weekly_df.to_excel(writer, index=False, sheet_name="每周平均运价")
        monthly_df.to_excel(writer, index=False, sheet_name="每月平均运价")
        yearly_df.to_excel(writer, index=False, sheet_name="每年平均运价")

    auto_adjust_excel_width_and_date(filename)


def save_excel_resilient(
    detail_data,
    history_daily_data,
    weekly_data,
    monthly_data,
    yearly_data,
    filename,
    pending_data=None
) -> bool:
    """先生成临时Excel；原文件被打开时保存为待更新副本，不中断采集。"""
    absolute_filename = os.path.abspath(filename)
    directory = os.path.dirname(absolute_filename)
    base_name = os.path.splitext(os.path.basename(absolute_filename))[0]
    temp_file = os.path.join(directory, f".{base_name}_new.xlsx")
    pending_file = os.path.join(directory, PENDING_REPORT_XLSX)
    save_excel(
        detail_data,
        history_daily_data,
        weekly_data,
        monthly_data,
        yearly_data,
        temp_file,
        pending_data,
    )
    try:
        os.replace(temp_file, absolute_filename)
        if os.path.exists(pending_file):
            try:
                os.remove(pending_file)
            except OSError:
                pass
        return True
    except PermissionError:
        os.replace(temp_file, pending_file)
        print(
            f"警告：{filename} 正在被打开，已保存最新副本: {PENDING_REPORT_XLSX}；"
            "关闭原Excel后，下次更新会自动替换。"
        )
        if LOGGER:
            LOGGER.warning("Excel被占用，最新副本已保存到 %s", pending_file)
        return False


# =========================
# 19. 按始发地与目的城市画历史折线图
# =========================

def draw_destination_daily_charts(history_df):
    if history_df is None or history_df.empty:
        print("历史日均价为空，无法画图。")
        return

    plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    history_df = history_df.copy()
    history_df["日期排序"] = pd.to_datetime(history_df["日期"], format="%Y-%m-%d", errors="coerce")

    if "始发地" not in history_df.columns:
        history_df["始发地"] = "历史未区分"

    for (origin, city), sub in history_df.groupby(["始发地", "目的城市"]):
        sub = sub.copy()
        sub = sub.sort_values("日期排序")
        route_name = f"{origin}到{city}"
        safe_route_name = re.sub(r'[\\/:*?"<>|]', "_", route_name)

        plt.figure(figsize=(10, 5))
        plt.plot(sub["日期"], sub["平均运价"], marker="o", label="日均运价")
        if "7日均线" in sub.columns:
            plt.plot(sub["日期"], sub["7日均线"], marker="s", label="7日均线")
        plt.title(f"{route_name}-板材日均运价走势图")
        plt.xlabel("日期")
        plt.ylabel("平均运价")
        plt.legend()
        plt.xticks(rotation=45)
        plt.grid(True)
        plt.tight_layout()
        out_path = os.path.join(CHART_FOLDER, f"{safe_route_name}_板材_日均运价走势图.png")
        plt.savefig(out_path, dpi=150)
        plt.close()

        if "价格指数" in sub.columns:
            plt.figure(figsize=(10, 5))
            plt.plot(sub["日期"], sub["价格指数"], marker="o", label="价格指数")
            plt.title(f"{route_name}-板材价格指数走势图")
            plt.xlabel("日期")
            plt.ylabel("指数（首日=100）")
            plt.legend()
            plt.xticks(rotation=45)
            plt.grid(True)
            plt.tight_layout()
            out_path = os.path.join(CHART_FOLDER, f"{safe_route_name}_板材_价格指数走势图.png")
            plt.savefig(out_path, dpi=150)
            plt.close()

        print(f"已更新 {route_name} 的运价图和指数图")


def draw_route_period_chart(
    period_df: pd.DataFrame,
    x_column: str,
    label_column: str,
    period_name: str,
    filename_suffix: str
) -> None:
    if period_df is None or period_df.empty:
        print(f"{period_name}统计为空，暂不生成{period_name}走势图。")
        return

    for (origin, city), sub in period_df.groupby(["始发地", "目的城市"]):
        sub = sub.sort_values(x_column).copy()
        route_name = f"{origin}到{city}"
        safe_route_name = re.sub(r'[\\/:*?"<>|]', "_", route_name)
        if period_name == "周":
            labels = sub[label_column].map(lambda value: f"第{int(value)}周")
        elif period_name == "年":
            labels = sub[label_column].map(lambda value: f"第{int(value)}观察年")
        else:
            labels = sub[label_column].astype(str)
        if "周期状态" in sub.columns:
            labels = labels + sub["周期状态"].map(
                lambda status: "（进行中）" if status == "进行中" else ""
            )

        plt.figure(figsize=(11, 5.5))
        plt.plot(labels, sub["平均运价"], marker="o", label=f"{period_name}平均运价")
        plt.title(f"{route_name}-板材{period_name}价格走势图")
        plt.xlabel(period_name)
        plt.ylabel("平均运价")
        plt.legend()
        plt.xticks(rotation=45, ha="right")
        plt.grid(True)
        plt.tight_layout()
        out_path = os.path.join(
            CHART_FOLDER,
            f"{safe_route_name}_板材_{filename_suffix}.png"
        )
        plt.savefig(out_path, dpi=150)
        plt.close()
        print(f"已更新 {route_name} 的{period_name}价格走势图")


def draw_observation_year_monthly_charts(monthly_df: pd.DataFrame) -> None:
    """每个观察年以月份为横轴，并用虚线标出该观察年的月均价格。"""
    if monthly_df is None or monthly_df.empty:
        return

    df = monthly_df.copy()
    df["观察年序号"] = ((df["月份序号"] - 1) // 12 + 1).astype(int)
    for (origin, city, year_number), sub in df.groupby(
        ["始发地", "目的城市", "观察年序号"]
    ):
        sub = sub.sort_values("月份序号")
        route_name = f"{origin}到{city}"
        safe_route_name = re.sub(r'[\\/:*?"<>|]', "_", route_name)
        year_average = round(float(sub["平均运价"].mean()), DECIMAL_PLACES)

        plt.figure(figsize=(11, 5.5))
        plt.plot(sub["月份"], sub["平均运价"], marker="o", label="月平均运价")
        plt.axhline(
            year_average,
            color="orange",
            linestyle="--",
            label=f"观察年平均 {year_average:.{DECIMAL_PLACES}f}"
        )
        plt.title(f"{route_name}-第{int(year_number)}观察年月度价格走势")
        plt.xlabel("月份")
        plt.ylabel("平均运价")
        plt.legend()
        plt.xticks(rotation=45, ha="right")
        plt.grid(True)
        plt.tight_layout()
        out_path = os.path.join(
            CHART_FOLDER,
            f"{safe_route_name}_板材_第{int(year_number)}观察年_月度价格走势图.png"
        )
        plt.savefig(out_path, dpi=150)
        plt.close()


def draw_period_price_charts(
    weekly_df: pd.DataFrame,
    monthly_df: pd.DataFrame,
    yearly_df: pd.DataFrame
) -> None:
    plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    draw_route_period_chart(weekly_df, "周序号", "周序号", "周", "周价格走势图")
    draw_route_period_chart(monthly_df, "月份序号", "月份", "月", "月价格走势图")
    draw_route_period_chart(
        yearly_df,
        "观察年序号",
        "观察年序号",
        "年",
        "年价格走势图"
    )
    draw_observation_year_monthly_charts(monthly_df)


# =========================
# 20. 一次完整重建
# =========================

def rebuild_all(
    default_origin: str = DEFAULT_ORIGIN,
    as_of_date: date | None = None,
    database_file: str | None = None
) -> dict:
    print("\n检测到文件变化，开始重新统计...")
    delete_outputs_except_history()
    effective_as_of_date = as_of_date or trusted_today()

    if database_file:
        database = FreightDatabase(database_file)
        all_records = database.fetch_records()
        database.export_rejections_csv(REJECTED_CSV)
    else:
        formatted_file = auto_format_input_file(
            INPUT_FILE,
            FORMATTED_INPUT_FILE,
            default_origin
        )
        all_records = deduplicate_records(parse_chat_file(formatted_file, default_origin))
    records = []
    pending_records = []
    for record in all_records:
        try:
            record_date = datetime.strptime(record["日期"], "%Y-%m-%d").date()
        except (KeyError, TypeError, ValueError):
            continue
        if record_date <= effective_as_of_date:
            records.append(record)
        else:
            pending_records.append(record)

    daily_summary = summarize_daily_quote(records)

    save_csv(records, DETAIL_CSV)
    save_csv(pending_records, PENDING_CSV)
    save_csv(daily_summary, DAILY_CSV)

    history_df = merge_daily_history(daily_summary)
    observation_start = get_or_create_observation_start()
    weekly_df, monthly_df, yearly_df = build_period_statistics(
        history_df,
        observation_start,
        effective_as_of_date
    )

    save_period_csv(weekly_df, WEEKLY_CSV, WEEKLY_COLUMNS)
    save_period_csv(monthly_df, MONTHLY_CSV, MONTHLY_COLUMNS)
    save_period_csv(yearly_df, YEARLY_CSV, YEARLY_COLUMNS)
    excel_updated = save_excel_resilient(
        records,
        history_df.to_dict(orient="records"),
        weekly_df.to_dict(orient="records"),
        monthly_df.to_dict(orient="records"),
        yearly_df.to_dict(orient="records"),
        REPORT_XLSX,
        pending_records
    )

    draw_period_price_charts(weekly_df, monthly_df, yearly_df)

    print(f"纳入明细记录: {len(records)}")
    print(f"待生效缓存记录: {len(pending_records)}")
    print("已生成文件：")
    print(f"1. {DETAIL_CSV}")
    print(f"2. {PENDING_CSV}")
    print(f"3. {DAILY_CSV}")
    print(f"4. {HISTORY_DAILY_CSV}")
    print(f"5. {WEEKLY_CSV}")
    print(f"6. {MONTHLY_CSV}")
    print(f"7. {YEARLY_CSV}")
    print(f"8. {REPORT_XLSX}")
    print(f"9. {CHART_FOLDER} 文件夹中的周、月、年走势图")
    if database_file:
        print(f"10. {REJECTED_CSV}")
        print(f"11. {DATABASE_FILE}")

    rejected_count = 0
    if database_file:
        rejected_count = len(FreightDatabase(database_file).fetch_rejections())
    return {
        "active_records": len(records),
        "pending_records": len(pending_records),
        "rejected_records": rejected_count,
        "excel_updated": excel_updated,
        "as_of_date": effective_as_of_date.isoformat(),
    }


# =========================
# 21. QQ / OneBot 实时接入
# =========================

def load_qq_live_config(config_path: str, require_group_ids: bool = True) -> dict:
    """读取并校验 NapCatQQ / OneBot 11 的本地连接配置。"""
    if not os.path.exists(config_path):
        raise FileNotFoundError(
            f"未找到 QQ 实时配置文件: {config_path}\n"
            "请复制 qq_live_config.example.json 为 qq_live_config.json，并填写群号。"
        )

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    config_dir = os.path.dirname(os.path.abspath(config_path))

    def resolve_config_path(value: str, fallback: str = "") -> str:
        path = str(value or fallback).strip()
        if path and not os.path.isabs(path):
            path = os.path.join(config_dir, path)
        return os.path.abspath(path) if path else ""

    rules_file = resolve_config_path(config.get("rules_file"), RULES_CONFIG_FILE)
    load_rules_config(rules_file)

    ws_url = str(config.get("ws_url", "")).strip()
    if not re.match(r"^wss?://", ws_url, flags=re.IGNORECASE):
        raise ValueError("ws_url 必须以 ws:// 或 wss:// 开头。")

    raw_group_ids = config.get("group_ids", [])
    if not isinstance(raw_group_ids, list):
        raise ValueError("group_ids 必须是 QQ 群号列表。")

    group_ids = {str(group_id).strip() for group_id in raw_group_ids}
    group_ids.discard("")
    if any(not group_id.isdigit() for group_id in group_ids):
        raise ValueError("group_ids 中的 QQ 群号必须是纯数字。")
    if require_group_ids and not group_ids:
        raise ValueError("请在 group_ids 中填写至少一个 QQ 群号。")

    raw_group_names = config.get("group_names", {})
    if not isinstance(raw_group_names, dict):
        raise ValueError("group_names 必须是以群号为键、群名为值的对象。")
    group_names = {
        str(group_id).strip(): str(group_name).strip()
        for group_id, group_name in raw_group_names.items()
        if str(group_id).strip() in group_ids
    }

    raw_default_origins = config.get("group_default_origins", {})
    if not isinstance(raw_default_origins, dict):
        raise ValueError("group_default_origins 必须是以群号为键、始发地为值的对象。")
    group_default_origins = {}
    for group_id in group_ids:
        raw_origin = str(raw_default_origins.get(group_id, DEFAULT_ORIGIN)).strip()
        normalized_origin = normalize_origin(raw_origin)
        if not normalized_origin:
            raise ValueError(f"群 {group_id} 的默认始发地不在支持范围内: {raw_origin}")
        group_default_origins[group_id] = normalized_origin

    output_root = str(config.get("output_root", "QQ实时统计结果")).strip()
    if not output_root:
        raise ValueError("output_root 不能为空。")
    if not os.path.isabs(output_root):
        output_root = os.path.join(config_dir, output_root)
    output_root = os.path.abspath(output_root)

    try:
        reconnect_seconds = max(1.0, float(config.get("reconnect_seconds", 5)))
    except (TypeError, ValueError) as exc:
        raise ValueError("reconnect_seconds 必须是数字。") from exc

    dashboard = config.get("status_dashboard", {})
    if not isinstance(dashboard, dict):
        raise ValueError("status_dashboard 必须是对象。")
    time_check_urls = config.get("time_check_urls", [])
    if not isinstance(time_check_urls, list):
        raise ValueError("time_check_urls 必须是网址列表。")

    return {
        "config_path": os.path.abspath(config_path),
        "ws_url": ws_url,
        "access_token": str(config.get("access_token", "")).strip(),
        "group_ids": group_ids,
        "group_names": group_names,
        "group_default_origins": group_default_origins,
        "output_root": output_root,
        "rules_file": rules_file,
        "accept_self_messages": bool(config.get("accept_self_messages", False)),
        "ocr_enabled": bool(config.get("ocr_enabled", False)),
        "ocr_script": resolve_config_path(config.get("ocr_script")),
        "dashboard_enabled": bool(dashboard.get("enabled", True)),
        "dashboard_host": str(dashboard.get("host", "127.0.0.1")),
        "dashboard_port": int(dashboard.get("port", 8765)),
        "backup_retention_days": max(1, int(config.get("backup_retention_days", 30))),
        "time_check_urls": [str(value) for value in time_check_urls],
        "reconnect_seconds": reconnect_seconds,
        "rebuild_on_start": bool(config.get("rebuild_on_start", True))
    }


def safe_folder_component(value: str) -> str:
    """生成可读且适用于 Windows 的单级目录名。"""
    safe_value = re.sub(r'[\\/:*?"<>|]', "_", str(value or ""))
    safe_value = safe_value.strip().rstrip(".")
    return safe_value or "QQ群"


def build_qq_group_profiles(config: dict) -> dict[str, dict]:
    """为每个允许群建立独立输入、状态和统计输出目录。"""
    profiles = {}
    for group_id in sorted(config["group_ids"]):
        group_name = config["group_names"].get(group_id) or f"QQ群_{group_id}"
        folder_name = f"{safe_folder_component(group_name)}_{group_id}"
        output_dir = os.path.join(config["output_root"], folder_name)
        profiles[group_id] = {
            "group_id": group_id,
            "group_name": group_name,
            "default_origin": config["group_default_origins"][group_id],
            "output_dir": output_dir,
            "input_file": os.path.join(output_dir, INPUT_FILE),
            "state_file": os.path.join(output_dir, QQ_LIVE_STATE_FILE),
            "database_file": os.path.join(output_dir, DATABASE_FILE),
            "rejected_file": os.path.join(output_dir, REJECTED_CSV),
            "backup_retention_days": int(config.get("backup_retention_days", 30)),
            "backup_enabled": "backup_retention_days" in config,
            "rules_file": config.get("rules_file", ""),
            "config_path": config.get("config_path", "")
        }
    return profiles


def initialize_group_database(profile: dict) -> FreightDatabase:
    """首次升级时把已有TXT解析结果迁移进SQLite；之后只做增量写入。"""
    database = FreightDatabase(profile["database_file"])
    if database.count_records() > 0:
        return database
    if not os.path.exists(profile["input_file"]) or os.path.getsize(profile["input_file"]) == 0:
        return database

    previous_directory = os.getcwd()
    try:
        os.chdir(profile["output_dir"])
        formatted_file = auto_format_input_file(
            INPUT_FILE,
            FORMATTED_INPUT_FILE,
            profile["default_origin"],
        )
        records = deduplicate_records(
            parse_chat_file(formatted_file, profile["default_origin"])
        )
        keyed_records = [
            (build_record_content_dedup_key(record), record)
            for record in records
        ]
        database.insert_records(keyed_records, source="txt_migration")
    finally:
        os.chdir(previous_directory)
    return database


def rebuild_qq_group_output(profile: dict) -> dict:
    """在目标群目录中运行原有统计流程，并保证结束后恢复工作目录。"""
    output_dir = profile["output_dir"]
    os.makedirs(output_dir, exist_ok=True)
    initialize_group_database(profile)
    previous_directory = os.getcwd()
    try:
        os.chdir(output_dir)
        summary = rebuild_all(
            profile["default_origin"],
            database_file=profile["database_file"],
        )
    finally:
        os.chdir(previous_directory)
    if profile.get("backup_enabled"):
        create_daily_backup(
            output_dir,
            profile["database_file"],
            extra_files=[
                profile["input_file"],
                os.path.join(output_dir, HISTORY_DAILY_CSV),
                os.path.join(output_dir, REPORT_XLSX),
                profile.get("rules_file", ""),
                profile.get("config_path", ""),
            ],
            retention_days=profile.get("backup_retention_days", 30),
            backup_date=trusted_today(),
        )
    return summary


def extract_onebot_plain_text(event: dict) -> str:
    """从 OneBot 字符串或消息段数组中提取纯文本，不把图片等 CQ 码当作运价。"""
    message = event.get("message")
    text_parts = []

    if isinstance(message, list):
        for segment in message:
            if not isinstance(segment, dict) or segment.get("type") != "text":
                continue
            data = segment.get("data") or {}
            text_parts.append(str(data.get("text", "")))
    elif isinstance(message, str):
        text_parts.append(message)

    if not text_parts:
        text_parts.append(str(event.get("raw_message", "")))

    text = "".join(text_parts)
    text = re.sub(r"\[CQ:[^\]]+\]", " ", text)
    return text.strip()


def extract_onebot_image_sources(event: dict) -> list[str]:
    message = event.get("message")
    if not isinstance(message, list):
        return []
    sources = []
    for segment in message:
        if not isinstance(segment, dict) or segment.get("type") != "image":
            continue
        data = segment.get("data") or {}
        source = data.get("url") or data.get("file") or data.get("path")
        if source and str(source) not in sources:
            sources.append(str(source))
    return sources


def build_onebot_message_key(event: dict, message_text: str) -> str:
    """优先使用 NapCat 消息 ID；缺失时使用稳定摘要。"""
    group_id = str(event.get("group_id", ""))
    message_id = str(event.get("message_id", "")).strip()
    if message_id:
        return f"{group_id}:{message_id}"

    fallback = {
        "group_id": group_id,
        "user_id": str(event.get("user_id", "")),
        "time": str(event.get("time", "")),
        "text": message_text
    }
    payload = json.dumps(fallback, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_seen_qq_message_keys(state_file: str) -> list[str]:
    if not os.path.exists(state_file):
        return []

    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        keys = data.get("seen_message_keys", [])
        if isinstance(keys, list):
            return [str(key) for key in keys[-MAX_SEEN_QQ_MESSAGES:]]
    except (OSError, json.JSONDecodeError, TypeError):
        print(f"警告：状态文件损坏，将重新建立: {state_file}")
    return []


def save_seen_qq_message_keys(state_file: str, keys: list[str]) -> None:
    """原子更新状态文件，避免程序中断后留下半个 JSON。"""
    state_dir = os.path.dirname(os.path.abspath(state_file))
    os.makedirs(state_dir, exist_ok=True)
    temp_file = state_file + ".tmp"
    data = {"seen_message_keys": keys[-MAX_SEEN_QQ_MESSAGES:]}
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, state_file)


class OneBotFreightIngestor:
    """把允许群消息增量写入SQLite；内容查重同时覆盖正式和未来记录。"""

    def __init__(
        self,
        group_profiles: dict[str, dict],
        accept_self_messages: bool = False,
        ocr: WindowsOcr | None = None,
        logger=None,
    ) -> None:
        self.group_profiles = group_profiles
        self.accept_self_messages = accept_self_messages
        self.ocr = ocr
        self.logger = logger
        self.databases = {}
        for group_id, profile in group_profiles.items():
            os.makedirs(profile["output_dir"], exist_ok=True)
            self.databases[group_id] = initialize_group_database(profile)

    def _record_rejection(
        self,
        database: FreightDatabase,
        message_key: str,
        group_id: str,
        sender: str,
        message_time: datetime,
        reason: str,
        message_text: str,
        media_source: str = "",
    ) -> None:
        payload = f"{message_key}|{reason}|{message_text}|{media_source}"
        rejection_key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        database.add_rejection(
            rejection_key,
            message_key,
            group_id,
            sender,
            message_time.isoformat(timespec="seconds"),
            reason,
            message_text,
            media_source,
        )

    def ingest(self, event: dict) -> dict:
        if event.get("post_type") != "message" or event.get("message_type") != "group":
            return {"status": "ignored", "reason": "不是群消息"}

        group_id = str(event.get("group_id", ""))
        if group_id not in self.group_profiles:
            return {"status": "ignored", "reason": "群号不在允许列表"}
        profile = self.group_profiles[group_id]
        database = self.databases[group_id]

        if not self.accept_self_messages:
            self_id = str(event.get("self_id", "")).strip()
            user_id = str(event.get("user_id", "")).strip()
            if self_id and user_id and self_id == user_id:
                return {"status": "ignored", "reason": "登录账号自己的消息已关闭"}

        event_time = event.get("time", time.time())
        try:
            message_time = datetime.fromtimestamp(float(event_time))
        except (TypeError, ValueError, OSError):
            message_time = CLOCK.now().replace(tzinfo=None)

        sender_info = event.get("sender") or {}
        sender = (
            sender_info.get("card")
            or sender_info.get("nickname")
            or event.get("user_id")
            or "QQ用户"
        )
        sender = re.sub(r"[:\r\n]", "_", str(sender)).strip() or "QQ用户"

        plain_text = extract_onebot_plain_text(event)
        image_sources = extract_onebot_image_sources(event)
        ocr_texts = []
        ocr_errors = []
        if image_sources and self.ocr:
            for source in image_sources:
                text, error = self.ocr.recognize(source)
                if text:
                    ocr_texts.append(text)
                elif error:
                    ocr_errors.append((source, error))
        combined_text = "\n".join(value for value in [plain_text, *ocr_texts] if value).strip()
        key_material = combined_text or "|".join(image_sources)
        message_key = build_onebot_message_key(event, key_material)
        if database.has_processed_message(message_key):
            return {"status": "duplicate", "group_id": group_id}

        for source, error in ocr_errors:
            self._record_rejection(
                database, message_key, group_id, sender, message_time,
                f"图片OCR失败: {error}", plain_text, source,
            )

        if not combined_text:
            self._record_rejection(
                database, message_key, group_id, sender, message_time,
                "没有可识别的文字或图片OCR文本", "", "|".join(image_sources),
            )
            database.mark_processed_message(message_key)
            return {
                "status": "rejected",
                "reason": "没有可识别文字",
                "group_id": group_id,
                "group_name": profile["group_name"],
                "sender": sender,
            }

        parsed_records = []
        raw_valid_lines = []
        rejected_lines = []
        for raw_line in combined_text.splitlines():
            line = normalize_text(raw_line)
            if not line:
                continue
            line_records = []
            for effective_date, freight_text in expand_relative_freight_dates(
                line,
                message_time.date().isoformat(),
            ):
                formatted = auto_format_freight_line(
                    freight_text,
                    profile["default_origin"],
                )
                if not formatted:
                    continue
                record = parse_freight_line(
                    formatted,
                    effective_date,
                    sender,
                    profile["default_origin"],
                )
                if record:
                    line_records.append(record)
            if line_records:
                raw_valid_lines.append(line)
                parsed_records.extend(line_records)
            else:
                rejected_lines.append(line)

        for line in rejected_lines:
            self._record_rejection(
                database, message_key, group_id, sender, message_time,
                "不是可识别的运价信息", line, "|".join(image_sources),
            )

        unique_records = {}
        for record in parsed_records:
            unique_records.setdefault(build_record_content_dedup_key(record), record)
        keyed_records = list(unique_records.items())
        existing_keys = database.existing_record_keys(list(unique_records))
        pending_inserted = sum(
            1
            for dedup_key, record in keyed_records
            if dedup_key not in existing_keys
            and datetime.strptime(record["日期"], "%Y-%m-%d").date() > trusted_today()
        )
        inserted, duplicate_content = database.insert_records(
            keyed_records,
            message_key=message_key,
            source="qq_ocr" if ocr_texts else "qq_text",
        )
        database.mark_processed_message(message_key)

        if inserted > 0:
            input_file = profile["input_file"]
            os.makedirs(os.path.dirname(os.path.abspath(input_file)), exist_ok=True)
            needs_separator = os.path.exists(input_file) and os.path.getsize(input_file) > 0
            with open(input_file, "a", encoding="utf-8") as stream:
                if needs_separator:
                    stream.write("\n")
                stream.write(f"{sender}: {message_time:%Y-%m-%d %H:%M:%S}\n")
                for line in raw_valid_lines:
                    stream.write(line + "\n")

        if inserted == 0 and duplicate_content > 0:
            status = "duplicate_content"
        elif inserted > 0:
            status = "added"
        else:
            status = "rejected"

        return {
            "status": status,
            "group_id": group_id,
            "group_name": profile["group_name"],
            "default_origin": profile["default_origin"],
            "output_dir": profile["output_dir"],
            "sender": sender,
            "line_count": len(parsed_records),
            "inserted_count": inserted,
            "duplicate_content_count": duplicate_content,
            "pending_count": pending_inserted,
            "ocr_used": bool(ocr_texts),
        }


def run_qq_live(config_path: str) -> None:
    """连接 NapCat 正向 WebSocket，实时接收指定群的运价信息。"""
    global LOGGER
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError(
            "缺少 websocket-client，请按项目文档安装 requirements.txt。"
        ) from exc

    config = load_qq_live_config(config_path, require_group_ids=False)
    group_profiles = build_qq_group_profiles(config)
    LOGGER = configure_logging(os.path.join(config["output_root"], "logs"), "freight-live")
    clock_status = CLOCK.sync(config.get("time_check_urls"))
    LOGGER.info("启动联网校时: %s", clock_status)
    runtime_status = RuntimeStatus(config["output_root"])
    runtime_status.update(clock=clock_status, connection="starting")
    dashboard_server = None
    if config["dashboard_enabled"]:
        configuration_manager = FreightConfigurationManager(
            config["config_path"],
            config["rules_file"],
            validate_callback=lambda: load_qq_live_config(config_path),
            restart_callback=lambda: os._exit(0),
            logger=LOGGER,
        )
        dashboard_server = start_status_dashboard(
            runtime_status,
            config["dashboard_host"],
            config["dashboard_port"],
            LOGGER,
            configuration_manager,
        )
    if not config["group_ids"]:
        runtime_status.update(connection="setup_required")
        LOGGER.info("尚未配置QQ群，等待用户在管理页面完成首次设置。")
        print("尚未配置QQ群，请打开管理页面完成首次设置。")
        while True:
            try:
                time.sleep(5)
            except KeyboardInterrupt:
                runtime_status.update(connection="stopped")
                return
    ocr = WindowsOcr(config["ocr_script"], config["ocr_enabled"])
    ingestor = OneBotFreightIngestor(
        group_profiles,
        accept_self_messages=config["accept_self_messages"],
        ocr=ocr,
        logger=LOGGER,
    )

    print("QQ 实时提取已启动")
    print(f"监听群数量: {len(config['group_ids'])}")
    print(f"OneBot 地址: {config['ws_url']}")
    print(f"独立输出根目录: {config['output_root']}")
    print(
        f"程序可信时间: {CLOCK.now():%Y-%m-%d %H:%M:%S} "
        f"({clock_status['method']})"
    )
    if dashboard_server:
        print(
            f"状态面板: http://{config['dashboard_host']}:"
            f"{config['dashboard_port']}"
        )
    for profile in group_profiles.values():
        print(
            f"- {profile['group_name']} ({profile['group_id']}) -> "
            f"{profile['output_dir']}，默认始发地: {profile['default_origin']}"
        )
    print("支持文字和Windows图片OCR；未识别消息会单独保存；按 Ctrl+C 停止。")

    if config["rebuild_on_start"]:
        for profile in group_profiles.values():
            os.makedirs(profile["output_dir"], exist_ok=True)
            if not os.path.exists(profile["input_file"]):
                with open(profile["input_file"], "a", encoding="utf-8"):
                    pass
            summary = rebuild_qq_group_output(profile)
            runtime_status.update_group(
                profile["group_id"],
                name=profile["group_name"],
                output_dir=profile["output_dir"],
                **summary,
            )

    last_calendar_date = trusted_today()

    while True:
        ws = None
        try:
            headers = []
            if config["access_token"]:
                headers.append(f"Authorization: Bearer {config['access_token']}")

            ws = websocket.create_connection(
                config["ws_url"],
                header=headers,
                timeout=15
            )
            ws.settimeout(60)
            print("已连接 NapCat，等待群消息...")
            LOGGER.info("已连接NapCat: %s", config["ws_url"])
            runtime_status.update(connection="connected")
            runtime_status.clear_errors()

            while True:
                current_calendar_date = trusted_today()
                if current_calendar_date != last_calendar_date:
                    print(
                        f"检测到日期变化: {last_calendar_date} -> {current_calendar_date}，"
                        "开始转入到期缓存。"
                    )
                    for profile in group_profiles.values():
                        summary = rebuild_qq_group_output(profile)
                        runtime_status.update_group(
                            profile["group_id"],
                            name=profile["group_name"],
                            **summary,
                        )
                    last_calendar_date = current_calendar_date

                try:
                    raw_event = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue

                if not raw_event:
                    raise ConnectionError("NapCat 已关闭连接。")

                try:
                    event = json.loads(raw_event)
                except (json.JSONDecodeError, TypeError):
                    continue

                result = ingestor.ingest(event)
                if result["status"] in {"ignored", "duplicate"}:
                    continue
                target_profile = group_profiles.get(result.get("group_id"))
                if result["status"] == "added":
                    print(
                        f"收到群 {result['group_name']} ({result['group_id']}) 的运价信息，"
                        f"发布人: {result['sender']}，新增: {result['inserted_count']}，"
                        f"缓存新增: {result['pending_count']}"
                    )
                    LOGGER.info("运价消息: %s", result)
                elif result["status"] == "duplicate_content":
                    LOGGER.info("内容查重已拦截: %s", result)
                else:
                    LOGGER.info("消息进入未识别队列: %s", result)
                if target_profile:
                    summary = rebuild_qq_group_output(target_profile)
                    runtime_status.update_group(
                        target_profile["group_id"],
                        name=target_profile["group_name"],
                        last_message=CLOCK.now().isoformat(timespec="seconds"),
                        **summary,
                    )

        except KeyboardInterrupt:
            print("\nQQ 实时提取已停止。")
            runtime_status.update(connection="stopped")
            if ws is not None:
                ws.close()
            return
        except Exception as exc:
            print(
                f"QQ 实时连接异常: {exc}；"
                f"{config['reconnect_seconds']:g} 秒后重连。"
            )
            LOGGER.exception("QQ实时连接异常")
            runtime_status.update(connection="disconnected")
            runtime_status.add_error(str(exc))
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
            time.sleep(config["reconnect_seconds"])


def list_qq_groups(config_path: str) -> None:
    """通过 OneBot API 列出当前 QQ 账号加入的群，便于填写允许列表。"""
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError(
            "缺少 websocket-client，请按项目文档安装 requirements.txt。"
        ) from exc

    config = load_qq_live_config(config_path, require_group_ids=False)
    headers = []
    if config["access_token"]:
        headers.append(f"Authorization: Bearer {config['access_token']}")

    ws = None
    try:
        ws = websocket.create_connection(config["ws_url"], header=headers, timeout=15)
        echo = f"freight-group-list-{int(time.time())}"
        request = {"action": "get_group_list", "params": {}, "echo": echo}
        ws.send(json.dumps(request, ensure_ascii=False))

        while True:
            response = json.loads(ws.recv())
            if response.get("echo") != echo:
                continue
            if response.get("status") != "ok":
                raise RuntimeError(f"NapCat 获取群列表失败: {response.get('message', '未知错误')}")

            groups = response.get("data") or []
            print(f"共找到 {len(groups)} 个群：")
            for group in groups:
                print(f"{group.get('group_id')}\t{group.get('group_name', '')}")
            return
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"无法连接 NapCat: {exc}") from exc
    finally:
        if ws is not None:
            ws.close()


# =========================
# 22. 监控 txt 变化
# =========================

def watch_file(file_path):
    if not os.path.exists(file_path):
        print(f"未找到 {file_path}，请先创建该文件。")
        return

    last_mtime = os.path.getmtime(file_path)
    print(f"开始监控文件：{file_path}")
    print("当 txt 内容修改并保存后，会自动更新历史日均价、7日均线、价格指数和折线图。")
    print("按 Ctrl + C 可停止程序。")

    rebuild_all()
    create_daily_backup(
        os.getcwd(),
        "",
        extra_files=[INPUT_FILE, HISTORY_DAILY_CSV, REPORT_XLSX, RULES_CONFIG_FILE],
        retention_days=30,
        backup_date=trusted_today(),
    )
    last_calendar_date = trusted_today()

    while True:
        try:
            time.sleep(2)
            current_mtime = os.path.getmtime(file_path)
            current_calendar_date = trusted_today()
            if current_mtime != last_mtime or current_calendar_date != last_calendar_date:
                last_mtime = current_mtime
                rebuild_all()
                create_daily_backup(
                    os.getcwd(),
                    "",
                    extra_files=[INPUT_FILE, HISTORY_DAILY_CSV, REPORT_XLSX, RULES_CONFIG_FILE],
                    retention_days=30,
                    backup_date=trusted_today(),
                )
                last_calendar_date = current_calendar_date
        except KeyboardInterrupt:
            print("\n已停止监控。")
            break
        except Exception as e:
            print(f"监控时出错: {e}")
            time.sleep(2)


# =========================
# 23. 主程序
# =========================

def main() -> None:
    global LOGGER
    parser = argparse.ArgumentParser(description="物流运价统计与 QQ 实时提取")
    parser.add_argument(
        "--mode",
        choices=["file-watch", "file-once", "qq-groups", "qq-live"],
        default="file-watch",
        help="file-watch=监听文本；file-once=统计一次；qq-groups=列出群；qq-live=实时接入"
    )
    parser.add_argument(
        "--config",
        default=QQ_LIVE_CONFIG_FILE,
        help="qq-live 模式使用的 JSON 配置文件"
    )
    parser.add_argument(
        "--data-dir",
        default="",
        help="手动文本模式的数据输出目录"
    )
    args = parser.parse_args()

    app_dir = application_dir()
    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = os.path.join(app_dir, config_path)

    if args.mode not in {"qq-groups", "qq-live"}:
        data_dir = os.path.abspath(args.data_dir or os.path.join(app_dir, "手动文本统计"))
        os.makedirs(data_dir, exist_ok=True)
        os.chdir(data_dir)
        input_path = os.path.join(data_dir, INPUT_FILE)
        if not os.path.exists(input_path):
            with open(input_path, "a", encoding="utf-8"):
                pass
        load_rules_config(os.path.join(app_dir, RULES_CONFIG_FILE))
        LOGGER = configure_logging(os.path.join(data_dir, "logs"), "freight-manual")
        clock_status = CLOCK.sync()
        LOGGER.info("手动模式启动联网校时: %s", clock_status)

    if args.mode == "file-once":
        rebuild_all()
        create_daily_backup(
            data_dir,
            "",
            extra_files=[INPUT_FILE, HISTORY_DAILY_CSV, REPORT_XLSX, RULES_CONFIG_FILE],
            retention_days=30,
            backup_date=trusted_today(),
        )
    elif args.mode in {"qq-groups", "qq-live"}:
        try:
            if args.mode == "qq-groups":
                list_qq_groups(config_path)
            else:
                run_qq_live(config_path)
        except (FileNotFoundError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"无法启动 QQ 实时提取：{exc}")
            raise SystemExit(1) from None
    else:
        watch_file(INPUT_FILE)


if __name__ == "__main__":
    main()

