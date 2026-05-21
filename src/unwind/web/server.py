"""serve(): blocking entry point used by `Project.show()`."""

from __future__ import annotations

import threading
import webbrowser

import uvicorn

from unwind.project import Project
from unwind.web.app import build_app
from unwind.web.errors import WebServerError


def serve(
    project: Project,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Run the pipeline once, then block serving the web UI until Ctrl+C.

    Args:
        project: A loaded project; rendered and run on an in-memory DuckDB.
        host: Interface to bind. Stays on loopback by default.
        port: TCP port. Pass `0` to let the OS pick a free port.
        open_browser: If True, opens the default browser at the served URL.

    Raises:
        WebServerError: if the port cannot be bound.
    """
    app = build_app(project)
    url = f"http://{host}:{port}/"
    print(f"unwind web UI: {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    try:
        server.run()
    except KeyboardInterrupt:
        print("\nshutting down")
    except OSError as exc:
        raise WebServerError(f"cannot bind {host}:{port}: {exc}") from exc
