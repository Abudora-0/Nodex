"""Launch the Nodex backend.

    python -m nodex                  # serve on 127.0.0.1:8765
    python -m nodex --port 0         # pick a free port, announce it on stdout
    nodex-sidecar --port 0 --token T --parent-pid 1234
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time

READY_PREFIX = "NODEX_READY "


def _parent_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        WAIT_TIMEOUT = 0x00000102
        result = ctypes.windll.kernel32.WaitForSingleObject(handle, 0)
        ctypes.windll.kernel32.CloseHandle(handle)
        return result == WAIT_TIMEOUT
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _watch_parent(pid: int) -> None:
    while _parent_alive(pid):
        time.sleep(2)
    os._exit(0)


def main() -> int:
    parser = argparse.ArgumentParser(prog="nodex", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--token", default=None, help="require this X-Nodex-Token on API calls")
    parser.add_argument("--parent-pid", type=int, default=None, help="exit when this process exits")
    args = parser.parse_args()

    if args.token:
        os.environ["NODEX_TOKEN"] = args.token

    import uvicorn

    if args.reload:
        uvicorn.run("nodex.api.app:app", host=args.host, port=args.port, reload=True, log_level="info")
        return 0

    from nodex.api.app import app

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.host, args.port))
    port = sock.getsockname()[1]

    if args.parent_pid:
        threading.Thread(target=_watch_parent, args=(args.parent_pid,), daemon=True).start()

    config = uvicorn.Config(app, log_level="warning" if args.port == 0 else "info")
    server = uvicorn.Server(config)

    def announce() -> None:
        while not server.started:
            time.sleep(0.02)
        print(READY_PREFIX + json.dumps({"port": port, "token": args.token}), flush=True)

    threading.Thread(target=announce, daemon=True).start()
    server.run(sockets=[sock])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
