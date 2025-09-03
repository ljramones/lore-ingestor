# service/wait_for_temporal.py
from __future__ import annotations

import os
import socket
import sys
import time
import re
import subprocess

DEFAULT_TARGET = "lore-ingestor-temporal:7233"
SCHEME = re.compile(r"^\s*[a-z]+://", re.I)

def parse_target(raw: str | None) -> tuple[str, int]:
    raw = (raw or DEFAULT_TARGET).strip()
    raw = SCHEME.sub("", raw).strip("/")  # strip http:// if present
    if ":" not in raw:
        return raw, 7233
    host, port = raw.rsplit(":", 1)
    try:
        return host, int(port)
    except ValueError:
        return host, 7233

def wait_for(host: str, port: int, attempts: int, sleep_s: float) -> bool:
    print(f"[wait] Waiting for Temporal at {host}:{port} (attempts={attempts}, sleep={sleep_s}s)", flush=True)
    for i in range(1, attempts + 1):
        try:
            with socket.create_connection((host, port), timeout=3):
                print("[wait] Temporal server is ready!", flush=True)
                return True
        except Exception as e:
            print(f"[wait] attempt {i}/{attempts} failed: {e!s}", flush=True)
            time.sleep(sleep_s)
    print("[wait] Timeout waiting for Temporal server", flush=True)
    return False

def main() -> None:
    target = os.getenv("TEMPORAL_TARGET", DEFAULT_TARGET)
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    queue = os.getenv("TEMPORAL_TASK_QUEUE", "ingest-queue")
    host, port = parse_target(target)

    # knobs
    attempts = int(os.getenv("WAITER_ATTEMPTS", "60"))       # 60 * 2s = 2 minutes
    sleep_s  = float(os.getenv("WAITER_SLEEP_SECONDS", "2"))

    print(f"[wait] target={host}:{port} ns={namespace} queue={queue}", flush=True)
    ok = wait_for(host, port, attempts, sleep_s)
    if not ok:
        sys.exit(1)

    # Exec the real worker process (replace current PID)
    cmd = [sys.executable, "-m", "service.temporal_worker"]
    print(f"[wait] exec: {' '.join(cmd)}", flush=True)
    os.execvp(cmd[0], cmd)  # never returns

if __name__ == "__main__":
    main()
