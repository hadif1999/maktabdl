from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
from html.parser import HTMLParser

import httpx
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, DownloadColumn, TimeRemainingColumn

from .session import cookie_header, load_session, save_session, session_cookie

ORIGIN = "https://maktabkhooneh.org"
UA = "maktabdl/0.1 (+https://maktabkhooneh.org/)"
console = Console()


@dataclass(slots=True)
class CourseRef:
    url: str
    slug: str
    course_id: int | None
    lms: bool


def parse_course_url(value: str) -> CourseRef:
    parsed = urlparse(value if value.endswith("/") else f"{value}/")
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"maktabkhooneh.org", "www.maktabkhooneh.org"}:
        raise ValueError("course URL must point to maktabkhooneh.org")
    parts = [part for part in parsed.path.split("/") if part]
    try:
        if parts[:2] == ["lms", "course"]:
            slug = parts[2]
            lms = True
        else:
            index = parts.index("course")
            slug = parts[index + 1]
            lms = False
    except (ValueError, IndexError):
        raise ValueError("expected /course/<slug>/ or /lms/course/<slug>/unit/<id>/") from None
    match = re.search(r"-mk(\d+)$", slug, re.I)
    return CourseRef(value if value.endswith("/") else f"{value}/", slug, int(match.group(1)) if match else None, lms)


def safe_name(value: str, underscores: bool = False) -> str:
    value = unquote(value)
    value = re.sub(r"[/:*?\"<>|\\]", " ", value)
    value = re.sub(r"[\s\u200c\u200f\u202a-\u202e]+", "_" if underscores else " ", value)
    return value.strip(" ._")[:150] or "course"


def quality_suffix(value: str, quality: int) -> str:
    """Append a stable quality suffix to a file or directory component."""
    suffix = f"_{quality}p"
    return value if value.endswith(suffix) else f"{value}{suffix}"


def download_name(prefix: str, value: str, quality: int) -> str:
    """Build a filename component with an ASCII left-to-right anchor.

    The prefix and quality come first so file managers render mixed Persian
    and English names predictably while retaining the original title.
    """
    return f"{prefix}-{quality}p-{safe_name(value, underscores=True)}"


def _quality_number(item: dict) -> int | None:
    for key in ("resolution", "height", "quality"):
        value = item.get(key)
        match = re.search(r"\d{3,4}", str(value)) if value is not None else None
        if match:
            return int(match.group())
    match = re.search(r"(?:^|[^0-9])(480|720|1080)(?:p)?(?:[^0-9]|$)", str(item.get("download_url", "")), re.I)
    return int(match.group(1)) if match else None


def choose_video_url(data: dict | None, requested: int) -> tuple[str | None, int | None, str | None]:
    if not data:
        return None, None, "video URL response was empty"
    candidates = [item for item in data.get("qualities", []) if isinstance(item, dict) and item.get("download_url")]
    numbered = [(item, _quality_number(item)) for item in candidates]
    numbered = [(item, quality) for item, quality in numbered if quality]
    if numbered:
        exact = next(((item, quality) for item, quality in numbered if quality == requested), None)
        if exact:
            return exact[0]["download_url"], exact[1], None
        item, quality = min(numbered, key=lambda pair: (abs(pair[1] - requested), pair[1] > requested, pair[1]))
        return item["download_url"], quality, f"requested {requested}p unavailable; using {quality}p"
    urls = data.get("video_urls") or {}
    url = urls.get("hq") or urls.get("lq")
    return url, None, f"requested {requested}p unavailable; using fallback source" if url else "no downloadable video URL"


class LectureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sources: list[str] = []
        self.tracks: list[str] = []
        self.attachments: list[str] = []
        self._attachment_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = values.get("class", "") or ""
        if tag == "source" and values.get("src"):
            self.sources.append(urljoin(ORIGIN, html.unescape(values["src"])))
        if tag == "track" and values.get("src"):
            self.tracks.append(urljoin(ORIGIN, html.unescape(values["src"])))
        if tag == "div" and "unit-content--download" in classes:
            self._attachment_depth += 1
        if tag == "a" and self._attachment_depth and values.get("href") and "attachment" in values["href"].lower():
            self.attachments.append(urljoin(ORIGIN, html.unescape(values["href"])))

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._attachment_depth:
            self._attachment_depth -= 1


