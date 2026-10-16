#!/usr/bin/env python3
"""Locate and fetch Yuque documents without exposing the access token."""

import argparse
import datetime as dt
import hashlib
import json
import mimetypes
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    from .runtime import configure_stdio, ledger_lock, state_dir
except ImportError:
    from runtime import configure_stdio, ledger_lock, state_dir

API_BASE = "https://www.yuque.com/api/v2"
USER_AGENT = "yuque-note-publisher/0.1.0"
SHANGHAI_TZ = dt.timezone(dt.timedelta(hours=8))
DEFAULT_DAILY_BUDGET = 45
DEFAULT_USAGE_FILE = state_dir() / "usage.json"


def get_token():
    token = os.environ.get("YUQUE_PERSONAL_TOKEN") or os.environ.get("YUQUE_TOKEN")
    if token:
        return token
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                for name in ("YUQUE_PERSONAL_TOKEN", "YUQUE_TOKEN"):
                    try:
                        value, _ = winreg.QueryValueEx(key, name)
                        if value:
                            return value
                    except FileNotFoundError:
                        pass
        except OSError:
            pass
    raise RuntimeError("YUQUE_PERSONAL_TOKEN is not configured")


def read_daily_budget():
    raw = os.environ.get("YUQUE_API_DAILY_BUDGET", str(DEFAULT_DAILY_BUDGET))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("YUQUE_API_DAILY_BUDGET must be an integer") from exc
    if value < 0:
        raise RuntimeError("YUQUE_API_DAILY_BUDGET must be zero or greater")
    return value


class UsageLedger:
    """Persist a conservative local count of Yuque API requests across conversations."""

    def __init__(self, path=None, budget=None):
        configured_path = os.environ.get("YUQUE_API_USAGE_FILE")
        self.path = pathlib.Path(path or configured_path or DEFAULT_USAGE_FILE).expanduser()
        self.budget = read_daily_budget() if budget is None else budget

    @staticmethod
    def today():
        return dt.datetime.now(SHANGHAI_TZ).date().isoformat()

    def load(self):
        today = self.today()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Cannot read Yuque API usage ledger: {}".format(exc)) from exc
        if data.get("date") != today:
            return {"date": today, "tracked_calls": 0, "daily_budget": self.budget}
        data["tracked_calls"] = int(data.get("tracked_calls", 0))
        data["daily_budget"] = self.budget
        return data

    def save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.path)

    def reserve_api_call(self):
        with ledger_lock(self.path):
            data = self.load()
            if data["tracked_calls"] >= self.budget:
                raise RuntimeError(
                    "LOCAL_DAILY_BUDGET_REACHED: tracked {} Yuque API calls on {}; "
                    "stop and resume after the Shanghai date changes".format(
                        data["tracked_calls"], data["date"]
                    )
                )
            data["tracked_calls"] += 1
            self.save(data)
            return data

    def status(self):
        data = self.load()
        remaining = max(self.budget - data["tracked_calls"], 0)
        return {
            "date": data["date"],
            "tracked_calls": data["tracked_calls"],
            "daily_budget": self.budget,
            "tracked_remaining": remaining,
            "usage_file": str(self.path),
            "note": "Local count covers only requests made by this script; other Yuque clients are not visible.",
        }


