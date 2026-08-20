import importlib.util
import json
import os
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "src")
sys.path.insert(0, SRC_DIR)


PARSER_PATH = os.path.join(SRC_DIR, "qq_freight_parser.py")
DEFAULT_RULES = os.path.join(ROOT, "config", "freight_rules.default.json")


def load_parser():
    spec = importlib.util.spec_from_file_location("freight_config_integrity", PARSER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def main():
    freight = load_parser()
    assert freight.matplotlib.get_backend().lower() == "agg"

    with tempfile.TemporaryDirectory(prefix="freight-config-integrity-") as directory:
        output_root = os.path.join(directory, "data")
        os.makedirs(output_root)
        legacy_dir = os.path.join(output_root, "旧群名_10001")
        os.makedirs(legacy_dir)
        marker = os.path.join(legacy_dir, "历史数据标记.txt")
        with open(marker, "w", encoding="utf-8") as stream:
            stream.write("preserved")

        config = {
            "group_ids": {"10001"},
            "group_names": {"10001": "旧群名"},
            "group_default_origins": {"10001": "贵港"},
            "output_root": output_root,
            "rules_file": DEFAULT_RULES,
        }
        first_profile = freight.build_qq_group_profiles(config)["10001"]
        stable_dir = os.path.join(output_root, "QQ群_10001")
        assert first_profile["output_dir"] == stable_dir
        assert os.path.exists(os.path.join(stable_dir, "历史数据标记.txt"))
        assert not os.path.exists(legacy_dir)

        renamed = dict(config)
        renamed["group_names"] = {"10001": "新群名"}
        second_profile = freight.build_qq_group_profiles(renamed)["10001"]
        assert second_profile["output_dir"] == stable_dir
        assert os.path.exists(os.path.join(stable_dir, "历史数据标记.txt"))

        changed_rules = os.path.join(directory, "changed_rules.json")
        write_json(changed_rules, {
            "default_origin": "南宁",
            "origin_groups": {"南宁": ["南宁"]},
            "destination_groups": {"广州": ["广州"]},
            "board_types": ["红板"],
            "price_threshold": 500,
        })
        live_config = os.path.join(directory, "qq_live_config.json")
        write_json(live_config, {
            "ws_url": "ws://127.0.0.1:3001",
            "group_ids": ["10001"],
            "group_names": {"10001": "新群名"},
            "group_default_origins": {"10001": "南宁"},
            "output_root": output_root,
            "rules_file": changed_rules,
            "status_dashboard": {"enabled": False},
        })

        freight.load_rules_config(DEFAULT_RULES)
        before = {
            "default": freight.DEFAULT_ORIGIN,
            "origins": freight.ORIGIN_GROUPS,
            "destinations": freight.DEST_GROUPS,
            "boards": freight.BOARD_TYPES,
            "threshold": freight.PRICE_THRESHOLD,
        }
        freight.validate_qq_live_config_without_applying(live_config)
        after = {
            "default": freight.DEFAULT_ORIGIN,
            "origins": freight.ORIGIN_GROUPS,
            "destinations": freight.DEST_GROUPS,
            "boards": freight.BOARD_TYPES,
            "threshold": freight.PRICE_THRESHOLD,
        }
        assert after == before

    print(json.dumps({
        "matplotlib_agg_backend": True,
        "legacy_directory_migrated": True,
        "group_rename_keeps_stable_directory": True,
        "history_preserved": True,
        "validation_has_no_runtime_rule_side_effect": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
