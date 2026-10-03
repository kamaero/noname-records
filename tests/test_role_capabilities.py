"""Who may do what, after the studio settled on four roles.

`admin` is Max Ray, `author` is Belozerov, everyone else is `dictor`. Что роль ровно
одна — в `test_one_dictor_role.py`; здесь про то, что она даёт.

Четвёртая роль — `agent`, кастинг-директор. Она законна ровно потому, чем были
незаконны `dictor_pro` и `dictor_neo`: у неё есть живой носитель, названный поимённо.
Роль не даёт разделов, а отнимает один — Подготовку.

The line that matters runs through the middle of what used to be one check. A dictor
works on stress and on the palette of the roles he reads — that is his craft — and does
not start pipeline runs, publish chapters, mark them reviewed, or move money. Before
this, one `_can_edit` guarded all of it together, so a dictor could do none of it.
"""
import pytest

from app import auth
from app.constants import UserRole
from app.v2.api import _can_edit, _can_voice


class FakeRequest:
    pass


@pytest.fixture()
def as_role(monkeypatch):
    def _set(*roles: str):
        monkeypatch.setattr(auth, "session_roles", lambda request: set(roles))
        return FakeRequest()

    return _set


class TestTheRolesThemselves:
    def test_there_are_four_of_them(self):
        assert [role.value for role in UserRole] == ["admin", "author", "dictor", "agent"]


class TestWhatADictorMayDo:
    def test_stress_and_palette_are_his_craft(self, as_role):
        assert _can_voice(as_role(UserRole.DICTOR)) is True

    def test_he_does_not_run_the_pipeline_or_edit_the_markup(self, as_role):
        assert _can_edit(as_role(UserRole.DICTOR)) is False


class TestWhatAnAgentMayDo:
    """Агент — кастинг-директор: ведёт актёров, но не трогает ни пайплайн, ни разметку."""

    def test_he_does_not_run_the_pipeline_or_edit_the_markup(self, as_role):
        assert _can_edit(as_role(UserRole.AGENT)) is False

    def test_the_workspace_opens_by_the_role_itself(self, as_role):
        """Не по тому, что вошли через телеграм: доступ висит на роли."""
        assert auth.has_workspace_full_access(as_role(UserRole.AGENT)) is True

    def test_he_is_an_agent(self, as_role):
        assert auth.is_agent(as_role(UserRole.AGENT)) is True

    def test_an_author_who_also_carries_the_role_is_not_one(self, as_role):
        """Роль отнимает разделы. Тому, кто и так редактор, отнимать нечего."""
        assert auth.is_agent(as_role(UserRole.AGENT, UserRole.AUTHOR)) is False

    def test_an_admin_who_also_carries_the_role_is_not_one(self, as_role):
        assert auth.is_agent(as_role(UserRole.AGENT, UserRole.ADMIN)) is False

    def test_a_dictor_is_not_an_agent(self, as_role):
        assert auth.is_agent(as_role(UserRole.DICTOR)) is False


class TestWhatTheAuthorAndTheAdminMayDo:
    def test_the_author_does_both(self, as_role):
        request = as_role(UserRole.AUTHOR)
        assert _can_edit(request) is True and _can_voice(request) is True

    def test_the_admin_does_both(self, as_role):
        request = as_role(UserRole.ADMIN)
        assert _can_edit(request) is True and _can_voice(request) is True


class TestSomeoneWithNoRoleAtAll:
    def test_may_do_neither(self, as_role):
        request = as_role()
        assert _can_edit(request) is False and _can_voice(request) is False
