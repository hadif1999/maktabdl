from pathlib import Path

import httpx
import pytest

from maktabdl.core import MaktabClient, choose_video_url, parse_course_url, quality_suffix, safe_name
from maktabdl.session import load_session, save_session, session_cookie


def test_parse_both_course_url_formats():
    legacy = parse_course_url("https://maktabkhooneh.org/course/python-mk123/")
    lms = parse_course_url("https://maktabkhooneh.org/lms/course/python-mk123/unit/99/")
    assert (legacy.slug, legacy.course_id, legacy.lms) == ("python-mk123", 123, False)
    assert (lms.slug, lms.course_id, lms.lms) == ("python-mk123", 123, True)


def test_quality_exact_and_nearest_fallback():
    data = {"qualities": [{"resolution": 480, "download_url": "480.mp4"}, {"resolution": 1080, "download_url": "1080.mp4"}]}
    assert choose_video_url(data, 1080)[:2] == ("1080.mp4", 1080)
    assert choose_video_url(data, 720)[:2] == ("480.mp4", 480)


def test_session_round_trip_and_legacy_file(tmp_path: Path):
    path = tmp_path / "nested" / "session.json"
    save_session(path, "User@Example.com", "csrftoken=x; sessionid=y")
    assert session_cookie(path, "user@example.com") == "csrftoken=x; sessionid=y"
    assert load_session(path)["lastUsed"] == "user@example.com"
    assert path.stat().st_mode & 0o077 == 0


def test_safe_name_uses_underscores_for_folder_defaults():
    assert safe_name("A course / part", underscores=True) == "A_course_part"
    assert safe_name("%D8%A2%D9%85%D9%88%D8%B2%D8%B4_agentic_ai", underscores=True) == "آموزش_agentic_ai"
    assert quality_suffix("lecture", 480) == "lecture_480p"
    assert quality_suffix("lecture_480p", 480) == "lecture_480p"


@pytest.mark.asyncio
async def test_async_download_writes_file(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": "5"}, content=b"hello", request=request)

    client = MaktabClient(cookie="sessionid=test")
    await client.http.aclose()
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    target = tmp_path / "video.mp4"
    try:
        assert await client.download("https://cdn.example/video.mp4", target, "https://maktabkhooneh.org/") == "downloaded"
    finally:
        await client.close()
    assert target.read_bytes() == b"hello"


@pytest.mark.asyncio
async def test_download_retries_transient_http_failure(tmp_path: Path):
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, content=b"temporary", request=request)
        return httpx.Response(200, headers={"content-length": "5"}, content=b"hello", request=request)

    client = MaktabClient(cookie="sessionid=test", retries=1)
    await client.http.aclose()
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    target = tmp_path / "retry.mp4"
    try:
        assert await client.download("https://cdn.example/retry.mp4", target, "https://maktabkhooneh.org/") == "downloaded"
    finally:
        await client.close()
    assert calls == 2
    assert target.read_bytes() == b"hello"


@pytest.mark.asyncio
async def test_download_reports_non_retryable_http_error(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"missing", request=request)

    client = MaktabClient(cookie="sessionid=test", retries=3)
    await client.http.aclose()
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeError, match="HTTP 404"):
            await client.download("https://cdn.example/missing.mp4", tmp_path / "missing.mp4", "https://maktabkhooneh.org/")
    finally:
        await client.close()
