from __future__ import annotations

import socket
from pathlib import Path

from frontend.server import ensure_runtime_dirs, start_server


ROOT = Path(__file__).resolve().parent


def _find_free_port(start_port: int = 7860) -> int:
    for port in range(start_port, 65536):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            in_use = sock.connect_ex(("127.0.0.1", port)) == 0
            if not in_use:
                return port
    raise RuntimeError("No free port found in range 7860-65535")


def main() -> None:
    ensure_runtime_dirs(ROOT)
    port = _find_free_port(7860)
    print("MTASA local server started.")
    print(f"Open http://localhost:{port}")
    start_server(root=ROOT, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
