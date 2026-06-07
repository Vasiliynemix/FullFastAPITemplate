"""
CLI-обёртки (project.scripts).

* `api`     — prod-запуск через gunicorn с UvicornWorker.
* `api-dev` — dev-запуск через uvicorn с автоперезагрузкой.
"""

from __future__ import annotations

import subprocess
import sys

from app.core.config import settings


def dev() -> None:
    cmd = [
        "uvicorn",
        "app.main:app",
        "--host",
        settings.host,
        "--port",
        str(settings.port),
        "--reload",
        "--log-level",
        settings.log_level.lower(),
    ]
    sys.exit(subprocess.call(cmd))


def main() -> None:
    cmd = [
        "gunicorn",
        "app.main:app",
        "-c",
        "gunicorn.conf.py",
    ]
    sys.exit(subprocess.call(cmd))
