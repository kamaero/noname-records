from urllib.parse import urlencode

from fastapi import Request

from app.auth import has_workspace_full_access
from app.constants import FULL_WORKSPACE_TAB_ORDER


def workspace_tabs_for_request(request: Request) -> list[str]:
    if has_workspace_full_access(request):
        return list(FULL_WORKSPACE_TAB_ORDER)
    return []


def workspace_tab_url(tab: str, **params: str) -> str:
    query = {"tab": tab}
    for key, value in params.items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            query[key] = text
    return "/workspace?" + urlencode(query)
