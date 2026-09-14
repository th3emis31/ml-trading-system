from __future__ import annotations

import os
import socket

from app import app


def _is_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _find_free_port(host: str, preferred_port: int) -> int:
    for port in range(preferred_port, preferred_port + 25):
        if _is_port_available(host, port):
            return port
    raise RuntimeError(f"No free port found starting at {preferred_port}")


def main() -> None:
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    preferred_port = int(os.environ.get("PORT", os.environ.get("FLASK_PORT", "5001")))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    if "PORT" in os.environ:
        port = preferred_port
    else:
        port = _find_free_port(host, preferred_port)
        if port != preferred_port:
            print(f"Port {preferred_port} is busy, starting on {port} instead.")
    app.run(host=host, port=port, debug=debug, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()