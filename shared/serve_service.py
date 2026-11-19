"""Subprocess entrypoint that restores stdlib SSLContext before starting uvicorn HTTPS."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import uvicorn

from shared.tls import ensure_stdlib_ssl_context


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit(
            "Usage: python -m shared.serve_service <factory> <port> <cert_path> <key_path>"
        )
    factory, port_str, cert_path, key_path = sys.argv[1:5]
    ensure_stdlib_ssl_context()
    uvicorn.run(
        factory,
        factory=True,
        host="127.0.0.1",
        port=int(port_str),
        ssl_certfile=cert_path,
        ssl_keyfile=key_path,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
