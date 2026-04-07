# Security Audit Report: clean-data

**Project:** Privacy-first document sanitization tool (PII removal from PDF/DOCX via Signal)
**Date:** 2026-04-06
**Auditor:** Application Security Agent

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 1 |
| HIGH | 5 |
| MEDIUM | 9 |
| LOW | 4 |
| INFO | 2 |

**Verdict: FAIL — Do not deploy without addressing CRITICAL and HIGH findings.**

---

## Findings

### [CRITICAL] Path Traversal via Malicious Filename

**File:** `signal_bot.py:100`
**Issue:** The `filename` from the Signal attachment dict is user-controlled and used directly in path construction:
```python
input_path = self._temp_dir / f"input_{int(time.time())}_{filename}"
```
A malicious sender can craft an attachment with a filename like `../../etc/cron.d/evil` or `../../../root/.ssh/authorized_keys`. The `Path /` operator does not sanitize path separators in the filename component. After `write_bytes(content)`, an attacker can write arbitrary content to arbitrary filesystem locations the process user can access.

**Risk:** Arbitrary file write leading to remote code execution (e.g., writing cron jobs, SSH keys, or overwriting application code).

**Recommendation:** Sanitize the filename by extracting only the basename and stripping all path separators:
```python
safe_name = Path(filename).name  # strips directory components
safe_name = safe_name.replace("/", "").replace("\\", "").replace("\x00", "")
if not safe_name:
    safe_name = "attachment"
input_path = self._temp_dir / f"input_{int(time.time())}_{safe_name}"
```
Additionally, after constructing the path, verify it is still within `self._temp_dir`:
```python
if not input_path.resolve().is_relative_to(self._temp_dir.resolve()):
    raise ValueError("Invalid filename")
```

---

### [HIGH] PII Logged in detect_entities Return Value (Indirect Exposure)

**File:** `processor/pii_detector.py:141-150`
**Issue:** The `detect_entities` method returns the actual PII text in the result dict (`"text": text[r.start : r.end]`). This data flows to `pdf_cleaner.py:47` where `entity["text"]` is used. While not directly logged, the `entities` list is held in memory and any future debug logging, error traceback, or exception serialization could expose raw PII values.

**Risk:** PII appearing in log files, tracebacks, or error reports.

**Recommendation:** Remove the `"text"` field from entity dicts returned by `detect_entities`. Pass only start/end offsets and entity type. In `pdf_cleaner.py`, extract the text only where needed for `search_for()` and avoid storing it in dicts that could be serialized.

---

### [HIGH] ValidationError Message May Contain PII

**File:** `signal_bot.py:125-126`
**Issue:** When a `ValueError` is caught, its message is sent directly back to the user via Signal:
```python
except ValueError as e:
    logger.error("Validation error: %s", e)
    self._send_message(sender, str(e))
```
If any code path raises a `ValueError` that includes content from the document, PII would leak into both logs and Signal messages.

**Risk:** PII leakage through error messages as the codebase evolves.

**Recommendation:** Never log or return raw exception messages to users. Use generic error messages:
```python
except ValueError:
    self._send_message(sender, "Invalid file. Supported formats: PDF, DOCX.")
```

---

### [HIGH] File Size Check Uses Metadata, Not Actual Content

**File:** `signal_bot.py:88-95`
**Issue:** The file size check reads from `attachment.get("size", 0)` — a value provided by the Signal API (or potentially the sender). The actual content written at line 108 (`input_path.write_bytes(content)`) is never checked for size. If the `size` metadata is spoofed or inaccurate, an attacker could send an extremely large file that bypasses the check.

**Risk:** Denial of service through memory exhaustion or disk filling, which could also prevent secure deletion of other files if disk is full.

**Recommendation:** Check the actual length of the content bytes before writing:
```python
content = attachment.get("data", b"")
if isinstance(content, str):
    content = content.encode()
if len(content) > max_bytes:
    self._send_message(sender, "File too large.")
    return
input_path.write_bytes(content)
```

---

### [HIGH] No Rate Limiting on Message Processing

