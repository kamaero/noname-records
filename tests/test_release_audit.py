"""Сторож публичного релиза: секреты, реальные люди, запрещённые пути, проза книг."""
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release_audit  # noqa: E402


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture()
def people(tmp_path):
    db = tmp_path / "people.db"
    con = sqlite3.connect(db)
    con.execute("create table users (display_name text)")
    con.execute("create table telegram_auth_accounts (telegram_user_id text, display_name text)")
    con.execute("insert into users values ('Ветрогонова Аглая')")
    con.execute("insert into telegram_auth_accounts values ('5866697603', 'Ветрогонова Аглая')")
    con.execute("insert into users values ('Ку Ли Мэй')")
    con.execute("create table bot_contacts (telegram_user_id text, tg_name text, username text)")
    con.execute("insert into bot_contacts values ('7001', 'Gleb Voice', 'glebvoice777')")
    con.execute("create table characters (actor_name text)")
    con.execute("insert into characters values ('Зимородкова Ульяна?')")
    con.commit()
    con.close()
    return db


def _classes(findings):
    return sorted({f.kind for f in findings})


def test_a_clean_repo_has_no_findings(repo, people):
    assert release_audit.audit_tree(repo, people_db=people) == []


@pytest.mark.parametrize("text,kind", [
    ("TOKEN = '1234567890:AAH" + "k7Qz" * 9 + "'\n", "secret"),
    ("key = 'sk-or-v1-" + "a" * 64 + "'\n", "secret"),
    ("ELEVENLABS_API_KEY=" + "q" * 32 + "\n", "secret"),
    ("owner = 'Аглая Ветрогонова'\n", "person"),
    ("chat_id = '5866697603'\n", "person"),
    ("who = 'Ку Ли Мэй'\n", "person"),
    ("at = '@glebvoice777'\n", "person"),
    ("actor = 'Ульяна Зимородкова'\n", "person"),
    ("url = 'https://buchteam.online/app'\n", "forbidden"),
    ("OWNER = '11504'\n", "forbidden"),
])
def test_each_kind_is_caught_in_the_tree(repo, people, text, kind):
    (repo / "leak.py").write_text(text, encoding="utf-8")
    assert kind in _classes(release_audit.audit_tree(repo, people_db=people))


def test_placeholders_are_not_secrets(repo, people):
    (repo / ".env.example").write_text("SECRET_KEY=change-this-secret\nELEVENLABS_API_KEY=\n", encoding="utf-8")
    assert release_audit.audit_tree(repo, people_db=people) == []


def test_forbidden_paths_are_caught(repo, people):
    (repo / "docs" / "superpowers").mkdir(parents=True)
    (repo / "docs" / "superpowers" / "x.md").write_text("ok\n", encoding="utf-8")
    (repo / "base.db").write_bytes(b"x")
    found = release_audit.audit_tree(repo, people_db=people)
    assert {f.path for f in found if f.kind == "forbidden"} >= {"docs/superpowers/x.md", "base.db"}


def test_long_prose_is_reported_but_not_blocking(repo, people):
    prose = "Он медленно поднялся по лестнице, оглянулся на тёмный двор и подумал, что ночь будет долгой и тревожной.\n"
    (repo / "fixture.py").write_text(f"TEXT = '''{prose}'''\n", encoding="utf-8")
    found = release_audit.audit_tree(repo, people_db=people)
    assert _classes(found) == ["prose"]
    assert release_audit.blocking(found) == []


def test_history_finds_what_the_tree_no_longer_has(repo, people):
    (repo / "old.py").write_text("x = 'sk-or-v1-" + "b" * 64 + "'\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "leak")
    (repo / "old.py").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "remove")
    assert release_audit.audit_tree(repo, people_db=people) == []
    assert "secret" in _classes(release_audit.audit_history(repo, people_db=people))


def test_secret_values_never_appear_in_the_report(repo, people):
    secret = "sk-or-v1-" + "c" * 64
    (repo / "leak.py").write_text(f"k = '{secret}'\n", encoding="utf-8")
    report = release_audit.format_report(release_audit.audit_tree(repo, people_db=people))
    assert secret not in report and "leak.py" in report


