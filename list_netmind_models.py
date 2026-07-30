from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

import httpx


DEFAULT_BASE_URL = "https://stream-netmind.viettel.vn/gateway/v1"


def extract_models(payload: Any) -> list[dict[str, Any]]:
    """Normalize an OpenAI-compatible /models response."""
    if isinstance(payload, dict):
        models = payload.get("data")
    else:
        models = payload

    if not isinstance(models, list):
        raise ValueError(
            "Gateway response does not contain a model list in the 'data' field."
        )

    normalized: list[dict[str, Any]] = []
    for item in models:
        if isinstance(item, str):
            normalized.append({"id": item})
        elif isinstance(item, dict) and item.get("id"):
            normalized.append(item)

    return sorted(normalized, key=lambda item: str(item["id"]).lower())


def format_created(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "-"
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    except (OverflowError, OSError, ValueError):
        return str(value)


def print_table(models: list[dict[str, Any]]) -> None:
    rows = [
        (
            str(index),
            str(model.get("id", "-")),
            str(model.get("owned_by", "-")),
            format_created(model.get("created")),
        )
        for index, model in enumerate(models, start=1)
    ]
    headers = ("STT", "MODEL ID", "OWNED BY", "CREATED")
    widths = [
        max(len(headers[column]), *(len(row[column]) for row in rows))
        for column in range(len(headers))
    ]

    def line(values: tuple[str, ...]) -> str:
        return " | ".join(
            value.ljust(widths[column]) for column, value in enumerate(values)
        )

    print(line(headers))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(line(row))


def get_api_key(variable_name: str, no_prompt: bool) -> str | None:
    api_key = os.getenv(variable_name) or os.getenv("OPENAI_API_KEY")
    if api_key or no_prompt:
        return api_key
    return getpass.getpass(
        f"Nhap API key NetMind/Viettel (khong hien tren man hinh): "
    ).strip()


def fetch_models(
    *,
    base_url: str,
    api_key: str,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/models"
    response = httpx.get(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        timeout=timeout_seconds,
        follow_redirects=True,
    )

    if response.status_code == 401:
        raise PermissionError(
            "Gateway tu choi xac thuc (HTTP 401). "
            "Hay kiem tra lai API key NetMind/Viettel."
        )
    if response.status_code == 403:
        raise PermissionError(
            "API key khong co quyen xem models (HTTP 403)."
        )

    response.raise_for_status()
    return extract_models(response.json())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="List available models from the NetMind/Viettel gateway."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key-env", default="NETMIND_API_KEY")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print complete model metadata as JSON.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="Fail instead of asking for a key when the environment variable is absent.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    api_key = get_api_key(args.api_key_env, args.no_prompt)
    if not api_key:
        print(
            f"Khong co API key. Hay dat bien moi truong "
            f"{args.api_key_env}.",
            file=sys.stderr,
        )
        return 2

    try:
        models = fetch_models(
            base_url=args.base_url,
            api_key=api_key,
            timeout_seconds=args.timeout_seconds,
        )
    except PermissionError as exc:
        print(f"Loi: {exc}", file=sys.stderr)
        return 3
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:500]
        print(
            f"Gateway tra ve HTTP {exc.response.status_code}: {body}",
            file=sys.stderr,
        )
        return 4
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        print(f"Khong the lay danh sach model: {exc}", file=sys.stderr)
        return 5

    if args.json:
        print(json.dumps(models, ensure_ascii=False, indent=2))
    elif models:
        print_table(models)
        print(f"\nTong cong: {len(models)} model")
    else:
        print("Gateway khong tra ve model nao.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