**File:** `signal_bot.py:60`, `signal_bot.py:142-167`
**Issue:** Any Signal user who knows the bot's phone number can send unlimited messages with file attachments. There is no per-sender rate limiting, no concurrency limit, and no allowlist. An attacker can flood the bot with many large files simultaneously, consuming CPU (NLP models), memory, and disk.

**Risk:** Denial of service. The NLP models (spaCy, heBERT) are CPU-intensive. The `signal.alarm` timeout mechanism only works for a single alarm at a time — concurrent processing would break the timeout protection.

**Recommendation:**
1. Add per-sender rate limiting (e.g., max 3 files per minute per sender).
2. Add a global concurrency limit (e.g., process one file at a time with a queue).
3. Consider an allowlist of authorized phone numbers if this is not a public service.
4. Replace `signal.alarm` with `threading.Timer` or `multiprocessing` timeout that supports concurrency.

---

### [HIGH] signal.alarm Is Not Thread-Safe and Only Works on Main Thread

**File:** `processor/pipeline.py:51-52`
**Issue:** `signal.alarm()` and `signal.signal()` only work in the main thread on Unix. If the bot ever runs processing in a background thread, the timeout mechanism will raise `ValueError: signal only works in main thread`. Even in single-threaded mode, `signal.alarm` is global state — if two files are processed sequentially while a previous alarm is still set, the second `signal.alarm()` cancels the first.

**Risk:** Processing timeout fails silently, allowing a maliciously crafted document to hang the service indefinitely.

**Recommendation:** Use `multiprocessing` with a timeout, or `concurrent.futures.ProcessPoolExecutor`:
```python
from concurrent.futures import ProcessPoolExecutor, TimeoutError
with ProcessPoolExecutor(max_workers=1) as pool:
    future = pool.submit(self._do_clean, input_path, output_path, ext)
    future.result(timeout=self._config.PROCESSING_TIMEOUT)
```

---

### [MEDIUM] Secure Delete Ineffective on Copy-on-Write / Journaling Filesystems

**File:** `utils/secure_delete.py:17-24`
**Issue:** The overwrite-then-unlink approach does not guarantee data destruction on modern filesystems. Ext4 (journaled), Btrfs, ZFS (CoW), and any SSD with wear-leveling will retain the original data blocks even after overwriting.

**Risk:** PII data may be recoverable from disk even after "secure deletion."

**Recommendation:**
1. Document this limitation clearly.
2. Use an encrypted tmpfs (ramfs/tmpfs) for `TEMP_DIR` so files never touch persistent storage — this is the strongest mitigation.
3. For SSD, the only reliable approach is full-disk encryption (LUKS/dm-crypt) so that deleted data is cryptographically unrecoverable.

---

### [MEDIUM] secure_delete_dir Skips Subdirectories

**File:** `utils/secure_delete.py:36-37`
**Issue:** `secure_delete_dir` only deletes files in the immediate directory (`item.is_file()`), then calls `rmdir()`. If any subdirectories exist (e.g., created by a library during processing), `rmdir()` will fail and those files will persist undeleted.

**Risk:** PII-containing temp files in subdirectories survive cleanup.

**Recommendation:** Use recursive deletion:
```python
def secure_delete_dir(dir_path: Path) -> None:
    if not dir_path.exists():
        return
    for item in sorted(dir_path.rglob("*"), reverse=True):
        if item.is_file():
            secure_delete(item)
        elif item.is_dir():
            item.rmdir()
    dir_path.rmdir()
```

---

### [MEDIUM] Temp Files Not Cleaned If Process Crashes or Is Killed

**File:** `signal_bot.py:132-135`, `config.py:14`
**Issue:** If the Python process is killed (SIGKILL, OOM killer, power loss), the `finally` block never runs and temp files with PII persist in `/tmp/clean-data`. There is no startup cleanup that purges stale temp files from previous runs.

**Risk:** PII persists on disk across restarts.

