"""Bootstrap local TLS certificates and gitignored runtime/secrets.env."""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from cryptography.fernet import Fernet

from shared.tls import ensure_local_tls_certs


def ensure_runtime_environment(root_dir: Path | str = ".") -> tuple[Path, Path]:
    """Generate local TLS certificates and runtime/secrets.env if missing."""
    root = Path(root_dir)
    certs_dir = root / "runtime" / "certs"
    ca_crt, server_crt, server_key = ensure_local_tls_certs(certs_dir)

    secrets_path = root / "runtime" / "secrets.env"
    if not secrets_path.exists():
        secrets_path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(
            [
                "# Auto-generated prototype secrets (gitignored)",
                f"GATEWAY_SERVICE_SECRET={secrets.token_urlsafe(32)}",
                f"EVENT_REGISTRATION_SERVICE_SECRET={secrets.token_urlsafe(32)}",
                f"ATTENDANCE_SERVICE_SECRET={secrets.token_urlsafe(32)}",
                f"TOKEN_HMAC_KEY={secrets.token_urlsafe(32)}",
                f"TOKEN_FERNET_KEY={Fernet.generate_key().decode('ascii')}",
                f"CONTEXT_HMAC_KEY={secrets.token_urlsafe(32)}",
                f"SESSION_SECRET={secrets.token_urlsafe(32)}",
                f"TLS_CA_CERT_PATH={ca_crt.as_posix()}",
                f"TLS_SERVER_CERT_PATH={server_crt.as_posix()}",
                f"TLS_SERVER_KEY_PATH={server_key.as_posix()}",
                "",
            ]
        )
        secrets_path.write_text(content, encoding="utf-8")

    return certs_dir, secrets_path


if __name__ == "__main__":
    certs, env_file = ensure_runtime_environment()
    print(f"TLS certificates ready in {certs}")
    print(f"Runtime secrets ready at {env_file}")
