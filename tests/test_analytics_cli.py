from analytics_app.cli import build_parser


def test_cli_exposes_server_side_admin_commands():
    parser = build_parser()
    for argv, command in [
        (["hash-password", "temporary-secret"], "hash-password"),
        (["migrate"], "migrate"),
        (["send-report", "2026-08"], "send-report"),
        (["test-email"], "test-email"),
        (["reset-password", "new-long-administrator-password"], "reset-password"),
    ]:
        assert parser.parse_args(argv).command == command