class MaktabClient:
    def __init__(self, cookie: str | None = None, timeout: float = 60.0, retries: int = 3, concurrency: int = 4, verbose: bool = False) -> None:
        self.cookie = cookie
        self.timeout = timeout
        self.retries = max(0, retries)
        self.verbose = verbose
        self.sem = asyncio.Semaphore(max(1, concurrency))
        self.http = httpx.AsyncClient(follow_redirects=True, timeout=timeout, headers={"user-agent": UA, "accept-language": "en-US,en;q=0.9,fa;q=0.8"})

    def headers(self, referer: str | None = None, accept: str = "*/*") -> dict[str, str]:
        headers = {"accept": accept, "cache-control": "no-cache", "pragma": "no-cache", "x-requested-with": "XMLHttpRequest"}
        if self.cookie:
            headers["cookie"] = self.cookie
        if referer:
            headers["referer"] = referer
        return headers

    async def close(self) -> None:
        await self.http.aclose()

    @staticmethod
    def _retryable_status(status: int) -> bool:
        return status == 408 or status == 429 or status >= 500

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        attempts = self.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                async with self.sem:
                    response = await self.http.request(method, url, **kwargs)
                if response.status_code >= 400:
                    if self._retryable_status(response.status_code) and attempt < attempts:
                        await response.aclose()
                        await asyncio.sleep(min(attempt, 5))
                        continue
                    detail = response.text[:300].strip().replace("\n", " ")
                    await response.aclose()
                    raise RuntimeError(
                        f"{method} {url} failed with HTTP {response.status_code}"
                        + (f": {detail}" if detail else "")
                    )
                return response
            except httpx.RequestError as error:
                if attempt >= attempts:
                    raise RuntimeError(
                        f"{method} {url} failed after {self.retries} retries: {error}"
                    ) from error
                await asyncio.sleep(min(attempt, 5))
        raise RuntimeError(f"{method} {url} failed unexpectedly")

    async def json(self, url: str, referer: str, **kwargs) -> dict:
        response = await self._request("GET", url, headers=self.headers(referer, "application/json"), **kwargs)
        try:
            return response.json()
        except ValueError as error:
            raise RuntimeError(f"GET {url} returned invalid JSON") from error
        finally:
            await response.aclose()

    async def verify(self, referer: str = ORIGIN) -> dict:
        return await self.json(f"{ORIGIN}/api/v1/general/core-data/?profile=1", referer)

    async def login(self, email: str, password: str) -> str:
        # The current Nuxt frontend uses /signin; the old Django page
        # /accounts/login/ now returns 404.
        response = await self._request("GET", f"{ORIGIN}/signin/", headers={"accept": "text/html", "user-agent": UA})
        response_text = response.text
        await response.aclose()
        csrf = self.http.cookies.get("csrftoken")
        if not csrf:
            match = re.search(r'name=["\']csrfmiddlewaretoken["\'][^>]+value=["\']([^"\']+)', response_text, re.I)
            csrf = match.group(1) if match else None
        if not csrf:
            try:
                core_response = await self._request("GET", f"{ORIGIN}/api/v1/general/core-data/?profile=1", headers={"accept": "application/json"})
                csrf = core_response.json().get("auth", {}).get("csrf")
                await core_response.aclose()
            except (RuntimeError, ValueError):
                csrf = None
        if not csrf:
            raise RuntimeError("could not obtain CSRF token")
        headers = {"accept": "application/json, text/javascript, */*; q=0.01", "content-type": "application/x-www-form-urlencoded; charset=UTF-8", "x-requested-with": "XMLHttpRequest", "x-csrftoken": csrf, "origin": ORIGIN, "referer": f"{ORIGIN}/signin/"}
        check = await self._request("POST", f"{ORIGIN}/api/v1/auth/check-active-user", headers=headers, data={"csrfmiddlewaretoken": csrf, "tessera": email, "g-recaptcha-response": ""})
        try:
            check_data = check.json()
        finally:
            await check.aclose()
        if check_data.get("status") != "success" or check_data.get("message") != "get-pass":
            raise RuntimeError(f"active-user check failed: {check_data.get('message', check_data.get('status'))}")
        login = await self._request("POST", f"{ORIGIN}/api/v1/auth/login-authentication", headers=headers, data={"csrfmiddlewaretoken": csrf, "tessera": email, "hidden_username": email, "password": password, "g-recaptcha-response": ""})
        try:
            data = login.json()
        finally:
            await login.aclose()
        if data.get("status") != "success":
            raise RuntimeError(f"login failed: {data.get('message', data.get('status'))}")
        cookies = {
            item.name: item.value
            for item in self.http.cookies.jar
            if item.name in {"csrftoken", "sessionid"}
        }
        if "sessionid" not in cookies:
            raise RuntimeError("login succeeded but sessionid cookie was not returned")
        self.cookie = cookie_header(cookies)
        return self.cookie

    async def outline(self, course: CourseRef) -> dict:
        if course.course_id:
            try:
                data = await self.json(f"{ORIGIN}/api/v1/lms/courses/{course.course_id}/outline/", course.url)
                if isinstance(data.get("chapters"), list):
                    return data
            except (httpx.HTTPError, RuntimeError) as error:
                if "HTTP 401" in str(error) or "HTTP 403" in str(error):
                    raise
                pass
        return await self.json(f"{ORIGIN}/api/v1/courses/{course.slug}/chapters/", course.url)

    async def unit_data(self, unit_id: int, referer: str) -> tuple[dict | None, dict | None]:
        details, video = await asyncio.gather(
            self.json(f"{ORIGIN}/api/v1/lms/units/{unit_id}/", referer),
            self.json(f"{ORIGIN}/api/v1/lms/units/{unit_id}/video_url/", referer),
            return_exceptions=True,
        )
        return (details if isinstance(details, dict) else None, video if isinstance(video, dict) else None)

    async def fetch_legacy(self, url: str, referer: str) -> LectureParser:
        response = await self._request("GET", url, headers=self.headers(referer, "text/html"))
        try:
            parser = LectureParser()
            parser.feed(response.text)
            return parser
        finally:
            await response.aclose()

    async def download(self, url: str, target: Path, referer: str, sample_bytes: int = 0, label: str = "") -> str:
        if target.exists() and target.stat().st_size > 0 and not sample_bytes:
            return "exists"
        if sample_bytes and target.exists() and target.stat().st_size >= sample_bytes:
            return "exists"
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        offset = 0 if sample_bytes else (part.stat().st_size if part.exists() else 0)
        headers = self.headers(referer, "video/mp4,application/octet-stream,*/*")
        if sample_bytes:
            headers["range"] = f"bytes=0-{sample_bytes - 1}"
        elif offset:
            headers["range"] = f"bytes={offset}-"
        attempts = self.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                async with self.sem:
                    async with self.http.stream("GET", url, headers=headers) as response:
                        if response.status_code >= 400:
                            detail = (await response.aread()).decode(errors="replace")[:200].strip().replace("\n", " ")
                            if self._retryable_status(response.status_code) and attempt < attempts:
                                await asyncio.sleep(min(attempt, 5))
                                continue
                            raise RuntimeError(
                                f"download {url} failed with HTTP {response.status_code}"
                                + (f": {detail}" if detail else "")
                            )
                        if offset and response.status_code != 206:
                            part.unlink(missing_ok=True)
                            offset = 0
                            headers.pop("range", None)
                            raise RuntimeError("server did not honor range; restarting")
                        response.raise_for_status()
                        mode = "wb" if not offset else "ab"
                        total = response.headers.get("content-length")
                        total_bytes = int(total) + offset if total and not sample_bytes else (sample_bytes or None)
                        downloaded = offset
                        with part.open(mode) as output:
                            with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(), TaskProgressColumn(), DownloadColumn(), TimeRemainingColumn(), transient=True, console=console) as progress:
                                task = progress.add_task(label or target.name, total=total_bytes, completed=downloaded)
                                async for chunk in response.aiter_bytes(1024 * 128):
                                    if sample_bytes and downloaded + len(chunk) > sample_bytes:
                                        chunk = chunk[: sample_bytes - downloaded]
                                    if not chunk:
                                        break
                                    output.write(chunk)
                                    downloaded += len(chunk)
                                    progress.update(task, completed=downloaded)
                                    if sample_bytes and downloaded >= sample_bytes:
                                        break
                part.replace(target)
                return "downloaded"
            except (httpx.HTTPError, OSError, RuntimeError) as error:
                if attempt >= attempts:
                    raise RuntimeError(
                        f"download failed for {target} after {self.retries} retries: {error}"
                    ) from error
                await asyncio.sleep(attempt)
        raise AssertionError("unreachable")

    async def download_course(self, course: CourseRef, output: Path, folder_name: str | None, quality: int, sample_bytes: int) -> None:
        profile = await self.verify(course.url)
        if not profile.get("auth", {}).get("details", {}).get("is_authenticated"):
            raise RuntimeError("session is invalid or expired; run `maktabdl login` again")
        data = await self.outline(course)
        chapters = data.get("chapters", [])
        folder = folder_name or course.slug.replace("-", " ")
        root = output / download_name("course", folder, quality)
        root.mkdir(parents=True, exist_ok=True)
        lms = course.lms or any(isinstance(c.get("units"), list) for c in chapters if isinstance(c, dict))
        for chapter_index, chapter in enumerate(chapters, 1):
            units = chapter.get("units") if lms else chapter.get("unit_set", [])
            if not isinstance(units, list):
                continue
            chapter_name = str(chapter.get("title") or chapter.get("slug") or "chapter")
            chapter_dir = root / download_name(f"chapter-{chapter_index:02d}", chapter_name, quality)
            jobs = []
            for unit_index, unit in enumerate(units, 1):
                if unit.get("status") is False or unit.get("locked") is True:
                    continue
                is_video = unit.get("type") == 1 if lms else unit.get("type") == "lecture"
                if not is_video:
                    continue
                jobs.append(self._download_unit(course, chapter, unit, chapter_dir, unit_index, quality, sample_bytes, lms))
            await asyncio.gather(*jobs)

    async def _download_unit(self, course: CourseRef, chapter: dict, unit: dict, directory: Path, index: int, quality: int, sample_bytes: int, lms: bool) -> None:
        title = str(unit.get("title") or unit.get("slug") or "lecture")
        base = f"video-{index:02d}"
        lecture_url = f"{ORIGIN}/lms/course/{course.slug}/unit/{unit.get('id') or unit.get('unit_id')}/" if lms else f"{ORIGIN}/course/{course.slug}/{chapter.get('slug')}-ch{chapter.get('id')}/{unit.get('slug')}/"
        caption = None
        attachments: list[str] = []
        if lms:
            details, video = await self.unit_data(unit.get("id") or unit.get("unit_id"), course.url)
            video_url, selected, warning = choose_video_url(video, quality)
            if warning:
                console.print(f"[yellow]Quality: {base}: {warning}[/yellow]")
            caption = details.get("caption_file") if details and details.get("has_caption") else None
            attachments = [r.get("download_url") for r in (details or {}).get("resources", []) if isinstance(r, dict) and r.get("type") != 1 and r.get("download_url")]
        else:
            parser = await self.fetch_legacy(lecture_url, course.url)
            video_url = next((url for url in parser.sources if "/videos/" in url), None)
            selected = _quality_number({"download_url": video_url}) if video_url else None
            caption = parser.tracks[0] if parser.tracks else None
            attachments = parser.attachments
            if video_url and not re.search(r"(?:480|720|1080)", video_url):
                console.print(f"[yellow]Quality: {base}: legacy source has no explicit resolution; using available source[/yellow]")
        if not video_url:
            console.print(f"[yellow]Skipping {base}: no downloadable video URL[/yellow]")
            return
        file_base = download_name(base, title, selected or quality)
        final = directory / (file_base + (".sample.mp4" if sample_bytes else ".mp4"))
        status = await self.download(video_url, final, lecture_url, sample_bytes, base)
        console.print(f"[green]{status.upper()}: {final}[/green]")
        if caption:
            subtitle_name = file_base + ".vtt"
            caption_url = caption
            if "file=" in caption_url and caption_url.endswith("file="):
                caption_url += subtitle_name
            await self.download(caption_url, directory / subtitle_name, lecture_url, label=subtitle_name)
        for attachment in attachments:
            filename = Path(urlparse(attachment).path).name or "attachment.bin"
            await self.download(attachment, directory / f"{file_base}--{safe_name(filename, underscores=True)}", lecture_url, label=filename)