def is_api_url(url):
    parsed = urllib.parse.urlsplit(url)
    return (
        parsed.scheme == "https" and parsed.netloc == "www.yuque.com"
        and (parsed.path == "/api/v2" or parsed.path.startswith("/api/v2/"))
    )


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if is_api_url(req.full_url) and not is_api_url(newurl):
            raise RuntimeError("Authenticated API redirect outside Yuque API was blocked")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class YuqueClient:
    def __init__(self, token, min_interval=2.5, usage_ledger=None):
        self.token = token
        self.min_interval = min_interval
        self.last_api_request = 0.0
        self.usage_ledger = usage_ledger or UsageLedger()
        self.opener = urllib.request.build_opener(SafeRedirectHandler())

    def _throttle_api(self, url):
        if not is_api_url(url):
            return
        elapsed = time.monotonic() - self.last_api_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_api_request = time.monotonic()

    def request(self, path_or_url, binary=False):
        url = path_or_url if path_or_url.startswith("http") else API_BASE + path_or_url
        is_api = is_api_url(url)
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*" if binary else "application/json",
            "Referer": "https://www.yuque.com/",
        }
        if is_api:
            headers["X-Auth-Token"] = self.token
        last_error = None
        for attempt in range(3):
            try:
                self._throttle_api(url)
                if is_api:
                    self.usage_ledger.reserve_api_call()
                req = urllib.request.Request(url, headers=headers)
                with self.opener.open(req, timeout=25) as response:
                    data = response.read()
                    if binary:
                        return data, response.headers.get("Content-Type", "")
                    return json.loads(data.decode("utf-8"))["data"]
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code == 429 and is_api:
                    retry_after = exc.headers.get("Retry-After")
                    if attempt == 0 and retry_after and retry_after.isdigit() and int(retry_after) <= 60:
                        delay = int(retry_after)
                        print(
                            "request deferred after HTTP 429; retrying once in {} seconds".format(delay),
                            file=sys.stderr,
                            flush=True,
                        )
                        time.sleep(delay)
                        continue
                    raise RuntimeError(
                        "YUQUE_RATE_LIMITED: stop all new Yuque API requests and resume later"
                    ) from exc
                if not (500 <= exc.code < 600):
                    raise
                if is_api:
                    raise RuntimeError(
                        "YUQUE_SERVER_ERROR: API request was not automatically retried; "
                        "stop and let the user decide whether to retry"
                    ) from exc
                if attempt == 2:
                    break
                delay = 2 ** attempt
                print(
                    "request deferred after HTTP {}; retrying in {} seconds".format(exc.code, delay),
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(min(delay, 240))
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                if is_api:
                    raise RuntimeError(
                        "YUQUE_NETWORK_ERROR: API request may have reached the server and was "
                        "not automatically retried; inspect local usage before continuing"
                    ) from exc
                time.sleep(2 ** attempt)
        raise RuntimeError("Yuque request failed after 3 attempts: {}".format(last_error))

    def docs(self, namespace):
        all_docs = []
        offset = 0
        while True:
            path = "/repos/{}/docs?offset={}&limit=100".format(namespace, offset)
            page = self.request(path)
            all_docs.extend(page)
            if len(page) < 100:
                return all_docs
            offset += 100

    def doc(self, namespace, doc_id):
        validate_namespace(namespace)
        return self.request("/repos/{}/docs/{}".format(namespace, urllib.parse.quote(str(doc_id), safe="")))

    def toc(self, namespace):
        return self.request("/repos/{}/toc".format(namespace))


def parse_time(value):
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    return dt.datetime.fromisoformat(value)


def parse_yuque_url(value):
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or parsed.netloc.lower() not in (
        "yuque.com",
        "www.yuque.com",
    ):
        raise ValueError("Expected a https://www.yuque.com/<owner>/<repo>/<document> URL")
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) != 3 or any("/" in part for part in parts):
        raise ValueError("Yuque URL must include owner, repository, and document slug")
    validate_namespace(parts[0] + "/" + parts[1])
    return parts[0] + "/" + parts[1], parts[2]


def validate_namespace(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_-]+", value):
        raise ValueError("Namespace must be an explicit owner/knowledge-base pair")
    return value


def locate(client, titles, since, namespace):
    wanted = set(titles or [])
    since_dt = parse_time(since) if since else None
    results = []
    for doc in client.docs(namespace):
        title_match = not wanted or doc.get("title") in wanted
        updated = parse_time(doc.get("updated_at"))
        time_match = since_dt is None or (updated is not None and updated >= since_dt)
        if title_match and time_match:
            results.append({
                "id": doc.get("id"),
                "title": doc.get("title"),
                "slug": doc.get("slug"),
                "namespace": namespace,
                "created_at": doc.get("created_at"),
                "updated_at": doc.get("updated_at"),
            })
    results.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return results


def select_toc(client, namespace, titles, include_descendants=True):
    nodes = client.toc(namespace)
    wanted = set(titles or [])
    if not wanted:
        return nodes
    targets = [node for node in nodes if node.get("title") in wanted]
    if not include_descendants:
        return targets
    selected_ids = {str(node.get("uuid")) for node in targets}
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if str(node.get("parent_uuid")) in selected_ids:
                node_id = str(node.get("uuid"))
                if node_id not in selected_ids:
                    selected_ids.add(node_id)
                    changed = True
    return [node for node in nodes if str(node.get("uuid")) in selected_ids]