def test_eight_words_from_a_book_in_the_base_are_a_leak(repo, tmp_path):
    db = tmp_path / "books.db"
    con = sqlite3.connect(db)
    con.execute("create table v2_segments (text text)")
    con.execute("insert into v2_segments values ('— Ты опять опоздал к ужину, — проворчала старая ведьма и захлопнула ставни.')")
    con.commit()
    con.close()
    (repo / "fixture.py").write_text('LINE = "— Ты опять опоздал к ужину, — проворчала старая ведьма"\n', encoding="utf-8")
    (repo / "other.py").write_text('LINE = "Ты опять опоздал к обеду"\n', encoding="utf-8")
    found = release_audit.audit_tree(repo, books_db=db)
    assert {(f.kind, f.path) for f in found} == {("book", "fixture.py")}
    assert release_audit.blocking(found)


def test_a_line_checked_by_a_person_can_be_marked_ok(repo, tmp_path):
    """Числительные «один два три…» встречаются и в книгах; такую строку человек
    помечает явно, а не ослабляет сторож для всех."""
    db = tmp_path / "books.db"
    con = sqlite3.connect(db)
    con.execute("create table v2_segments (text text)")
    con.execute("insert into v2_segments values ('один два три четыре пять шесть семь восемь девять')")
    con.commit()
    con.close()
    (repo / "nums.py").write_text('ONES = ["один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь"]  # release-audit: ok\n',
                                  encoding="utf-8")
    assert release_audit.audit_tree(repo, books_db=db) == []


@pytest.mark.parametrize("text", [
    "SECRET_KEY=abcdefghijklmnopqrstuvwxyz0123456789\n",
    "REAL_TOKEN=example-abcdefghijklmnopqrstuvwxyz0123456789\n",
])
def test_a_session_key_and_a_placeholder_lookalike_are_secrets(repo, text):
    (repo / "conf.env").write_text(text, encoding="utf-8")
    assert "secret" in _classes(release_audit.audit_tree(repo))


def test_only_exact_placeholders_pass(repo):
    (repo / "conf.env").write_text("SECRET_KEY=change-this-secret\nOPENAI_API_KEY=${OPENAI_API_KEY:-}\n", encoding="utf-8")
    assert release_audit.audit_tree(repo) == []


def test_text_inside_a_binary_file_is_scanned(repo):
    (repo / "blob.bin").write_bytes(b"\x00\x01\xffhttps://buchteam.online/app\x00\x02")
    assert "forbidden" in _classes(release_audit.audit_tree(repo))


def test_commit_messages_are_part_of_the_history(repo):
    _git(repo, "commit", "-q", "--allow-empty", "-m", "fix for buchteam.online")
    assert "forbidden" in _classes(release_audit.audit_history(repo))


def test_release_mode_refuses_to_run_without_people_and_books(repo, capsys):
    assert release_audit.main(["--root", str(repo), "--release"]) == 2


def test_release_mode_refuses_an_empty_source(repo, tmp_path):
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert release_audit.main(["--root", str(repo), "--release", "--people-from", str(empty),
                               "--books-from", str(empty)]) == 2


@pytest.mark.parametrize("text", [
    "SECRET_KEY={abcdefghijklmnopqrstuvwxyz0123456789}\n",
    "SECRET_KEY=re.compileabcdefghijklmnopqrstuvwxyz0123456789\n",
    "SECRET_KEY=$abcdefghijklmnopqrstuvwxyz0123456789-not-a-var!\n",
])
def test_only_real_shell_substitutions_are_exempt(repo, text):
    (repo / "conf.env").write_text(text, encoding="utf-8")
    assert "secret" in _classes(release_audit.audit_tree(repo))


@pytest.mark.parametrize("text", [
    "KEY_ONE=${OPENAI_API_KEY:-}\n", "KEY_TWO=$($env:CLAUDE_API_KEY)\n", "SECRET_KEY=$secret_key_generated_here\n",
])
def test_shell_substitutions_are_not_secrets(repo, text):
    (repo / "install.sh").write_text(text, encoding="utf-8")
    assert release_audit.audit_tree(repo) == []


def test_a_binary_file_from_an_old_commit_is_scanned(repo):
    (repo / "logo.bin").write_bytes(b"\x00\xffxkrbot-internal\x00")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add")
    (repo / "logo.bin").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "drop")
    assert "forbidden" in _classes(release_audit.audit_history(repo))
