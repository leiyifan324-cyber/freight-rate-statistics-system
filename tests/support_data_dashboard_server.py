import os
import tempfile
import time

from test_data_management import create_demo, parser, start_status_dashboard, FreightDatabase


def main():
    with tempfile.TemporaryDirectory(prefix="freight-data-ui-", ignore_cleanup_errors=True) as directory:
        profiles, records, service, status, coordinator = create_demo(directory)
        database = FreightDatabase(profiles["1001"]["database_file"])
        extra = [parser.parse_freight_line(f"贵港到杭州 大板 {400+i}", parser.trusted_today().isoformat(), "分页测试") for i in range(26)]
        database.insert_records([(parser.build_record_content_dedup_key(r), r) for r in extra])
        parser.rebuild_qq_group_output(profiles["1001"])
        server = start_status_dashboard(status, port=int(os.environ.get("FREIGHT_UI_PORT", "8878")), data_service=service,
                                        recent_data_provider=lambda limit: parser.build_recent_data_snapshot(profiles, limit))
        if server is None:
            raise RuntimeError("测试端口已被占用")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()
            server.server_close()
            coordinator.stop(timeout=60)
            status.close()


if __name__ == "__main__":
    main()
