from pathlib import Path

import httpx
import pytest

from maktabdl.core import MaktabClient, choose_video_url, parse_course_url, safe_name
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
