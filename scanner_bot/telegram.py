"""Thin client for the Telegram Bot API (only the methods the bot needs)."""

from __future__ import annotations

from typing import Any

from .http import HttpError, post_json


class TelegramError(RuntimeError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class TelegramClient:
    def __init__(self, token: str, api_base: str = "https://api.telegram.org"):
        self._url = f"{api_base.rstrip('/')}/bot{token}/"

    def call(self, method: str, params: dict[str, Any] | None = None, http_timeout: float = 30.0) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            resp = post_json(self._url + method, params, timeout=http_timeout)
        except HttpError as e:
            body = e.body if isinstance(e.body, dict) else {}
            retry = (body.get("parameters") or {}).get("retry_after")
            desc = body.get("description") or str(e)
            raise TelegramError(f"{method}: {desc}", retry_after=retry) from e
        if not resp.get("ok"):
            retry = (resp.get("parameters") or {}).get("retry_after")
            raise TelegramError(f"{method}: {resp.get('description')}", retry_after=retry)
        return resp["result"]

    def get_me(self) -> dict:
        return self.call("getMe")

    def get_updates(self, offset: int | None, poll_timeout: int) -> list[dict]:
        return self.call(
            "getUpdates",
            {"offset": offset, "timeout": poll_timeout, "allowed_updates": ["message"]},
            http_timeout=poll_timeout + 15,
        )

    def reply(self, chat_id: int, message_id: int, text: str) -> dict:
        return self.call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "reply_parameters": {"message_id": message_id, "allow_sending_without_reply": True},
            },
        )
