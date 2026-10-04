from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from . import __version__
from .core import login_and_save, run_download

EXAMPLES = """Examples:
  maktabdl login -u you@example.com -p 'Secret123' -o ~/.config/maktabdl
  maktabdl download 'https://maktabkhooneh.org/course/<slug>/' -s ~/.config/maktabdl/session.json
  maktabdl download 'https://maktabkhooneh.org/lms/course/<slug>/unit/<id>/' --quality 1080 -o ./videos -f my_course
  maktabdl download 'https://maktabkhooneh.org/course/<slug>/' --quality 480 --retry 5 --timeout 90
  maktabdl download 'https://maktabkhooneh.org/course/<slug>/' --sample-bytes 65536
"""


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="maktabdl", description="Async Maktabkhooneh course downloader", epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    root.add_argument("--version", action="version", version=__version__)
    sub = root.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login", help="log in and save a session", description="Log in and save cookies in <dir>/session.json.", epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    login.add_argument("-u", "--username", required=True, help="account email or username")
    login.add_argument("-p", "--password", required=True, help="account password")
    login.add_argument("-o", "--output-dir", type=Path, default=Path.cwd(), help="session directory (default: current directory)")
    login.add_argument("--retry", type=int, default=3, help="retries after the first request (default: 3)")
    login.add_argument("--timeout", type=float, default=60.0, help="request timeout in seconds (default: 60)")
    login.set_defaults(handler="login")
    download = sub.add_parser("download", help="download a course", description="Download all accessible lecture videos and related files.", epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    download.add_argument("course_url", help="legacy or LMS course URL")
    download.add_argument("--quality", type=int, choices=(480, 720, 1080), default=720, help="preferred video quality (default: 720)")
    download.add_argument("-s", "--session-file", type=Path, default=Path.cwd() / "session.json", help="exact session file (default: ./session.json)")
    download.add_argument("-o", "--output-dir", type=Path, default=Path.cwd() / "download", help="parent output directory (default: ./download)")
    download.add_argument("-f", "--filename", help="course folder name override")
    download.add_argument("--sample-bytes", type=int, default=0, help="download only the first N bytes of each video")
    download.add_argument("--concurrency", type=int, default=4, help="maximum concurrent HTTP operations (default: 4)")
    download.add_argument("--retry", type=int, default=3, help="retries after the first request (default: 3)")
    download.add_argument("--timeout", type=float, default=60.0, help="request timeout in seconds (default: 60)")
    download.add_argument("-v", "--verbose", action="store_true", help="enable verbose diagnostics")
    download.set_defaults(handler="download")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.handler == "login":
            if args.retry < 0 or args.timeout <= 0:
                raise ValueError("retry must be non-negative and timeout must be positive")
            asyncio.run(login_and_save(args.username, args.password, args.output_dir / "session.json", args.retry, args.timeout))
        else:
            if args.sample_bytes < 0 or args.concurrency < 1 or args.retry < 0 or args.timeout <= 0:
                raise ValueError("sample-bytes must be non-negative, concurrency must be positive, retry must be non-negative, and timeout must be positive")
            asyncio.run(run_download(args.course_url, args.session_file, args.output_dir, args.filename, args.quality, args.sample_bytes, args.concurrency, args.retry, args.timeout, args.verbose))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
