"""Regression: negative numbers and per-volume quotes must never enter freight data."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import qq_freight_parser as freight

NEGATIVE_CASES = [
    '大板-987', '大板 运费=-987元/吨', '大板 - 987', '大板 −987',
    '大板﹣987', '大板－９８７', '大板负987', '大板120/-125',
    '大板-120/-125', '大板120--125', '大板 —987', '大板 –987',
]
VOLUME_CASES = [
    '大板37-38元/方', '大板每方40元', '大板每立方米40元',
    '大板40元/立方', '大板40元/立方米', '大板40元／m³',
    '大板40元/m3', '大板40元/m^3', '大板40元/㎥', '大板40元/CBM',
    '大板40/方', '大板40块/方', '大板40元每方', '大板一方40元',
    '大板40元一立方米', '大板方价40', '大板按方计费40', '大板按立方报价40',
]
VALID_CASES = [
    ('大板120', 120), ('大板120元/吨', 120), ('大板每吨120元', 120),
    ('大板120-125', 122.5), ('大板120 - 125', 122.5),
    ('大板120—125', 122.5), ('大板120/125', 122.5),
    ('大板120／125', 122.5), ('大板110 35-36吨', 110),
    ('大板13-13.75 165/175', 170), ('大板120元/吨 30方', 120),
    ('大板120元/吨 100立方米', 120), ('大板2400元/车', 2400),
]

def main() -> None:
    freight.load_rules_config(str(ROOT/'config/freight_rules.default.json'))
    count = 0
    for cases, reason in ((NEGATIVE_CASES, '负'), (VOLUME_CASES, '方')):
        for rest in cases:
            parsed, why = freight.extract_price_with_reason(rest)
            assert parsed is None and reason in why, (rest, parsed, why)
            full = '南宁到佛山' + rest
            formatted, why = freight.format_freight_line_with_reason(full, '南宁')
            assert formatted is None and reason in why, (full, formatted, why)
            assert freight.parse_freight_line(full, '2026-09-08', 'test') is None, full
            count += 3
    # Check before route extraction: it can otherwise discard a leading sign/unit.
    for full in ['南宁到佛山-987', '南宁到佛山负987', '每方40元 南宁到佛山大板',
                 '南宁到佛山（每方）大板40', '南宁到佛山 运费=-987',
                 '每立方米报价 南宁到佛山大板40']:
        assert freight.auto_format_freight_line(full) is None, full
        assert freight.parse_freight_line(full, '2026-09-08', 'test') is None, full
        count += 2
    for rest, expected in VALID_CASES:
        parsed = freight.extract_price(rest)
        assert parsed and parsed['price_avg'] == expected, (rest, parsed)
        result = freight.auto_format_freight_line('南宁到佛山'+rest)
        assert result, rest
        count += 2
    for value in ['-987', '120/-125', '120--125', '0/120', '0', '-120/-125']:
        assert freight.parse_price_text(value) is None, value
        count += 1
    assert freight.extract_price('木方120元/吨')['price_avg'] == 120
    assert freight.extract_price('木方价格120元/吨')['price_avg'] == 120
    assert freight.expand_relative_freight_dates('2026-09-08 南宁到佛山大板120-125', '2026-09-08') == [('2026-09-08', '南宁到佛山大板120-125')]
    count += 3
    with tempfile.TemporaryDirectory(prefix='freight-price-guards-') as directory:
        config = {'group_ids':{'1001'}, 'group_names':{'1001':'隔离测试'},
                  'group_default_origins':{'1001':'南宁'},'output_root':directory}
        profiles = freight.build_qq_group_profiles(config)
        ingestor = freight.OneBotFreightIngestor(profiles)
        message = {'post_type':'message','message_type':'group','group_id':1001,
                   'message_id':'guard-mixed','sender':{'nickname':'guard test'},
                   'message':[{'type':'text','data':{'text':
                       '南宁到佛山大板120元/吨\n南宁到佛山大板150元/吨\n'
                       '南宁到佛山大板37-38元/方\n南宁到佛山大板 运费=-987元/吨\n'
                       '每立方米40元 南宁到佛山大板'}}]}
        result = ingestor.ingest(message)
        assert result['inserted_count'] == 2 and result['rejected_line_count'] == 3, result
        records = ingestor.databases['1001'].fetch_records()
        assert {r['平均报价'] for r in records} == {120,150}, records
        assert freight.summarize_daily_quote(records)[0]['平均运价'] == 135
        assert len(ingestor.databases['1001'].fetch_rejections()) == 3
        source=Path(directory)/'input.txt'
        source.write_text('测试: 2026-09-08 09:00:00\n南宁到佛山大板120元/吨\n南宁到佛山大板40元/方\n南宁到佛山大板-987\n',encoding='utf-8')
        formatted=Path(directory)/'formatted.txt'
        freight.auto_format_input_file(str(source),str(formatted),'南宁')
        assert len(freight.parse_chat_file(str(formatted))) == 1
        assert len(freight.parse_chat_file(str(source))) == 1
        count += 6
    print(json.dumps({'checks':count,'negative_rejected':True,'per_volume_rejected':True,
                      'normal_ton_range_quotes_preserved':True,'mixed_lines_mean':135},ensure_ascii=False))

if __name__ == '__main__':
    main()
