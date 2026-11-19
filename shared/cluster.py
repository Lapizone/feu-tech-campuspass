"""Shared multi-process HTTPS service cluster runner for dev startup, E2E tests, and evidence capture."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from scripts.init_runtime import ensure_runtime_environment
from shared.config import parse_env_file
from shared.tls import build_client_ssl_context

SERVICE_SPECS: list[tuple[str, str, int]] = [
    ("student-directory", "services.student_directory.app:create_app", 8441),
    ("event-registration", "services.event_registration.app:create_app", 8442),
    ("attendance-service", "services.attendance.app:create_app", 8443),
    ("notification-service", "services.notification.app:create_app", 8444),
    ("gateway", "services.gateway.app:create_app", 8440),
]


def find_free_port() -> int:
    """Reserve and return an available localhost TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class ManagedService:
    """A running HTTPS uvicorn child process."""

    name: str
    factory: str
    port: int
    process: subprocess.Popen[str]

    @property
    def url(self) -> str:
        return f"https://127.0.0.1:{self.port}"


@dataclass
class ServiceCluster:
    """Manages all 5 HTTPS microservices with isolated DBs and shared TLS CA."""

    root_dir: Path
    data_dir: Path
    certs_dir: Path
    env: dict[str, str]
    ports: dict[str, int]
    processes: dict[str, ManagedService] = field(default_factory=dict)

    @property
    def ca_cert_path(self) -> Path:
        return self.certs_dir / "ca.crt"

    @property
    def gateway_url(self) -> str:
        return f"https://127.0.0.1:{self.ports['gateway']}"

    @classmethod
    def build(
        cls,
        *,
        work_dir: Path | str,
        use_ephemeral_ports: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> ServiceCluster:
        repo_root = Path(__file__).resolve().parent.parent
        work_path = Path(work_dir).resolve()
        certs_dir, secrets_path = ensure_runtime_environment(work_path)
        secrets_env = parse_env_file(secrets_path)
        data_dir = work_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        ports = {
            name: (find_free_port() if use_ephemeral_ports else default_port)
            for name, _, default_port in SERVICE_SPECS
        }

        env = os.environ.copy()
        env.update(secrets_env)
        env["PYTHONPATH"] = str(repo_root)
        env.update(
            {
                "GATEWAY_HOST": "127.0.0.1",
                "GATEWAY_PORT": str(ports["gateway"]),
                "STUDENT_DIRECTORY_URL": f"https://127.0.0.1:{ports['student-directory']}",
                "EVENT_REGISTRATION_URL": f"https://127.0.0.1:{ports['event-registration']}",
                "ATTENDANCE_SERVICE_URL": f"https://127.0.0.1:{ports['attendance-service']}",
                "NOTIFICATION_SERVICE_URL": f"https://127.0.0.1:{ports['notification-service']}",
                "STUDENT_DIRECTORY_DB": str(data_dir / "student_directory.db"),
                "EVENT_REGISTRATION_DB": str(data_dir / "event_registration.db"),
                "ATTENDANCE_DB": str(data_dir / "attendance.db"),
                "NOTIFICATION_DB": str(data_dir / "notification.db"),
                "GATEWAY_AUTH_DB": str(data_dir / "gateway_auth.db"),
                "TLS_CA_CERT_PATH": str(certs_dir / "ca.crt"),
                "TLS_SERVER_CERT_PATH": str(certs_dir / "server.crt"),
                "TLS_SERVER_KEY_PATH": str(certs_dir / "server.key"),
            }
        )
        if extra_env:
            env.update(extra_env)

        return cls(
            root_dir=repo_root,
            data_dir=data_dir,
            certs_dir=certs_dir,
            env=env,
            ports=ports,
        )

    def start_service(self, name: str, wait_ready: bool = True) -> ManagedService:
        factory = dict((n, f) for n, f, _ in SERVICE_SPECS)[name]
        port = self.ports[name]
        cmd = [
            sys.executable,
            "-m",
            "shared.serve_service",
            factory,
            str(port),
            str(self.certs_dir / "server.crt"),
            str(self.certs_dir / "server.key"),
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=str(self.root_dir),
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        svc = ManagedService(name=name, factory=factory, port=port, process=proc)
        self.processes[name] = svc
        if wait_ready:
            self._wait_for_health(svc)
        return svc

    def stop_service(self, name: str) -> None:
        svc = self.processes.pop(name, None)
        if not svc:
            return
        if svc.process.poll() is None:
            svc.process.terminate()
            try:
                svc.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                svc.process.kill()
                svc.process.wait(timeout=5.0)
        if svc.process.stderr is not None:
            svc.process.stderr.close()

    def start_all(self) -> None:
        for name, _, _ in SERVICE_SPECS:
            self.start_service(name, wait_ready=False)
        for name, _, _ in SERVICE_SPECS:
            self._wait_for_health(self.processes[name])

    def stop_all(self) -> None:
        for name in list(reversed(list(self.processes.keys()))):
            self.stop_service(name)

    def _wait_for_health(self, svc: ManagedService, timeout_sec: float = 15.0) -> None:
        deadline = time.monotonic() + timeout_sec
        ssl_ctx = build_client_ssl_context(self.ca_cert_path)
        last_error: Exception | None = None
        with httpx.Client(verify=ssl_ctx, timeout=1.5) as client:
            while time.monotonic() < deadline:
                if svc.process.poll() is not None:
                    stderr_out = svc.process.stderr.read() if svc.process.stderr else ""
                    raise RuntimeError(
                        f"Service '{svc.name}' exited early with code {svc.process.returncode}: {stderr_out}"
                    )
                try:
                    resp = client.get(f"{svc.url}/health")
                    if resp.status_code == 200:
                        return
                except httpx.TransportError as exc:
                    last_error = exc
                time.sleep(0.15)
        raise TimeoutError(
            f"Timed out waiting for HTTPS /health on {svc.name} ({svc.url}); last_error={last_error!r}"
        )
