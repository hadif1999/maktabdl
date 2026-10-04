# maktabdl

> **The best CLI tool for legally downloading Maktabkhooneh courses.**

`maktabdl` is a fast, feature-rich asynchronous command-line downloader for
Maktabkhooneh. It is designed for learners who want a dependable way to save
course videos and related material for offline use while respecting their
access rights. It uses `httpx` for high-performance HTTP requests, bounded
concurrency for fast downloads, and `uv` for reproducible Python environments.

Only download material that you are legally allowed to access.

## Why maktabdl?

- **Fast by design:** asynchronous downloads with configurable concurrency.
- **Quality control:** request 480p, 720p, or 1080p, with clear fallback behavior.
- **Reliable transfers:** retries, timeouts, resume support, and `.part` files.
- **Complete course archives:** videos, subtitles, and available attachments.
- **Readable organization:** chapter-aware progress, quality-aware names, and
  clean Persian/English filenames.
- **Simple workflow:** one login command, reusable sessions, and helpful CLI
  examples built into `--help`.

If you have legitimate access to Maktabkhooneh content, `maktabdl` gives you a
polished, scriptable, and privacy-conscious way to manage your personal course
archive.

## Quick start

Install [uv](https://docs.astral.sh/uv/) if it is not already installed, then
install the project:

```bash
uv sync
```

Log in and save a reusable session. The `-o` value is a directory; the command
creates `session.json` inside it:

```bash
uv run maktabdl login \
  -u you@example.com \
  -p 'your-password' \
  -o ~/.config/maktabdl
```

Download a course using the exact session-file path:

```bash
uv run maktabdl download \
  'https://maktabkhooneh.org/lms/course/<slug>/unit/<unit-id>/' \
  -s ~/.config/maktabdl/session.json \
  --quality 720
```

The default output is `./maktabdl_downloads/course-720p-<course_name>/`. Names begin with an ASCII
prefix and quality marker so mixed Persian/English names remain left-to-right in file
managers. Customize the output with `-o` and
the course folder name with `-f`:

```bash
uv run maktabdl download 'https://maktabkhooneh.org/course/<slug>/' \
  -s ~/.config/maktabdl/session.json \
  --quality 1080 \
  -o ./videos \
  -f my_course
```

See all options and more examples with:

```bash
uv run maktabdl --help
uv run maktabdl login --help
uv run maktabdl download --help
```

## Commands

### Login

```text
uv run maktabdl login -u USERNAME -p PASSWORD [-o SESSION_DIRECTORY]
```

The session file stores cookies in a multi-user JSON structure and is created
with private permissions. The default location is `./session.json`.

For better shell-history hygiene, prefer an environment variable or a prompt
wrapper when entering passwords rather than keeping a password in shell
history.

### Download

```text
uv run maktabdl download COURSE_URL [options]
```

- `-s`, `--session-file`: exact session file path; default `./session.json`.
- `--quality {480,720,1080}`: preferred video quality; default `720`.
- `-o`, `--output-dir`: parent output directory; default `./maktabdl_downloads`.
- `-f`, `--filename`: course folder name override.
- `--sample-bytes N`: save only the first `N` bytes of each video for a quick check.
- `--concurrency N`: maximum simultaneous HTTP operations; default `4`.
- `--retry N`: retries after the first failed request; default `3`.
- `--timeout N`: per-request timeout in seconds; default `60`.
- `-v`, `--verbose`: enable diagnostic output.

The downloader supports both legacy URLs and LMS URLs. It skips existing files,
resumes interrupted `.part` files with HTTP range requests, retries transient
failures, and downloads available subtitles and attachments beside each video.

## Quality and route behavior

For LMS courses, quality variants come from:

```text
/api/v1/lms/units/{unit_id}/video_url/
```

The requested resolution is selected exactly when available. If it is missing,
the nearest available resolution is selected and a warning is printed, followed
by the API's `hq`/`lq` fallback when necessary.

Legacy course pages expose video sources in HTML rather than a documented
resolution API. When a legacy source URL does not contain a resolution hint,
`maktabdl` warns and uses the available source.

Authentication currently uses the site's `/signin/` page followed by the
`/api/v1/auth/check-active-user` and `/api/v1/auth/login-authentication`
endpoints. Existing cookie overrides are also supported:

```bash
export MK_COOKIE='csrftoken=...; sessionid=...'
export MK_COOKIE_FILE="$HOME/.config/maktabdl/cookie.txt"
```

## Output layout

```text
maktabdl_downloads/
  course-720p-<course_name>/
    chapter-01-720p-<chapter>/
      video-01-720p-<lecture>.mp4
      video-01-720p-<lecture>.vtt
      video-01-720p-<lecture>--<attachment>
```

Quality is included immediately after the ASCII type/index prefix in course folders,
chapter folders, video filenames, subtitle filenames, and attachment prefixes. Sample
downloads use `-720p-<lecture>.sample.mp4` filenames. Interrupted full downloads keep a `.part` file
so a later run can continue from the saved offset. Folder names use the
requested quality; individual files use the selected quality when a fallback
was required.

In the terminal, progress bars are grouped by chapter and labeled with the
chapter first, for example `Chapter 01 / Introduction / video-01`.

## Development

Install development dependencies and run the targeted test suite:

```bash
uv sync --dev
uv run pytest -q
uv run python -m compileall -q src tests
```

The package entry point is `maktabdl.cli:main`, and the source lives under
`src/maktabdl/`.