**Recommendation:**
1. On startup, securely delete all files in the temp directory before starting the message loop.
2. Use `atexit` handler as additional safety net.
3. Mount `TEMP_DIR` on tmpfs so files are lost on reboot.

---

### [MEDIUM] No File Magic / Content-Type Validation

**File:** `signal_bot.py:81-83`, `processor/pipeline.py:38`
**Issue:** File type is determined solely by extension. An attacker can send a file named `evil.pdf` that is actually a zip bomb, a polyglot file, or a specially crafted file designed to exploit PyMuPDF or python-docx parsing vulnerabilities.

**Risk:** Exploitation of parser vulnerabilities via malformed files. Potential for zip bomb attacks (DOCX is a ZIP archive) causing memory/disk exhaustion.

**Recommendation:**
1. Validate file magic bytes before processing (e.g., `python-magic` library).
2. Wrap `fitz.open()` and `Document()` calls in try/except to handle malformed files gracefully.
3. Consider running document parsing in a sandboxed subprocess with resource limits (`ulimit`).

---

### [MEDIUM] Predictable Temp Filenames Enable Local Symlink Attacks

**File:** `signal_bot.py:100`
**Issue:** The temp filename uses `int(time.time())` which is predictable. If an attacker has local access to the machine, they could predict the filename and create a symlink at that path before the bot writes to it.

**Risk:** With local access, arbitrary file overwrite via symlink race.

**Recommendation:** Use cryptographic randomness:
```python
import secrets
random_id = secrets.token_hex(8)
input_path = self._temp_dir / f"input_{random_id}{ext}"
```

---

### [MEDIUM] Uncontrolled Exception Details May Leak to Logs

**File:** `signal_bot.py:129`
**Issue:** `logger.error("Processing failed: %s", e)` logs the full exception message for any unexpected error. If PyMuPDF or python-docx raises an exception that includes document content in the message, PII from the document could appear in logs.

**Risk:** PII leakage through log files.

**Recommendation:** Log only the exception type:
```python
except Exception:
    logger.error("Processing failed for attachment (type: %s)", type(e).__name__)
```

---

### [MEDIUM] Signal CLI Communication Over Unencrypted HTTP

**File:** `config.py:13`
**Issue:** The default `SIGNAL_CLI_URL` is `http://localhost:8080` (unencrypted HTTP). If signal-cli-rest-api is running on a different host, file attachments and messages transit in plaintext.

**Risk:** PII exposure during transit between the bot and signal-cli-rest-api.

**Recommendation:** If running on a separate host, HTTPS must be enforced. Add a startup validation:
```python
if not config.SIGNAL_CLI_URL.startswith("https://") and "localhost" not in config.SIGNAL_CLI_URL:
    logger.warning("SIGNAL_CLI_URL is not HTTPS and not localhost — PII may transit in plaintext")
```

---

### [MEDIUM] DOCX Cleaning Misses Embedded Content

**File:** `processor/docx_cleaner.py:42-76`
**Issue:** The DOCX cleaner processes paragraphs, tables, and headers/footers, but DOCX files can also contain PII in: comments, tracked changes (revisions), footnotes/endnotes, text boxes (drawing XML), embedded images with EXIF data, and custom XML parts.

**Risk:** PII survives in uncleaned document sections, defeating the purpose of the tool.

**Recommendation:**
1. Process footnotes and endnotes.
2. Strip comments and tracked changes by removing the relevant XML elements.
3. Strip any embedded image EXIF metadata.
4. Document known limitations clearly to users.

---

### [MEDIUM] PDF Cleaning Misses Annotations, Embedded Files, and Form Fields

**File:** `processor/pdf_cleaner.py:24-62`
**Issue:** The PDF cleaner only processes page text. PDFs can contain PII in: annotations/comments, form fields (AcroForm), embedded file attachments, XMP metadata (beyond the basic metadata dict), bookmarks/outlines, and JavaScript actions.

**Risk:** PII survives in uncleaned PDF elements.

