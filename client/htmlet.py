#!/usr/bin/env python3
"""htmlet CLI — upload / list / get / delete HTML pages.

Standard library only (no dependencies), so it runs anywhere Python 3.8+ exists.

Config via flags or environment:
    HTMLET_SERVER   base URL, e.g. https://share.example.com
    HTMLET_KEY      upload API key

Examples:
    htmlet upload page.html                 # auto slug -> html-N
    htmlet upload page.html --slug landing  # explicit slug (overwrites)
    htmlet list
    htmlet get landing -o out.html
    htmlet delete landing
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _server(args: argparse.Namespace) -> str:
    server = (args.server or os.environ.get("HTMLET_SERVER", "")).rstrip("/")
    if not server:
        sys.exit("error: no server set (pass --server or set HTMLET_SERVER)")
    return server


def _key(args: argparse.Namespace) -> str:
    key = args.key or os.environ.get("HTMLET_KEY", "")
    if not key:
        sys.exit("error: no API key set (pass --key or set HTMLET_KEY)")
    return key


def _request(
    method: str, url: str, *, data: bytes | None = None, key: str | None = None
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method=method)
    if key:
        req.add_header("X-API-Key", key)
    if data is not None:
        req.add_header("Content-Type", "text/html; charset=utf-8")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        sys.exit(f"error: could not reach server: {exc.reason}")


def _print_json_or_fail(status: int, body: bytes) -> dict:
    try:
        parsed = json.loads(body or b"{}")
    except json.JSONDecodeError:
        sys.exit(
            f"error: unexpected response ({status}): {body[:200].decode('utf-8', 'replace')}"
        )
    if status >= 400:
        sys.exit(f"error ({status}): {parsed.get('detail', parsed)}")
    return parsed


def cmd_upload(args: argparse.Namespace) -> None:
    server, key = _server(args), _key(args)
    try:
        data = (
            sys.stdin.buffer.read()
            if args.file == "-"
            else Path(args.file).read_bytes()
        )
    except OSError as exc:
        sys.exit(f"error: cannot read {args.file}: {exc}")
    if args.slug:
        status, body = _request(
            "PUT", f"{server}/api/files/{args.slug}", data=data, key=key
        )
    else:
        status, body = _request("POST", f"{server}/api/upload", data=data, key=key)
    result = _print_json_or_fail(status, body)
    print(result["url"])


def cmd_list(args: argparse.Namespace) -> None:
    server, key = _server(args), _key(args)
    query = []
    if args.limit is not None:
        query.append(f"limit={args.limit}")
    if args.offset:
        query.append(f"offset={args.offset}")
    query_string = "?" + "&".join(query) if query else ""
    url = f"{server}/api/files{query_string}"
    status, body = _request("GET", url, key=key)
    result = _print_json_or_fail(status, body)
    files = result.get("files", [])
    if not files:
        print("(no files)")
        return
    for item in files:
        print(f"{item['slug']:<24} {item['size']:>9}  {item['url']}")
    total = result.get("total")
    if isinstance(total, int) and total > len(files):
        print(f"... showing {len(files)} of {total} (use --limit/--offset)")


def cmd_get(args: argparse.Namespace) -> None:
    server = _server(args)
    status, body = _request("GET", f"{server}/{args.slug}")
    if status >= 400:
        sys.exit(f"error ({status}): {body[:200].decode('utf-8', 'replace')}")
    if args.output:
        with open(args.output, "wb") as handle:
            handle.write(body)
        print(f"wrote {len(body)} bytes -> {args.output}")
    else:
        sys.stdout.buffer.write(body)


def cmd_delete(args: argparse.Namespace) -> None:
    server, key = _server(args), _key(args)
    status, body = _request("DELETE", f"{server}/api/files/{args.slug}", key=key)
    result = _print_json_or_fail(status, body)
    print(f"deleted {result['deleted']}")


def cmd_genkey(args: argparse.Namespace) -> None:
    print(secrets.token_hex(args.bytes))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="htmlet", description="htmlet CLI")
    parser.add_argument("--server", help="base URL (or HTMLET_SERVER)")
    parser.add_argument("--key", help="API key (or HTMLET_KEY)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_up = sub.add_parser("upload", help="upload an HTML file ('-' for stdin)")
    p_up.add_argument("file")
    p_up.add_argument(
        "--slug", help="explicit slug (overwrites); omit to auto-generate"
    )
    p_up.set_defaults(func=cmd_upload)

    p_ls = sub.add_parser("list", help="list uploaded pages")
    p_ls.add_argument(
        "--limit", type=int, default=None, help="max rows (server caps at 1000)"
    )
    p_ls.add_argument("--offset", type=int, default=0, help="skip the first N rows")
    p_ls.set_defaults(func=cmd_list)

    p_get = sub.add_parser("get", help="fetch a page's HTML")
    p_get.add_argument("slug")
    p_get.add_argument("-o", "--output", help="write to file instead of stdout")
    p_get.set_defaults(func=cmd_get)

    p_del = sub.add_parser("delete", help="delete a page")
    p_del.add_argument("slug")
    p_del.set_defaults(func=cmd_delete)

    p_gen = sub.add_parser(
        "genkey", help="generate a random API key to put in API_KEYS"
    )
    p_gen.add_argument(
        "--bytes", type=int, default=32, help="entropy in bytes (default 32)"
    )
    p_gen.set_defaults(func=cmd_genkey)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