async def login_and_save(email: str, password: str, session_file: Path, retries: int = 3, timeout: float = 60.0) -> None:
    client = MaktabClient(retries=retries, timeout=timeout)
    try:
        cookie = await client.login(email, password)
        save_session(session_file, email, cookie, load_session(session_file))
        console.print(f"[green]Login successful; session saved to {session_file}[/green]")
    finally:
        await client.close()


async def run_download(url: str, session_file: Path, output: Path, folder_name: str | None, quality: int, sample_bytes: int, concurrency: int, retries: int, timeout: float, verbose: bool) -> None:
    cookie = session_cookie(session_file)
    if not cookie:
        import os
        cookie = os.getenv("MK_COOKIE")
        if not cookie and os.getenv("MK_COOKIE_FILE"):
            cookie = Path(os.environ["MK_COOKIE_FILE"]).read_text(encoding="utf-8").strip()
    if not cookie:
        raise RuntimeError(f"no session found at {session_file}; run `maktabdl login ... -o {session_file.parent}` first")
    course = parse_course_url(url)
    client = MaktabClient(cookie=cookie, concurrency=concurrency, retries=retries, timeout=timeout, verbose=verbose)
    try:
        await client.download_course(course, output, folder_name, quality, sample_bytes)
    finally:
        await client.close()
