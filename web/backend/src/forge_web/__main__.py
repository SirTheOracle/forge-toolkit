"""`python3 -m forge_web` — the single entry point `bin/forge-web` execs.

ONE FOREGROUND SERVER ON LITERAL 127.0.0.1. No reload, no workers, no background service,
no network binding. Uvicorn's default access log is left on: this is a local operator tool
and a silent server is harder to debug than a chatty one.
"""

from __future__ import annotations

import sys

from . import SetupError, self_check
from .app import create_app
from .config import ConfigError, LOOPBACK, load


def main(argv: list[str] | None = None) -> int:
    _ = argv
    try:
        self_check()
        cfg = load()
    except (SetupError, ConfigError) as exc:
        sys.stderr.write("forge-web: %s\n" % exc)
        return 1

    import uvicorn

    # Belt and braces: config.load() already refuses any other host, but the literal is
    # repeated at the bind site so a future refactor cannot widen it by accident.
    uvicorn.run(create_app(cfg), host=LOOPBACK, port=cfg.port, log_level="info",
                access_log=True, reload=False, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))