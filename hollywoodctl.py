#!/usr/bin/env python3
"""Install and manage the Hollywood user service."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8765"
DEFAULT_DB = str(Path.home() / ".hollywood" / "hollywood.db")
DEFAULT_UNIT_NAME = "hollywood.service"


def hollywood_executable() -> str:
    path = shutil.which("hollywood")
    if not path:
        raise SystemExit("Could not find `hollywood` on PATH. Install the package or use the checked-in script.")
    return path


def render_unit(exec_path: str, db_path: str) -> str:
    return f"""[Unit]
Description=Hollywood local agent chat service
After=network.target

[Service]
Type=simple
ExecStart={exec_path} serve --host 127.0.0.1 --port 8765 --db {db_path}
Restart=on-failure
RestartSec=1

[Install]
WantedBy=default.target
"""


def run_systemctl(*args: str) -> int:
    return subprocess.run(["systemctl", "--user", *args], check=False).returncode


def cmd_install(args: argparse.Namespace) -> int:
    unit_dir = Path(args.unit_dir).expanduser()
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / DEFAULT_UNIT_NAME
    db_path = str(Path(args.db).expanduser())
    unit_path.write_text(render_unit(hollywood_executable(), db_path))

    if run_systemctl("daemon-reload") != 0:
        raise SystemExit("systemctl --user daemon-reload failed")
    if run_systemctl("enable", "--now", DEFAULT_UNIT_NAME) != 0:
        raise SystemExit("systemctl --user enable --now failed")

    print(f"Installed and started {DEFAULT_UNIT_NAME}")
    print(f"Unit path: {unit_path}")
    print("Manage with: hollywoodctl")
    return 0


def cmd_start(_args: argparse.Namespace) -> int:
    raise SystemExit(run_systemctl("start", DEFAULT_UNIT_NAME))


def cmd_stop(_args: argparse.Namespace) -> int:
    raise SystemExit(run_systemctl("stop", DEFAULT_UNIT_NAME))


def cmd_restart(_args: argparse.Namespace) -> int:
    raise SystemExit(run_systemctl("restart", DEFAULT_UNIT_NAME))


def cmd_status(_args: argparse.Namespace) -> int:
    raise SystemExit(run_systemctl("--no-pager", "--full", "status", DEFAULT_UNIT_NAME))


def cmd_logs(_args: argparse.Namespace) -> int:
    raise SystemExit(
        subprocess.run(["journalctl", "--user", "-u", DEFAULT_UNIT_NAME, "-f"], check=False).returncode
    )


def cmd_health(args: argparse.Namespace) -> int:
    url = args.url.rstrip("/") + "/hollywood/v1/health"
    raise SystemExit(subprocess.run(["curl", "-fsS", url], check=False).returncode)


def cmd_disable(_args: argparse.Namespace) -> int:
    raise SystemExit(run_systemctl("disable", "--now", DEFAULT_UNIT_NAME))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hollywoodctl", description="Manage the Hollywood user service")
    sub = parser.add_subparsers(dest="cmd", required=True)

    install = sub.add_parser("install", help="Install, enable, and start the systemd user service")
    install.add_argument("--db", default=os.environ.get("HOLLYWOOD_DB", DEFAULT_DB))
    install.add_argument(
        "--unit-dir",
        default=str(Path.home() / ".config" / "systemd" / "user"),
        help="systemd user unit directory",
    )
    install.set_defaults(func=cmd_install)

    start = sub.add_parser("start", help="Start the service")
    start.set_defaults(func=cmd_start)

    stop = sub.add_parser("stop", help="Stop the service")
    stop.set_defaults(func=cmd_stop)

    restart = sub.add_parser("restart", help="Restart the service")
    restart.set_defaults(func=cmd_restart)

    status = sub.add_parser("status", help="Show service status")
    status.set_defaults(func=cmd_status)

    logs = sub.add_parser("logs", help="Follow journal logs")
    logs.set_defaults(func=cmd_logs)

    health = sub.add_parser("health", help="Check the HTTP health endpoint")
    health.add_argument("--url", default=os.environ.get("HOLLYWOOD_URL", DEFAULT_URL))
    health.set_defaults(func=cmd_health)

    disable = sub.add_parser("disable", help="Stop and disable the service")
    disable.set_defaults(func=cmd_disable)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    result = args.func(args)
    if isinstance(result, int):
        return result
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
