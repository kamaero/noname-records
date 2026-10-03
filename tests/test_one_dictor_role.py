"""У диктора одна роль. Не три, не «одна и две про запас».

`dictor_pro` и `dictor_neo` не носила ни одна учётка — ни в `user_roles`, ни в
whitelist'е телеграма. Они жили только в исходниках и тащили за собой целый режим
«neo-only»: отдельный набор вкладок, отдельный `access_scope`, отдельный редирект после
входа — всё для людей, которых не существует.

Совместимость, которую они изображали, обходилась дороже себя: `require_dictor_pro`
спрашивал роль, которой ни у кого нет, и молча отвечал 403 всем пятидесяти девяти
дикторам. Пока роль есть в словаре, кто-нибудь снова напишет её в проверке.
"""
import pytest

from app import auth, constants
from app.constants import DICTOR_ROLES, UserRole
from app.v2.api import _can_edit, _can_voice


class FakeRequest:
    """Сессия без cookie: роли подставляются напрямую, источник входа не при чём."""
    cookies: dict = {}


@pytest.fixture()
def as_role(monkeypatch):
    def _set(*roles: str):
        monkeypatch.setattr(auth, "session_roles", lambda request: set(roles))
        return FakeRequest()

    return _set


class TestTheRoleList:
    def test_the_studio_has_four_roles_and_the_dictor_is_one(self):
        assert [role.value for role in UserRole] == ["admin", "author", "dictor", "agent"]

    def test_a_dictor_is_a_dictor_and_nothing_else(self):
        assert DICTOR_ROLES == frozenset({"dictor"})

    def test_the_legacy_set_is_gone_not_merely_empty(self):
        """Пустое множество с прежним именем — приглашение снова его наполнить."""
        assert not hasattr(constants, "LEGACY_DICTOR_ROLES")

    def test_the_two_names_appear_in_no_role_table(self):
        for name in ("dictor_pro", "dictor_neo"):
            assert name not in {role.value for role in UserRole}
            assert name not in DICTOR_ROLES
            assert name not in constants.WORKSPACE_TABS


class TestWhatTheOldNamesBuyNow:
    def test_nothing(self, as_role):
        request = as_role("dictor_pro")

        assert _can_voice(request) is False
        assert _can_edit(request) is False

    def test_and_no_workspace_either(self, as_role):
        assert auth.has_workspace_full_access(as_role("dictor_neo")) is False


class TestTheModeTheyDraggedAlong:
    def test_neo_only_access_is_gone(self):
        assert not hasattr(auth, "has_workspace_neo_only_access")

    def test_so_is_its_tab_order(self):
        assert not hasattr(constants, "NEO_ONLY_TAB_ORDER")

