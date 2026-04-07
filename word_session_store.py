import time


class WordSessionStore:
    """Per-sender custom word storage with configurable TTL.

    Words accumulate across multiple /replace commands.
    TTL is refreshed on each /replace command for that sender.
    Default TTL is 3600 seconds (1 hour), configurable via WORD_SESSION_TTL_SECONDS
    environment variable (passed in through Config).
    """

    DEFAULT_TTL_SECONDS = 3600  # 1 hour

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_words: int = 15,
        max_word_length: int = 100,
        max_sessions: int = 100,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_words = max_words
        self._max_word_length = max_word_length
        self._max_sessions = max_sessions
        # {sender: {"words": set[str], "expires_at": float}}
        self._sessions: dict[str, dict] = {}

    def add_words(self, sender: str, words: list[str]) -> list[str]:
        """Add words for a sender. Returns the full current word list.

        Raises ValueError if limits are exceeded.
        """
        self._prune_expired()
        cleaned = [w.strip()[:self._max_word_length] for w in words if w.strip()]
        if not cleaned:
            return self.get_words(sender)
        if sender not in self._sessions:
            if len(self._sessions) >= self._max_sessions:
                raise ValueError("Too many active sessions. Please try again later.")
            self._sessions[sender] = {"words": set(), "expires_at": 0}
        current = self._sessions[sender]["words"]
        if len(current) + len(cleaned) > self._max_words:
            raise ValueError(
                f"Word limit exceeded. Maximum {self._max_words} words per session."
            )
        current.update(cleaned)
        self._sessions[sender]["expires_at"] = time.time() + self._ttl_seconds
        return sorted(current)

    def get_words(self, sender: str) -> list[str]:
        """Get active words for a sender. Returns empty list if expired/none."""
        self._prune_expired()
        session = self._sessions.get(sender)
        if not session:
            return []
        return sorted(session["words"])

    def clear(self, sender: str) -> None:
        """Clear all words for a sender (/end command)."""
        self._sessions.pop(sender, None)

    def has_active_session(self, sender: str) -> bool:
        """Check if sender has active custom words."""
        self._prune_expired()
        return sender in self._sessions and bool(self._sessions[sender]["words"])

    def _prune_expired(self) -> None:
        """Remove expired sessions."""
        now = time.time()
        expired = [s for s, d in self._sessions.items() if d["expires_at"] <= now]
        for s in expired:
            del self._sessions[s]
