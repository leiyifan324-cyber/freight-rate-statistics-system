"""De-identified format regressions derived from local NapCat freight samples.

No group identifiers, senders, contacts, or live network/database access.
Routes are adapted to the existing configured scope for integration tests.
"""
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
import qq_freight_parser as freight


PRICE_CASES = [
    ('大板 115-120', '115-120', 117.5),
    ('大板 110-115', '110-115', 112.5),
    ('大板 200/220', '200/220', 210),
    ('大板 165／170', '165/170', 167.5),
    ('大板 １２０／１２５', '120/125', 122.5),
    ('大板 160 - 170', '160-170', 165),
    ('大板 160～170', '160-170', 165),
    ('大板 160—170', '160-170', 165),
    ('大板 110 35-36吨', '110', 110),
    ('大板140 33-34吨 装', '140', 140),
    ('155大板 36吨左', '155', 155),
    ('大板 34/35 吨 180', '180', 180),
    ('大板34-35吨左右 3400', '3400', 3400),
    ('大板245,34吨左右。13.75-17.5米都可以', '245', 245),
    ('大板13-13.75 165/175', '165/175', 170),
    ('大板165-170 13-14米', '165-170', 167.5),
    ('大板85 13／13.75', '85', 85),
    ('大板9.6 4600', '4600', 4600),
    ('4000-4100 9.6', '4000-4100', 4050),
    ('大板2装1卸距离3公里 3400-3500', '3400-3500', 3450),
    ('大板165 2装距离5公里', '165', 165),
    ('大板 120-125。2装', '120-125', 122.5),
    ('大板大吨位220/230', '220/230', 225),
    ('木糠 吨包17.5 100-105一吨', '100-105', 102.5),
    ('大板 120元/吨', '120', 120),
    ('大板 120.5/125.5', '120.5/125.5', 123),
    ('大板 120 13900000000', '120', 120),
]


def main():
    # Fixed historical scope: format recognition must not depend on a user's release preset.
    rules = ROOT/'tests'/'fixtures'/'recognition_rules.json'
    before_rules = rules.read_bytes()
    freight.load_rules_config(str(rules))
    checks = 0
    # Requirement changed: per-volume rates are now explicitly out of scope.
    parsed, reason = freight.extract_price_with_reason('大板 37-38元/方')
    assert parsed is None and '不采集' in reason, (parsed, reason)
    checks += 1
    for text, token, average in PRICE_CASES:
        parsed = freight.extract_price(text)
        assert parsed and parsed['price_text'] == token and parsed['price_avg'] == average, (text, parsed)
        days = freight.expand_relative_freight_dates('武鸣到佛山 '+text, '2026-09-08')
        assert len(days) == 1 and days[0][0] == '2026-09-08', (text,days)
        formatted, reason = freight.format_freight_line_with_reason(days[0][1], '贵港')
        assert formatted and token in formatted, (text, formatted, reason)
        checks += 3

    for text in ['大板 35-36吨', '大板 13-14米', '大板245加1', '大板150 160', '大板 13.13.75 140']:
        parsed, reason = freight.extract_price_with_reason(text)
        assert parsed is None and reason, (text, parsed, reason)
        checks += 1

    date_cases = [
        ('8/11武鸣到佛山大板120-125', '2026-08-11'),
        ('2026-09-09 武鸣到佛山大板120-125', '2026-09-09'),
        ('武鸣到佛山大板120 9/10装货', '2026-09-10'),
        ('武鸣到佛山大板120 9月10日装', '2026-09-10'),
        ('明天武鸣到佛山大板120/125 33-34吨', '2026-09-09'),
        ('武鸣到佛山大板120/125明天', '2026-09-09'),
    ]
    for text, day in date_cases:
        expanded = freight.expand_relative_freight_dates(text, '2026-09-08')
        assert expanded and expanded[0][0] == day, (text,expanded)
        checks += 1
    for text in ['22号武鸣到佛山大板120', '2026-02-30 武鸣到佛山大板120']:
        assert freight.expand_relative_freight_dates(text,'2026-09-08') == []
        checks += 1
    assert freight.auto_format_freight_line('武鸣红板到佛山120') == '武鸣到佛山 红板 120'
    assert freight.auto_format_freight_line('武鸣拼板两装到佛山120') == '武鸣到佛山 拼板 120'
    assert freight.auto_format_freight_line('武鸣到佛山大板120有车了') is None
    assert freight.auto_format_freight_line('八塘到佛山大板115-120') is None
    assert freight.auto_format_freight_line('武鸣到郴州大板115-120') is None
    checks += 5

    with tempfile.TemporaryDirectory(prefix='quote-format-') as temp:
        config = {'group_ids':{'10001'},'group_names':{'10001':'隔离回归'},
                  'group_default_origins':{'10001':'贵港'}, 'output_root':temp}
        profiles = freight.build_qq_group_profiles(config)
        ingestor = freight.OneBotFreightIngestor(profiles)
        event = {'post_type':'message','message_type':'group','group_id':10001,
                 'message_id':'local-only-1','time':datetime(2026,9,8,9).timestamp(),
                 'sender':{'nickname':'脱敏样本'},'message':[{'type':'text','data':{'text':
                     '武鸣到佛山大板110 35-36吨\n'
                     '东龙到武汉洪山4000-4100 9.6\n'
                     '今明武鸣到临沂兰山大板大吨位220/230\n'
                     '八塘到郴州大板115-120\n'
                     '22号武鸣到佛山大板150\n'
                     '武鸣到佛山大板245加1'}}]}
        result = ingestor.ingest(event)
        assert result['inserted_count'] == 4, result
        assert result['rejected_line_count'] == 3, result
        assert {r['平均报价'] for r in result['_inserted_records']} == {110,4050,225}, result
        assert any('日期无效' in r for r in result['rejected_reasons']), result
        assert any('加价' in r for r in result['rejected_reasons']), result
        assert ingestor.ingest(event)['status'] == 'duplicate'
        checks += 6
        # Existing file-import path must use the same date and price normalization.
        raw = Path(temp)/'input.txt'
        raw.write_text('测试: 2026-09-08 09:00:00\n武鸣到佛山大板115-120 35-36吨\n', encoding='utf-8')
        formatted_file = Path(temp)/'formatted.txt'
        freight.auto_format_input_file(str(raw),str(formatted_file),'贵港')
        parsed = freight.parse_chat_file(str(formatted_file))
        assert len(parsed) == 1 and parsed[0]['平均报价'] == 117.5, parsed
        checks += 1
    assert rules.read_bytes() == before_rules
    print(json.dumps({'checks':checks,'status':'passed','scope':'unchanged','live_data':'untouched'},ensure_ascii=False))


if __name__ == '__main__':
    main()
