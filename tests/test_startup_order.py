"""The app must survive its own migrations.

Startup does two things: it brings the schema to head, and it seeds the Telegram
whitelist from the environment. They were registered in the wrong order — the seeding
ran first — which nothing noticed for as long as no migration touched a table the
seeding reads. Revision 0014 added a column to exactly that table, and production
refused to start: «no such column: telegram_auth_accounts.user_id», caught by the
deploy's smoke check and rolled back.

The order is the invariant, so the test boots the real app against a database that
needs migrating, with a whitelist to seed, in a subprocess of its own — the schema
work has to happen on a real file, and the app may only be imported once per process.
"""
import os
import subprocess
import sys
import textwrap

BOOT = textwrap.dedent(
    """
    from fastapi.testclient import TestClient
    import app.main

    with TestClient(app.main.app) as client:
        assert client.get("/health").status_code == 200, "health не ответил"
    print("OK")
    """
)


def test_the_app_starts_on_a_database_that_still_needs_migrating(tmp_path):
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{tmp_path / 'fresh.db'}"
    # a non-empty whitelist is what makes startup read the table it may not have yet
    env["TELEGRAM_AUTH_WHITELIST"] = "900000100|Max Ray;900000106|Александр Белозёров"

    done = subprocess.run(
        [sys.executable, "-c", BOOT],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert done.returncode == 0, f"приложение не поднялось:\n{done.stdout}\n{done.stderr[-2500:]}"
    assert "OK" in done.stdout
