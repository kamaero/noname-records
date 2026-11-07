"""Каталог фильтруется на сервере, preview не выдаётся за исходник, сеть подменена."""
import copy
import json

import pytest
import requests

from app.services.openverse_client import OpenverseClient, OpenverseError, normalize_audio, public_url

IDENTITY = "b16ea2da-12ab-4f60-933b-9967807d66c3"


def audio(**kwargs):
    return {"id": IDENTITY, "title": "Тихий лес", "creator": "Автор", "provider": "wikimedia",
            "foreign_landing_url": "https://commons.wikimedia.org/wiki/File:Forest.wav",
            "url": "https://upload.wikimedia.org/forest.wav", "license": "by", "license_version": "4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/", "duration": 63000,
            "attribution": "Тихий лес — Автор, CC BY 4.0", **kwargs}


class Response:
    def __init__(self, body, status=200, headers=None):
        self.body, self.status_code, self.headers = body, status, headers or {}
        self.content = json.dumps(body).encode()

    def json(self):
        return copy.deepcopy(self.body)


def test_filters_jointly_limit_commercial_and_adaptation_and_cache_is_isolated():
    calls = []
    def transport(url, **kwargs):
        calls.append((url, kwargs))
        return Response({"results": [audio(category=None)], "page_count": 2})
    client = OpenverseClient(transport=transport)
    result = client.search("forest")
    assert result["results"][0]["duration_seconds"] == 63
    assert result["results"][0]["category"] == ""
    result["results"][0]["title"] = "Changed"
    assert client.search("forest")["results"][0]["title"] == "Тихий лес"
    assert len(calls) == 1
    assert calls[0][1]["params"]["license_type"] == "commercial,modification"
    assert set(calls[0][1]["params"]["license"].split(",")) == {"cc0", "by", "by-sa", "pdm"}
    assert "category" not in calls[0][1]["params"]
    assert calls[0][1]["allow_redirects"] is False
    assert client.search("forest", 2)["page"] == 2
    assert len(calls) == 2


@pytest.mark.parametrize("license,url,version", [
    ("by-nc", "https://creativecommons.org/licenses/by-nc/4.0/", "4.0"),
    ("by-nd", "https://creativecommons.org/licenses/by-nd/4.0/", "4.0"),
    ("sampling+", "https://creativecommons.org/licenses/sampling+/1.0/", "1.0"),
    ("unknown", "", ""), ("by", "", "4.0"), ("by", "https://example.com/license", "4.0"),
    ("by", "https://creativecommons.org/licenses/by-nc/4.0/", "4.0"),
    ("by", "https://creativecommons.org/licenses/by/4.0/", ""),
])
def test_disallowed_or_incomplete_license_is_not_a_permitted_search_result(license, url, version):
    raw = audio(license=license, license_url=url, license_version=version)
    assert normalize_audio(raw)["rights_status"] == "unknown"
    client = OpenverseClient(transport=lambda url, **kw: Response(raw if url.endswith(IDENTITY + "/") else {"results": [raw]}))
    assert client.search("forest")["results"] == []
    with pytest.raises(ValueError, match="unsupported_license"):
        client.detail(IDENTITY)


def test_sharealike_is_separately_labeled_and_original_urls_preserved():
    row = normalize_audio(audio(license="by-sa", license_url="https://creativecommons.org/licenses/by-sa/4.0/"))
    assert row["rights_status"] == "share_alike"
    assert "ShareAlike" in row["rights_warning"]
    assert row["download_url"] == "https://upload.wikimedia.org/forest.wav"
    assert row["preview_url"] == row["download_url"]


def test_freesound_preview_is_not_a_download_and_api_auth_url_is_not_promised():
    row = normalize_audio(audio(provider="freesound", url="https://cdn.freesound.org/previews/580/580072-hq.mp3",
        alt_files=[{"url": "https://freesound.org/apiv2/sounds/580072/download/", "filetype": "wav"}]))
    assert row["preview_url"].endswith("-hq.mp3")
    assert row["download_url"] == ""
    assert row["source_url"]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "http://127.0.0.1/a",
    "http://169.254.169.254/a", "https://[::1]/x", "http://localhost/x", "http://host.internal/x",
    "https://user:pass@example.org/file", "https://example.org:bad/a", "https://example.org/a\nfoo"])
def test_urls_are_safe_browser_links_not_server_fetches(url):
    assert public_url(url) == ""


@pytest.mark.parametrize("body,status,code", [([],200,"openverse_bad_response"),
    ({},200,"openverse_bad_response"), ({},429,"openverse_rate_limited"), ({},500,"openverse_unavailable")])
def test_upstream_errors_are_explicit(body, status, code):
    client = OpenverseClient(transport=lambda *a, **kw: Response(body, status))
    with pytest.raises(OpenverseError) as exc:
        client.search("forest")
    assert exc.value.code == code


def test_timeout_and_bad_json_are_not_empty_successes():
    def timeout(*a, **kw):
        raise requests.Timeout()
    with pytest.raises(OpenverseError):
        OpenverseClient(transport=timeout).search("forest")
    class Invalid(Response):
        def json(self):
            raise ValueError("bad JSON")
    with pytest.raises(OpenverseError):
        OpenverseClient(transport=lambda *a, **kw: Invalid({})).search("forest")


def test_detail_cannot_be_redirected_to_arbitrary_url():
    calls = []
    client = OpenverseClient(transport=lambda *a, **kw: calls.append(a))
    with pytest.raises(ValueError):
        client.detail("http://127.0.0.1/secret")
    assert not calls


@pytest.mark.parametrize("license,url,version", [
    ("cc0", "https://creativecommons.org/publicdomain/zero/1.0/", "1.0"),
    ("pdm", "https://creativecommons.org/publicdomain/mark/1.0/", "1.0"),
    ("by", "https://creativecommons.org/licenses/by/2.5/", "2.5"),
])
def test_known_public_domain_and_by_25_are_recognized(license, url, version):
    row = normalize_audio(audio(license=license, license_version=version, license_url=url))
    assert row["rights_status"] == "compatible"
    assert row["license_version"] == version


def test_cache_expires_and_empty_results_are_successful():
    now, calls = [0], []
    def transport(*a, **kw):
        calls.append(a)
        return Response({"results": [], "page_count": 0})
    client = OpenverseClient(transport=transport, ttl=1, clock=lambda: now[0])
    assert client.search("quiet")["results"] == []
    client.search("quiet")
    assert len(calls) == 1
    now[0] = 2
    client.search("quiet")
    assert len(calls) == 2
