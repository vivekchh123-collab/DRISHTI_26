"""Entry point for the frozen desktop build.

PyInstaller cannot run ``python -m uvicorn``, so the server is started
programmatically here instead. Two things matter for the desktop shell:

* **A free port is chosen at runtime.** Hard-coding 8000 means the app refuses
  to start whenever anything else on the machine already has it, which on a
  developer's laptop is often. The chosen port is printed on stdout in a form
  the Electron shell parses.
* **Paths resolve against the bundle.** A frozen build unpacks its data into a
  temporary directory, so the frontend and model files are found relative to
  ``sys._MEIPASS`` rather than to the source tree.
"""

from __future__ import annotations

import os
import socket
import sys


def bundle_dir() -> str:
    """Where the bundled data lives, frozen or not."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def free_port(preferred: int = 8000) -> int:
    """The preferred port if it is free, otherwise one the OS picks."""
    for candidate in (preferred, 0):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", candidate))
            port = s.getsockname()[1]
            s.close()
            return port
        except OSError:
            s.close()
    return preferred


def main() -> int:
    root = bundle_dir()
    # The app package sits beside this file in both the frozen and source
    # layouts; make sure it is importable before anything tries.
    if root not in sys.path:
        sys.path.insert(0, root)

    os.environ.setdefault("DRISHTI_FRONTEND", os.path.join(root, "frontend"))
    os.environ.setdefault("DRISHTI_MODELS", os.path.join(root, "models"))

    port = int(os.environ.get("DRISHTI_PORT") or free_port())

    # Printed for the desktop shell to read, and flushed immediately: a frozen
    # process without a console buffers stdout, and a shell waiting on a line
    # that never arrives simply hangs with a blank window.
    print("DRISHTI_PORT=%d" % port, flush=True)

    import uvicorn

    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning",
                access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
