from __future__ import annotations

import os
from pathlib import Path

import requests


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    load_env(Path(".env"))
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set.")

    response = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    print("FOUND_CHATS")
    seen: set[int] = set()
    for update in data.get("result", []):
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not chat_id or chat_id in seen:
            continue
        seen.add(chat_id)
        title = (
            chat.get("title")
            or chat.get("username")
            or f"{chat.get('first_name', '')} {chat.get('last_name', '')}".strip()
        )
        print(f"id={chat_id} type={chat.get('type')} title={title}")


if __name__ == "__main__":
    main()
