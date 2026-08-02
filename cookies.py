import json
from pathlib import Path
from typing import Iterable

def parse_cookies(
    cookies_file: str | Path = "cookies.json",
    allowed_names: Iterable[str] | None = None,
) -> str:
    values = get_cookies(cookies_file)
    if allowed_names is not None:
        allowed = set(allowed_names)
        values = {name: value for name, value in values.items() if name in allowed}
    return "; ".join(f"{name}={value}" for name, value in values.items())

def get_cookies(cookies_file: str | Path = "cookies.json") -> dict[str, str]:
    with Path(cookies_file).open("r", encoding="utf-8") as file:
        raw = json.load(file)
    if not isinstance(raw, list):
        raise ValueError("cookies.json 必须是浏览器导出的 Cookie 数组")
    return {
        cookie["name"]: cookie["value"]
        for cookie in raw
        if isinstance(cookie, dict)
        and isinstance(cookie.get("name"), str)
        and isinstance(cookie.get("value"), str)
    }

