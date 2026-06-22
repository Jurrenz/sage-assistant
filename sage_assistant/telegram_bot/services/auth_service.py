from __future__ import annotations


class AuthService:
    def __init__(self, allowed_chat_ids: set[int]) -> None:
        self.allowed_chat_ids = allowed_chat_ids

    def is_allowed(self, chat_id: int | None) -> bool:
        if not self.allowed_chat_ids:
            return chat_id is not None
        return chat_id is not None and chat_id in self.allowed_chat_ids
