from __future__ import annotations

import argparse
import getpass
import json
from datetime import datetime

from .config import Settings
from .reports import ReportService, send_test_email
from .repository_postgres import PostgresRepository
from .security import hash_password
from .umami import UmamiClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hydroclimatex-analytics")
    commands = parser.add_subparsers(dest="command", required=True)
    password = commands.add_parser("hash-password", help="create an Argon2id administrator password hash")
    password.add_argument("password", nargs="?", help="omit to enter without terminal echo")
    reset = commands.add_parser("reset-password", help="create a replacement hash and revoke every session")
    reset.add_argument("password", nargs="?", help="omit to enter without terminal echo")
    commands.add_parser("migrate", help="apply Analytics database migrations")
    commands.add_parser("init-umami", help="ensure the Umami reader user and website exist and print their credentials")
    commands.add_parser("test-email", help="send a one-off SMTP configuration test")
    send = commands.add_parser("send-report", help="generate and send one monthly report")
    send.add_argument("month", help="month in YYYY-MM format")
    send.add_argument("--force", action="store_true", help="resend a previously delivered report")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "hash-password":
        secret = args.password or getpass.getpass("New administrator password: ")
        print(hash_password(secret))
        return 0

    if args.command == "init-umami":
        from .umami_init import init_umami

        for key, value in init_umami(Settings.from_env()).items():
            print(f"{key}={value}")
        return 0

    settings = Settings.from_env()
    repository = PostgresRepository(settings.database_url)
    if args.command == "reset-password":
        secret = args.password or getpass.getpass("New administrator password: ")
        replacement = hash_password(secret)
        repository.revoke_all_sessions()
        print(replacement)
        print("All administrator sessions revoked. Replace ANALYTICS_ADMIN_PASSWORD_HASH and recreate analytics-api.")
        return 0
    if args.command == "migrate":
        repository.migrate()
        print("Analytics database migrations applied.")
        return 0

    if args.command == "test-email":
        send_test_email(settings)
        print("SMTP test email sent.")
        return 0

    month = datetime.strptime(args.month, "%Y-%m").date().replace(day=1)
    reports = ReportService(settings, repository, UmamiClient(settings))
    print(json.dumps(reports.send(month, force=args.force)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
