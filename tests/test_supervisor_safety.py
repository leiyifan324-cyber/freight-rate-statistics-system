import json
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import freight_supervisor


def main():
    source_filter = freight_supervisor.build_managed_process_filter(
        current_pid=123,
        frozen=False,
    )
    assert "freight_app.py" in source_filter
    assert freight_supervisor.BASE_DIR.replace("'", "''") in source_filter
    assert "--mode supervisor" in source_filter
    assert "--mode qq-live" in source_filter
    assert "$_.ProcessId -ne 123" in source_filter
    assert "ExecutablePath -eq" not in source_filter or "freight_app.py" in source_filter

    frozen_filter = freight_supervisor.build_managed_process_filter(
        current_pid=456,
        frozen=True,
    )
    assert "ExecutablePath -eq" in frozen_filter
    assert "--mode" in frozen_filter
    assert "$_.ProcessId -ne 456" in frozen_filter

    collector_filter = freight_supervisor.build_managed_process_filter(
        current_pid=789,
        frozen=False,
        modes=("qq-live",),
    )
    assert "--mode qq-live" in collector_filter
    assert "--mode supervisor" not in collector_filter
    assert "freight_app.py" in collector_filter

    print(json.dumps({
        "source_shutdown_scoped_to_project": True,
        "frozen_shutdown_scoped_to_application": True,
        "unrelated_python_processes_excluded": True,
        "unhealthy_recovery_scoped_to_collector": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
