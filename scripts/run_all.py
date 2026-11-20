"""Launch all five Campus Event HTTPS microservices for local demonstration."""

from __future__ import annotations

import signal
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from shared.cluster import SERVICE_SPECS, ServiceCluster


def main() -> None:
    cluster = ServiceCluster.build(work_dir=ROOT_DIR, use_ephemeral_ports=False)
    print("Starting Integrated Campus Event Registration & Attendance System (HTTPS)...")
    cluster.start_all()
    for name, _, port in SERVICE_SPECS:
        print(f"  - {name:20s} https://127.0.0.1:{port}")
    print(f"\nWeb Prototype UI ready at: {cluster.gateway_url}/")
    print("Press Ctrl+C to stop all services cleanly.")

    running = True

    def _handle_sig(_sig, _frame) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    restore_flag = ROOT_DIR / "runtime" / "restore_attendance.flag"
    restore_flag.unlink(missing_ok=True)

    try:
        while running:
            if restore_flag.exists():
                restore_flag.unlink(missing_ok=True)
                att = cluster.processes.get("attendance-service")
                if att is None or att.process.poll() is not None:
                    cluster.start_service("attendance-service", wait_ready=True)
                    print("Restored attendance-service on https://127.0.0.1:8443")
            time.sleep(0.5)
    finally:
        print("\nShutting down services...")
        cluster.stop_all()
        print("All services stopped.")


if __name__ == "__main__":
    main()
