import time
import unicodedata


def _normalize(word: str) -> str:
    """NFC, inner whitespace collapsed (same as CustomWordDetector)."""
    return " ".join(unicodedata.normalize("NFC", word).split())


class WordSessionStore:
    """Per-sender custom word list, in memory only, with a sliding TTL.

    Words accumulate across messages. The TTL is refreshed on every message
    or file from that sender (``touch``), so a long batch of files keeps the
    list; it lapses after ``ttl_seconds`` of silence, and in any case
    ``max_age_seconds`` after the list was started (activity can't keep
    words, which are PII, in memory forever). Words are compared
    case-insensitively; the first spelling sent is kept. Nothing is written
    to disk, so a restart also clears every list.
    """

    DEFAULT_TTL_SECONDS = 7200  # 2 hours of inactivity
    DEFAULT_MAX_AGE_SECONDS = 86400  # hard cap: 24 hours after the list started

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_words: int = 30,
        max_word_length: int = 100,
        max_sessions: int = 100,
        max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_age_seconds = max_age_seconds
        self._max_words = max_words
        self._max_word_length = max_word_length
        self._max_sessions = max_sessions
        # {sender: {"words": dict[lowercased, original], "expires_at": float, "hard_expiry": float}}
        self._sessions: dict[str, dict] = {}

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    @property
    def max_words(self) -> int:
        return self._max_words

    def add_words(self, sender: str, words: list[str]) -> tuple[list[str], list[str]]:
        """Add words for a sender. Returns (newly added, already on the list).

        Nothing is added if the result would exceed the word limit.
        Raises ValueError if limits are exceeded.
        """
        self._prune_expired()
        cleaned: dict[str, str] = {}
        for w in words:
            w = _normalize(w)[:self._max_word_length].strip()
            if w and w.lower() not in cleaned:
                cleaned[w.lower()] = w
        if not cleaned:
            return [], []
        if sender not in self._sessions:
            if len(self._sessions) >= self._max_sessions:
                raise ValueError("Too many active sessions. Please try again later.")
            now = time.time()
            self._sessions[sender] = {"words": {}, "expires_at": 0, "hard_expiry": now + self._max_age_seconds}
        current: dict[str, str] = self._sessions[sender]["words"]
        new = {k: v for k, v in cleaned.items() if k not in current}
        existing = [v for k, v in cleaned.items() if k in current]
        if len(current) + len(new) > self._max_words:
            if not current:
                del self._sessions[sender]
            raise ValueError(f"Word limit reached ({self._max_words}).")
        current.update(new)
        self._extend(self._sessions[sender])
        return list(new.values()), existing

    def remove_words(self, sender: str, words: list[str]) -> tuple[list[str], list[str]]:
        """Remove words (case-insensitive). Returns (removed, not on the list).

        An emptied list ends the session.
        """
        self._prune_expired()
        session = self._sessions.get(sender)
        current: dict[str, str] = session["words"] if session else {}
        removed, missing = [], []
        for w in words:
            w = _normalize(w)
            if not w:
                continue
            if w.lower() in current:
                removed.append(current.pop(w.lower()))
            else:
                missing.append(w)
        if session is not None and not current:
            del self._sessions[sender]
        return removed, missing

    def get_words(self, sender: str) -> list[str]:
        """Active words for a sender, in the order added. Empty if expired/none."""
        self._prune_expired()
        session = self._sessions.get(sender)
        if not session:
            return []
        return list(session["words"].values())

    def touch(self, sender: str) -> None:
        """Restart the TTL of a live session (never revives an expired one)."""
        self._prune_expired()
        session = self._sessions.get(sender)
        if session:
            self._extend(session)

    def _extend(self, session: dict) -> None:
        """Restart the inactivity TTL, never past the hard expiry."""
        session["expires_at"] = min(time.time() + self._ttl_seconds, session["hard_expiry"])

    def clear(self, sender: str) -> None:
        """Clear all words for a sender."""
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