**Recommendation:**
1. Remove all annotations: `page.delete_annot(annot)` for each annotation.
2. Remove form fields.
3. Remove embedded files.
4. Remove XMP metadata: `doc.del_xml_metadata()`.
5. Remove bookmarks/outlines.

---

### [LOW] No Sender Authentication / Allowlist

**File:** `signal_bot.py:60`
**Issue:** The bot processes messages from any Signal user who sends a message. There is no allowlist or authentication mechanism.

**Risk:** Unauthorized use of the service, resource abuse, potential for attack.

**Recommendation:** Add a configurable allowlist of authorized phone numbers:
```python
ALLOWED_SENDERS = set(os.getenv("ALLOWED_SENDERS", "").split(","))
```

---

### [LOW] Config Dataclass Default Evaluation at Import Time

**File:** `config.py:10-16`
**Issue:** The `@dataclass` default values call `os.getenv()` at class definition time (import time), not at instantiation time. If environment variables are set after the module is first imported, they will not be picked up. `int(os.getenv(...))` will raise `ValueError` at import time if the env var contains a non-integer.

**Risk:** Configuration errors cause hard-to-debug startup failures.

**Recommendation:** Use `__post_init__` or a factory method to load config with validation.

---

### [LOW] Potential XXE via lxml/python-docx on Untrusted DOCX

**File:** `processor/metadata.py:5`
**Issue:** `lxml` is used internally by python-docx for DOCX XML parsing. By default, lxml's `etree` parser is vulnerable to XXE (XML External Entity) attacks. A crafted DOCX with XXE payloads could potentially cause SSRF or file disclosure.

**Risk:** Low, since python-docx handles the parsing. Depends on python-docx's XXE configuration.

**Recommendation:** Verify that python-docx disables external entity resolution. Consider using `defusedxml` as a drop-in replacement.

---

### [LOW] No Integrity Check on Downloaded Attachments

**File:** `signal_bot.py:105-108`
**Issue:** Attachment content is taken directly from `attachment.get("data", b"")` with no checksum or integrity verification.

**Risk:** Low given localhost deployment, but defense-in-depth concern.

**Recommendation:** If signal-cli provides content hashes, verify them before processing.

---

### [INFO] Unpinned Dependency Versions

**File:** `requirements.txt`
**Issue:** All dependencies use minimum version constraints (`>=`) rather than pinned versions. Builds are not reproducible, and a compromised upstream release could be automatically pulled in.

**Recommendation:** Use pinned versions with a lockfile (`pip-tools`, `poetry.lock`).

---

### [INFO] Extra PyPI Index URL in requirements.txt

**File:** `requirements.txt:7`
**Issue:** `--extra-index-url https://download.pytorch.org/whl/cpu` adds a secondary package index. Extra index URLs can be a vector for dependency confusion attacks.

**Recommendation:** Use `--find-links` instead of `--extra-index-url` for PyTorch, or use a private index/mirror.

---

## Positives

- All NLP processing runs locally — no document data sent to external APIs
- Temp directory created with `mode=0o700` (restricted permissions)
- `finally` blocks ensure cleanup on processing errors
- Metadata stripping covers both PDF and DOCX properties
- Secure overwrite (random bytes + zeros) before file deletion
- Configuration loaded from environment variables, not hardcoded

---

## Deployment Blockers (must fix)

1. **Path traversal** (Finding #1) — trivially exploitable, leads to arbitrary file write
2. **Actual content size validation** (Finding #4) — metadata-based size check is bypassable
3. **Rate limiting and concurrency control** (Findings #5, #6) — service is trivially DoS-able and timeout mechanism is fragile

## Strong Recommendations (should fix)

4. **Mount TEMP_DIR on tmpfs** — mitigates findings #7, #9, and greatly reduces PII persistence risk
5. **File magic validation** (Finding #10) — reject files whose content does not match their claimed extension
6. **Sanitize all log output** (Findings #2, #3, #12) — no variable derived from document content should reach a logger
7. **Clean all document elements** (Findings #14, #15) — current cleaners miss several PII-carrying structures
8. **Add sender allowlist** (Finding #16) — unless intentionally a public service
