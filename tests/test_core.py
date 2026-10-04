import asyncio
from pathlib import Path

import httpx
import pytest

from maktabdl.core import CourseRef, MaktabClient, choose_video_url, parse_course_url, quality_suffix, safe_name
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


def test_ltr_download_names_anchor_mixed_persian_and_english():
    from maktabdl.core import download_name

    course = download_name("course", "آموزش agentic ai پایتون mk15783", 720)
    chapter = download_name("chapter-01", "مقدمه", 720)
    video = download_name("video-01", "معرفی agentic AI", 720)
    assert course == "course-720p-آموزش_agentic_ai_پایتون_mk15783"
    assert chapter == "chapter-01-720p-مقدمه"
    assert video == "video-01-720p-معرفی_agentic_AI"
    assert course[0].isascii() and video[0].isascii()
    assert "\u202a" not in course and "\u202b" not in course


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
async def test_async_download_updates_shared_progress(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": "5"}, content=b"hello", request=request)

    class RecordingProgress:
        def __init__(self):
            self.added = []
            self.updates = []

        def add_task(self, description, **kwargs):
            self.added.append((description, kwargs))
            return 7

        def update(self, task_id, **kwargs):
            self.updates.append((task_id, kwargs))

    client = MaktabClient(cookie="sessionid=test")
    await client.http.aclose()
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    progress = RecordingProgress()
    target = tmp_path / "video.mp4"
    try:
        assert await client.download(
            "https://cdn.example/video.mp4",
            target,
            "https://maktabkhooneh.org/",
            label="video-01",
            progress=progress,
        ) == "downloaded"
    finally:
        await client.close()

    assert progress.added == [("video-01", {"total": None})]
    assert progress.updates[-1] == (7, {"completed": 5})


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


@pytest.mark.asyncio
async def test_download_course_keeps_one_progress_display_until_all_jobs_finish(monkeypatch, tmp_path: Path):
    class RecordingProgress:
        instances = []

        def __init__(self, *args, **kwargs):
            self.events = []
            self.tasks = []
            self.__class__.instances.append(self)

        def __enter__(self):
            self.events.append("enter")
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self.events.append("exit")

        def add_task(self, description, **kwargs):
            self.tasks.append(description)
            return len(self.tasks)

        def update(self, task_id, **kwargs):
            return None

    monkeypatch.setattr("maktabdl.core.Progress", RecordingProgress)

    class FakeClient(MaktabClient):
        def __init__(self):
            super().__init__(concurrency=2)
            self.events = []

        async def verify(self, referer="://"):
            return {"auth": {"details": {"is_authenticated": True}}}

        async def outline(self, course):
            return {"chapters": [{"units": [{"id": 1, "type": 1}, {"id": 2, "type": 1}]}]}

        async def _download_unit(self, course, chapter, unit, directory, index, quality, sample_bytes, lms, *, progress=None):
            self.events.append(f"start-{unit['id']}")
            assert progress is RecordingProgress.instances[0]
            if unit["id"] == 1:
                self.events.append("failed")
                raise RuntimeError("one lecture failed")
            await asyncio.sleep(0.02)
            self.events.append("finished")

    client = FakeClient()
    course = CourseRef("https://maktabkhooneh.org/lms/course/test-mk1/unit/1/", "test-mk1", 1, True)
    try:
        with pytest.raises(RuntimeError, match="one lecture failed"):
            await client.download_course(course, tmp_path, None, 720, 0)
    finally:
        await client.close()

    assert client.events == ["start-1", "failed", "start-2", "finished"]
    assert [instance.events for instance in RecordingProgress.instances] == [["enter", "exit"]]