MD_IMAGE = re.compile(r"!\[([^\]]*)\]\((https?://[^\s\)]+)(?:\s+[\"'][^\)]*[\"'])?\)")
HTML_IMAGE = re.compile(r"(<img\b[^>]*?\bsrc=[\"'])(https?://[^\"']+)([\"'][^>]*>)", re.I)


def extension_for(content_type, url):
    clean_type = content_type.split(";", 1)[0].strip().lower()
    overrides = {"image/jpeg": ".jpg", "image/svg+xml": ".svg", "image/webp": ".webp"}
    ext = overrides.get(clean_type) or mimetypes.guess_extension(clean_type)
    if not ext:
        ext = pathlib.PurePosixPath(urllib.parse.urlparse(url).path).suffix.lower()
    if not ext or len(ext) > 6 or not re.match(r"^\.[a-z0-9]+$", ext):
        ext = ".bin"
    return ext


def localize_images(client, markdown, output_path, assets_dir, manifest_path=None):
    assets_dir.mkdir(parents=True, exist_ok=True)
    cache = {}
    persistent_cache = {}
    if manifest_path and manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                persistent_cache = loaded
        except (OSError, ValueError):
            persistent_cache = {}

    def download(url):
        if url in cache:
            return cache[url]
        cached_name = persistent_cache.get(url)
        if cached_name:
            cached_target = assets_dir / cached_name
            if cached_target.resolve().parent != assets_dir.resolve():
                raise RuntimeError("Image cache path escapes the asset directory")
            if cached_target.is_file() and cached_target.stat().st_size > 0:
                relative = pathlib.Path(
                    os.path.relpath(str(cached_target), str(output_path.parent))
                ).as_posix()
                cache[url] = relative
                return relative
        data, content_type = client.request(url, binary=True)
        if not data:
            raise RuntimeError("Downloaded image is empty")
        if not content_type.lower().startswith("image/"):
            raise RuntimeError("Image URL returned non-image content")
        digest = hashlib.sha256(data).hexdigest()[:12]
        target = assets_dir / (digest + extension_for(content_type, url))
        if not target.exists():
            target.write_bytes(data)
        relative = pathlib.Path(os.path.relpath(str(target), str(output_path.parent))).as_posix()
        cache[url] = relative
        if manifest_path:
            persistent_cache[url] = target.name
            write_json_atomic(manifest_path, persistent_cache)
        return relative

    def replace_md(match):
        return "![{}]({})".format(match.group(1), download(match.group(2)))

    def replace_html(match):
        return match.group(1) + download(match.group(2)) + match.group(3)

    markdown = MD_IMAGE.sub(replace_md, markdown)
    markdown = HTML_IMAGE.sub(replace_html, markdown)
    return markdown, len(cache)


def fetch(client, namespace, doc_id, output, assets_dir):
    output_path = pathlib.Path(output).resolve()
    assets_path = pathlib.Path(assets_dir).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    source_cache = output_path.with_suffix(output_path.suffix + ".source-cache.json")
    image_manifest = output_path.with_suffix(output_path.suffix + ".image-cache.json")
    resumed_from_cache = False
    if source_cache.exists():
        cached = json.loads(source_cache.read_text(encoding="utf-8"))
        if cached.get("namespace") != namespace or cached.get("selection") != str(doc_id):
            raise RuntimeError("Source cache belongs to a different document; use a new output path")
        doc = cached["document"]
        resumed_from_cache = True
    else:
        doc = client.doc(namespace, doc_id)
        write_json_atomic(source_cache, {
            "namespace": namespace, "selection": str(doc_id), "document": doc,
        })
    body = doc.get("body") or ""
    if not body.strip():
        raise RuntimeError("Yuque document body is empty")
    localized, image_count = localize_images(
        client,
        body,
        output_path,
        assets_path,
        manifest_path=image_manifest,
    )
    temp = output_path.with_suffix(output_path.suffix + ".tmp")
    temp.write_text(localized.rstrip() + "\n", encoding="utf-8")
    temp.replace(output_path)
    source_cache.unlink(missing_ok=True)
    image_manifest.unlink(missing_ok=True)
    result = {
        "id": doc.get("id"),
        "title": doc.get("title"),
        "slug": doc.get("slug"),
        "namespace": namespace,
        "source_url": "https://www.yuque.com/{}/{}".format(namespace, doc.get("slug")),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "output": str(output_path),
        "assets_dir": str(assets_path),
        "image_count": image_count,
        "content_sha256": hashlib.sha256(localized.encode("utf-8")).hexdigest(),
        "resumed_from_cache": resumed_from_cache,
    }
    write_json_atomic(output_path.with_suffix(".meta.json"), result)
    return result


