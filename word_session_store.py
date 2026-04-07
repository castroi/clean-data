import time


class WordSessionStore:
    """Per-sender custom word storage with configurable TTL.

    Words accumulate across multiple /replace commands.
    TTL is refreshed on each /replace command for that sender.
    Default TTL is 3600 seconds (1 hour), configurable via WORD_SESSION_TTL_SECONDS
    environment variable (passed in through Config).
    """

    DEFAULT_TTL_SECONDS = 3600  # 1 hour

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        # {sender: {"words": set[str], "expires_at": float}}
        self._sessions: dict[str, dict] = {}

    def add_words(self, sender: str, words: list[str]) -> list[str]:
        """Add words for a sender. Returns the full current word list."""
        self._prune_expired()
        cleaned = [w.strip() for w in words if w.strip()]
        if sender not in self._sessions:
            self._sessions[sender] = {"words": set(), "expires_at": 0}
        self._sessions[sender]["words"].update(cleaned)
        self._sessions[sender]["expires_at"] = time.time() + self._ttl_seconds
        return sorted(self._sessions[sender]["words"])

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
