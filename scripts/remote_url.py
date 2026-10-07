#!/usr/bin/env python3
"""Git remote URL 正規化：只回傳不含 credential side-channel 的 canonical remote。

獨立成可安裝模組，讓 collect-run-metrics.py 與 development-baseline.py 在安裝後的
CODEX_HOME 也能使用，不依賴只服務 repo 的 policy_check.py。
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


SCP_REMOTE = re.compile(r"^git@([A-Za-z0-9.-]+):([^\s?#]+)$")


def sanitize_remote_url(value: str) -> tuple[str | None, str | None]:
    """只回傳不含 credential side-channel 的 canonical remote；失敗時不回顯原值。"""
    if not value or any(character.isspace() for character in value):
        return None, "remote_url_unsafe"
    if value.startswith("/"):
        return (value, None) if "?" not in value and "#" not in value else (None, "remote_url_unsafe")
    scp_match = SCP_REMOTE.fullmatch(value)
    if scp_match:
        return value, None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "ssh"}
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
            or parsed.password is not None
            or (parsed.scheme == "https" and parsed.username is not None)
        ):
            return None, "remote_url_unsafe"
        if parsed.username is not None and not re.fullmatch(r"[A-Za-z0-9._-]+", parsed.username):
            return None, "remote_url_unsafe"
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        netloc = f"{parsed.username}@" if parsed.username else ""
        netloc += host
        if parsed.port is not None:
            netloc += f":{parsed.port}"
        normalized = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
        return normalized, None
    except (TypeError, ValueError):
        return None, "remote_url_unsafe"
