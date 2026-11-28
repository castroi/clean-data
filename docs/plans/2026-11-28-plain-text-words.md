# Plain-text custom words (replaces the /replace command)

**Goal:** Let users add words to remove by just sending them, without commands, and without retyping them for every file — while automatic PII removal always stays on.

**Supersedes:** `2026-04-07-replace-command.md`. That plan made `/replace` switch automatic PII detection off; a user who added one missed name silently lost removal of emails, phones and IDs. This plan makes custom words additive.

## Key decisions

- **Text = words.** Any text that is not a control word is a comma/newline separated list of words to add. Spaces stay inside a phrase (`Dana Cohen`).
- **`-word` removes one word.** No state, works on both keyboards. No "remove by number": numbers clash with ID/phone words.
- **Whole-message control words**, English + Hebrew, exact match (idea from train-notifier `route.ts`): `0 / clear / נקה / ביטול`, `list / רשימה`, `help / עזרה / ? / /help / /start` and greetings.
- **Never stored as words:** chat filler (`thanks / תודה / ok ...`), emoji/punctuation only, one character, links, unknown `/commands`.
- **No 1/2 confirmation.** Words are added immediately and echoed back; a wrong one is undone with `-word`.
- **Caption on a file** adds its words quietly, then the file is cleaned; one caption covers every file in that message. A caption item of more than 3 words (reads like a sentence) gets a reply, since it is saved as one phrase.
- **Session stays** (many files in a row with the same names): in memory only, sliding TTL — every message or file restarts it. Default 2 h (was 1 h, refreshed only on `/replace`). Word cap 30 (was 15).
- **Don't retype, don't retain:** `list` answers with one copyable line; pasting it back restores the list. The user's own Signal history is the storage; the bot keeps nothing on disk.
- **Every file gets a summary message** saying what it was cleaned with (automatic PII + the words in use). No counts: in PDFs a count of text matches need not equal what was blanked, and the owner doesn't need them. Sent as a **separate** message — the file's caption travels when forwarded, so it stays PII-free (`DONE_MESSAGE`).
- **Matching:** whole-word, case-insensitive, NFC-normalized; the parts of a phrase match across any whitespace (line breaks in PDF text); Hebrew words also match after up to three prefix letters (ו ה ב ל מ ש כ), the prefix is kept. Hebrew niqqud is not handled (not expected in the documents).
- **One removal pass:** custom-word and automatic matches are found on the same original text, merged, and deleted together, so removing a custom word can't hide a name from the automatic detector.
- **Hard cap:** a list is cleared 24 h after it started even if in use (`MAX_WORD_SESSION_AGE_SECONDS`), so activity can't keep PII in memory forever.
- **Legacy:** `/replace a, b` still adds the words with a "no command needed" hint; `/end` clears.

## Changes

- `processor/custom_word_detector.py` — `CustomWordDetector(words, base)`: base = automatic detector; boundary-aware patterns; one instance per file.
- `processor/pipeline.py` — `detector` property exposing the loaded PIIDetector.
- `word_session_store.py` — case-insensitive dedupe in insertion order, `remove_words`, `touch`, `add_words` returns `(added, existing)`; failed first add leaves no empty session.
- `signal_bot.py` — `_handle_text` replaces `_handle_command`; `HELP_MESSAGE` with current status; summary after each sent file.
- Config defaults: `WORD_SESSION_TTL_SECONDS=7200`, `MAX_WORDS_PER_SENDER=30`.

## Tests

- `tests/test_custom_words_flow.py` — conversation flow with a fake pipeline (no NLP), plus DOCX end to end.
- `tests/test_custom_words_pipeline.py` — real pipeline + PDFs (replaces `test_replace_command.py`).
- `tests/test_custom_word_detector.py`, `tests/test_word_session_store.py` — rewritten for the new behaviour.

## Known limitation / follow-up


PDF redaction blanks each match via `page.search_for(text)`, which is a plain substring search on the page, so a custom word can still be blanked inside a longer word in PDFs (detection itself is whole-word). Possible follow-up (not planned): redact from `page.get_text("words")` positions instead.