def write_json_atomic(path, value):
    output_path = pathlib.Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_suffix(output_path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(output_path)
    return output_path


def main():
    configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    locate_parser = subparsers.add_parser("locate")
    locate_parser.add_argument("--repo", required=True, type=validate_namespace)
    locate_parser.add_argument("--title", action="append", default=[])
    locate_parser.add_argument("--since")
    toc_parser = subparsers.add_parser("toc")
    toc_parser.add_argument("--repo", required=True, type=validate_namespace)
    toc_parser.add_argument("--title", action="append", default=[])
    toc_parser.add_argument(
        "--output",
        help="save the complete TOC JSON locally and print only a compact summary",
    )
    toc_parser.add_argument(
        "--exact-only",
        action="store_true",
        help="return matching TOC nodes without recursively selecting descendants",
    )
    fetch_parser = subparsers.add_parser("fetch")
    fetch_parser.add_argument("--repo")
    fetch_parser.add_argument("--doc")
    fetch_parser.add_argument("--url", help="exact Yuque document URL; replaces --repo and --doc")
    fetch_parser.add_argument("--output", required=True)
    fetch_parser.add_argument("--assets-dir", required=True)
    localize_parser = subparsers.add_parser(
        "localize",
        help="localize remote images in a user-provided Markdown file without calling Yuque API",
    )
    localize_parser.add_argument("--input", required=True)
    localize_parser.add_argument("--output", required=True)
    localize_parser.add_argument("--assets-dir", required=True)
    subparsers.add_parser("usage", help="show locally tracked API usage without calling Yuque")
    parser.add_argument("--min-interval", type=float, default=2.5)
    args = parser.parse_args()

    try:
        usage_ledger = UsageLedger()
        if args.command == "usage":
            result = usage_ledger.status()
            json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
            return 0
        if args.command == "localize":
            input_path = pathlib.Path(args.input).expanduser().resolve()
            output_path = pathlib.Path(args.output).expanduser().resolve()
            markdown = input_path.read_text(encoding="utf-8")
            if not markdown.strip():
                raise RuntimeError("Input Markdown is empty")
            client = YuqueClient("", min_interval=0, usage_ledger=usage_ledger)
            localized, image_count = localize_images(
                client,
                markdown,
                output_path,
                pathlib.Path(args.assets_dir).expanduser().resolve(),
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temp = output_path.with_suffix(output_path.suffix + ".tmp")
            temp.write_text(localized.rstrip() + "\n", encoding="utf-8")
            temp.replace(output_path)
            result = {
                "input": str(input_path),
                "output": str(output_path),
                "assets_dir": str(pathlib.Path(args.assets_dir).expanduser().resolve()),
                "image_count": image_count,
            }
            json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
            return 0
        client = YuqueClient(
            get_token(),
            min_interval=max(args.min_interval, 0.0),
            usage_ledger=usage_ledger,
        )
        if args.command == "locate":
            result = locate(client, args.title, args.since, args.repo)
        elif args.command == "toc":
            result = select_toc(
                client,
                args.repo,
                args.title,
                include_descendants=not args.exact_only,
            )
            if args.output:
                output_path = write_json_atomic(args.output, result)
                result = {
                    "namespace": args.repo,
                    "node_count": len(result),
                    "output": str(output_path),
                }
        else:
            if args.url:
                if args.repo or args.doc:
                    raise RuntimeError("Use either --url or the --repo/--doc pair, not both")
                namespace, doc_id = parse_yuque_url(args.url)
            else:
                if not args.repo or not args.doc:
                    raise RuntimeError("fetch requires --url or both --repo and --doc")
                namespace, doc_id = args.repo, args.doc
            result = fetch(client, namespace, doc_id, args.output, args.assets_dir)
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    except Exception as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
