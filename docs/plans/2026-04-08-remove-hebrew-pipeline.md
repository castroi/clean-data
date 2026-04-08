# Remove Hebrew NLP pipeline from clean-data

**Goal:** Strip the `avichr/heBERT_NER` pipeline and its heavy dependencies (torch, transformers, langdetect) from clean-data, collapsing the dual-language PII detector to English-only spaCy while preserving the Israeli regex recognizers.

**Architecture:** `PIIDetector` loses its heBERT loading branch, `_detect_language` / `_get_hebrew_entities` methods, and the Hebrew code paths in `detect_entities` / `detect_and_remove`. The Israeli `Teudat Zehut` ID and 05X/+972 phone recognizers stay — they are pattern-based and language-agnostic. Public method signatures are unchanged, so `pipeline.py`, `pdf_cleaner.py`, and `docx_cleaner.py` need no edits. The dependency strip reclaims ~720 MB of installed packages and ~230 MB of runtime RAM, making clean-data feasible on resource-constrained hardware (a follow-up plan will add an optional RPi3 deployment overlay).

**Key decisions:**
- **Keep Israeli regex recognizers** — 9-digit IDs and 05X/+972 phone patterns work without any NLP model. Israeli PII coverage survives the heBERT removal for free.
- **Keep Hebrew `/replace` tests** — they exercise case-insensitive string matching, not NLP. They pass without transformers. Deleting them would shrink test coverage for no technical reason.
- **Delete `docs/cybersecurity.md`** — per user request. All references to it are scrubbed in the same commit so nothing links to a dead file.
- **Add a regression guard test** — asserts that importing `processor.pii_detector` does not pull in `transformers`, `torch`, or `langdetect`. Small test, catches future mistakes.
- **Single commit on a dedicated branch** — conventional commit type is `refactor` (removal of functionality, not a new feature). Branch: `refactor/remove-hebrew-pipeline`.
- **Build verification before commit** — the project must pass a fresh venv install, `pytest` on every test file, and a `docker build` before any git write operation.

---

## Dependency graph

```
Task 1 (pii_detector strip) ──┐
Task 2 (requirements.txt)  ───┤
Task 3 (Dockerfile)        ───┼──► Task 5 (tests) ──► Task 6 (docs) ──► Task 7 (build verify) ──► Task 8 (commit)
Task 4 (delete cyber docs) ───┘
```

**Parallelizable:** Tasks 1, 2, 3, 4
**Sequential:** Task 5 (after 1–4), Task 6 (after 5), Task 7 (after 6), Task 8 (after 7)

---

## Tasks

### Task 1: Strip Hebrew branches from `processor/pii_detector.py`

**Independent:** Yes
**Scope:** Small (1 file)

**Files:**
- Modify: `processor/pii_detector.py`

**Steps:**

1. Edit the `presidio_analyzer` import block: remove `RecognizerResult` (it was only used by `_get_hebrew_entities`). The block should end up as:
   ```python
   from presidio_analyzer import (
       AnalyzerEngine,
       PatternRecognizer,
       Pattern,
   )
   from presidio_analyzer.nlp_engine import NlpEngineProvider
   from presidio_anonymizer import AnonymizerEngine
   from presidio_anonymizer.entities import OperatorConfig
   ```

2. In `PIIDetector.__init__`, delete lines 63–77 (the entire `try: from transformers import pipeline ...` block, the `self._hebrew_available = False` / `self._hebrew_pipeline = None` initialization, and the logger calls).

3. Delete the `_detect_language` method in full (was lines 79–86).

4. Delete the `_get_hebrew_entities` method in full (was lines 88–117).

5. In `detect_entities`, delete the Hebrew branch (was lines 141–145):
   ```python
   # Add Hebrew NER results if text contains Hebrew
   lang = self._detect_language(text)
   if lang == "he":
       hebrew_entities = self._get_hebrew_entities(text)
       results.extend(hebrew_entities)
   ```

6. In `detect_and_remove`, delete the identical Hebrew branch (was lines 180–184).

7. Rewrite the class docstring (was lines 44–48) to:
   ```python
   """Detects and removes PII using Presidio with English NLP and Israeli regex recognizers.

   Uses spaCy en_core_web_sm for English NER. Custom pattern recognizers cover
   Israeli Teudat Zehut (9-digit IDs) and Israeli phone number formats (05X and
   +972). No NLP model is loaded for Hebrew — Hebrew text still passes through
   the English analyzer and the regex recognizers.
   """
   ```

8. Leave `_build_israeli_id_recognizer`, `_build_israeli_phone_recognizer`, the `AnalyzerEngine` setup, and both registration calls untouched.

**Verification:**
```bash
cd /home/user/clean-data && source .venv/bin/activate
python -c "from processor.pii_detector import PIIDetector; d = PIIDetector(); print(d.detect_entities('My name is John Smith and my ID is 123456789'))"
```
Expect: output contains an entry with `type: PERSON` for "John Smith" and an entry with `type: IL_ID_NUMBER` for "123456789". No `ImportError` about transformers.

```bash
grep -nE "hebrew|heBERT|langdetect|transformers|_detect_language|_get_hebrew_entities|_hebrew_" processor/pii_detector.py
```
Expect: zero matches.

**Acceptance criteria:**
- [ ] No references to transformers, langdetect, or heBERT remain in the file
- [ ] `PIIDetector()` instantiates without network access
- [ ] Israeli ID and phone patterns still fire on sample text
- [ ] English spaCy NER still detects PERSON entities

---

### Task 2: Strip Hebrew dependencies from `requirements.txt`

**Independent:** Yes
**Scope:** Small (1 file)

**Files:**
- Modify: `requirements.txt`

**Steps:**

1. Delete these four lines:
   ```
   transformers>=4.36.0
   --extra-index-url https://download.pytorch.org/whl/cpu
   torch>=2.1.0
   langdetect>=1.0.9
   ```

2. Final file should contain exactly these 8 lines:
   ```
   PyMuPDF>=1.23.0
   python-docx>=1.1.0
   presidio-analyzer>=2.2.0
   presidio-anonymizer>=2.2.0
   spacy>=3.7.0
   pysignalclirestapi>=0.3.20
   python-dotenv>=1.0.0
   pytest>=7.4.0
   ```

**Verification:**
```bash
grep -E "transformers|torch|langdetect|pytorch|--extra-index-url" requirements.txt
```
Expect: no matches.

```bash
wc -l requirements.txt
```
Expect: `8 requirements.txt`.

**Acceptance criteria:**
- [ ] File contains exactly 8 non-empty lines
- [ ] No torch/transformers/langdetect references

---

### Task 3: Strip heBERT download from `Dockerfile`

**Independent:** Yes
**Scope:** Small (1 file)

**Files:**
- Modify: `Dockerfile`

**Steps:**

1. Delete lines 19–23 (the heBERT download step):
   ```dockerfile
   # Pin model revision to a known-good commit for supply chain safety.
   # To update: visit https://huggingface.co/avichr/heBERT_NER/commits/main and pick the latest commit SHA.
   RUN python -c "from transformers import AutoTokenizer, AutoModelForTokenClassification; \
       AutoTokenizer.from_pretrained('avichr/heBERT_NER'); \
       AutoModelForTokenClassification.from_pretrained('avichr/heBERT_NER')"
   ```

2. Delete line 35 (the HuggingFace cache copy):
   ```dockerfile
   COPY --from=builder /root/.cache/huggingface /home/cleandata/.cache/huggingface
   ```

3. Delete lines 44–45 (the cache env vars):
   ```dockerfile
   ENV HF_HOME="/home/cleandata/.cache/huggingface"
   ENV TRANSFORMERS_CACHE="/home/cleandata/.cache/huggingface"
   ```

4. Leave everything else untouched. Specifically: the `gcc g++` apt install stays (spaCy wheel compilation may still need it on some arches), the spaCy download on line 18 stays, the non-root user setup stays, the `COPY` of application source stays.

**Verification:**
```bash
grep -nE "hebrew|heBERT|huggingface|transformers|HF_HOME|TRANSFORMERS_CACHE" Dockerfile
```
Expect: zero matches.

**Acceptance criteria:**
- [ ] Dockerfile has no Hebrew / HuggingFace / transformers references
- [ ] Multi-stage build structure (builder + runtime) is intact
- [ ] spaCy download step remains

---

### Task 4: Delete `docs/cybersecurity.md` and scrub all references

**Independent:** Yes (runs in parallel with Tasks 1–3)
**Scope:** Small (3 files: 1 deleted + 2 modified)

**Files:**
- Delete: `docs/cybersecurity.md`
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Steps:**

1. Delete the file:
   ```bash
   rm docs/cybersecurity.md
   ```

2. In `README.md`, remove any sentence or paragraph that links to `docs/cybersecurity.md`. Known locations (confirm with grep):
   - Around line 229: sentence ending *"...from the [security audit](docs/cybersecurity.md)."*
   - Around line 270: *"A full security audit is available at [`docs/cybersecurity.md`](docs/cybersecurity.md)."*

   Use `grep -n "cybersecurity.md" README.md` first to get exact line numbers, then edit each match.

3. In `CLAUDE.md`, remove the attribution line (around line 96): *"These were established by a security audit (see `docs/cybersecurity.md`):"*. Replace with: *"Security rules enforced across the codebase:"* so the rule bullets below still have a heading sentence. The bullets themselves STAY — they are the active rules, not documentation of the removed file.

**Verification:**
```bash
grep -rn "cybersecurity.md\|security audit" /home/user/clean-data --include="*.md" --include="*.py" --include="*.yml"
```
Expect: zero matches.

```bash
test ! -e docs/cybersecurity.md && echo "deleted"
```
Expect: `deleted`.

**Acceptance criteria:**
- [ ] `docs/cybersecurity.md` no longer exists
- [ ] No file references the deleted doc
- [ ] CLAUDE.md security rules section still present with an intro sentence
- [ ] README.md reads cleanly with the references removed (no orphaned punctuation)

---

### Task 5: Add transformers-import guard test and run the full test suite

**Independent:** No — depends on Tasks 1, 2, 3, 4
**Scope:** Small (1 test file modified, all tests run)

**Files:**
- Modify: `tests/test_pii_detector.py`

**Steps:**

1. Read `tests/test_pii_detector.py` in full before editing. Grep it for `hebrew`, `heBERT`, `_detect_language`, `_hebrew_`, `_get_hebrew_entities`:
   ```bash
   grep -niE "hebrew|heBERT|_detect_language|_hebrew_|_get_hebrew_entities" tests/test_pii_detector.py
   ```
   From discovery-phase exploration, this file only contains Israeli ID/phone tests (pattern-based, stay). If the grep finds any test that asserts NLP-based Hebrew name detection, delete or update it — note the change in the commit message.

2. Append a new guard test at the end of the file:
   ```python
   def test_no_transformers_import_at_module_load():
       """Regression guard: importing pii_detector must not pull in transformers/torch.

       These libraries were removed to strip the Hebrew NLP pipeline. If a future
       change reintroduces them as a top-level or constructor-time import, this
       test will fail and force an explicit decision.
       """
       import sys

       for mod in list(sys.modules):
           if mod.startswith(("transformers", "torch", "langdetect")):
               del sys.modules[mod]
       if "processor.pii_detector" in sys.modules:
           del sys.modules["processor.pii_detector"]

       import processor.pii_detector  # noqa: F401
       from processor.pii_detector import PIIDetector

       PIIDetector()  # Constructor must also not import transformers/torch

       assert "transformers" not in sys.modules, \
           "transformers must not be imported by pii_detector"
       assert "torch" not in sys.modules, \
           "torch must not be imported by pii_detector"
       assert "langdetect" not in sys.modules, \
           "langdetect must not be imported by pii_detector"
   ```

3. Run each test file individually (the project README warns against running the whole suite due to OOM risk with multiple PIIDetector instances).

**Verification:**
```bash
cd /home/user/clean-data && source .venv/bin/activate
pytest tests/test_pii_detector.py -v
pytest tests/test_pdf_cleaner.py -v
pytest tests/test_docx_cleaner.py -v
pytest tests/test_metadata.py -v
pytest tests/test_secure_delete.py -v
pytest tests/test_pipeline.py -v
pytest tests/test_signal_bot.py -v
pytest tests/test_config.py -v
pytest tests/test_custom_word_detector.py -v
pytest tests/test_replace_command.py -v
pytest tests/test_word_session_store.py -v
```
Expect: every file green. In particular:
- `test_removes_israeli_id` and `test_removes_israeli_phone` in `test_pii_detector.py` still pass (regex-based, unaffected).
- Hebrew `/replace` tests in `test_custom_word_detector.py` and `test_replace_command.py` still pass (string matching, unaffected).
- New `test_no_transformers_import_at_module_load` passes.

**Acceptance criteria:**
- [ ] Every test file passes individually when run with `pytest -v`
- [ ] New guard test catches accidental transformers/torch/langdetect import
- [ ] Hebrew string-matching tests in `/replace` test files still pass
- [ ] No test was skipped or marked xfail to paper over a failure

---

### Task 6: Update `README.md` and `CLAUDE.md` for Hebrew removal

**Independent:** No — depends on Task 5 (waits for green tests before touching docs)
**Scope:** Small (2 files)

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Steps for `README.md`:**

1. In the intro paragraph (around line 3), change:
   > "...using Microsoft Presidio and a local Hebrew NER model, then returns the cleaned document."
   to:
   > "...using Microsoft Presidio with English NLP and Israeli PII pattern recognizers, then returns the cleaned document."

2. In the Features list, delete the bullet:
   > "Supports English and Hebrew (RTL) documents, including bilingual content"

   Replace with:
   > "English-language document support with Israeli PII pattern detection (Teudat Zehut IDs, 05X/+972 phone numbers)"

3. In the Stack table, delete the row:
   > `| HuggingFace heBERT-NER | Hebrew named entity recognition (runs locally) |`

4. Delete the entire "5. Download the Hebrew NER model (optional)" subsection under Setup (includes the `python -c "from transformers import pipeline..."` command and the "500MB of disk space" note).

5. Renumber the subsequent setup steps: 6 → 5, 7 → 6, 8 → 7.

**Steps for `CLAUDE.md`:**

1. In the Stack section, delete the bullet:
   > "**Hebrew NER:** HuggingFace `avichr/heBERT-NER` (runs locally, no API calls)"

2. In the Architecture section, rewrite the `processor/pii_detector.py` bullet (around line 80) to:
   > "**`processor/pii_detector.py`** — Wraps Presidio with English NLP (spaCy `en_core_web_sm`). Includes custom recognizers for Israeli PII (Teudat Zehut, 05X phones, +972 format)."

3. In the Key Design Constraints section, delete the bullet:
   > "**Hebrew RTL support is required.** Both PDF and DOCX handling must preserve RTL text direction. PII detection must work for Hebrew names, Israeli IDs, and Israeli phone formats."

   Leave the other constraints (local processing, full deletion, zero data retention, error-path cleanup) untouched.

**Verification:**
```bash
grep -niE "hebrew|heBERT|avichr|RTL" README.md CLAUDE.md
```
Expect: zero matches.

```bash
grep -n "Israeli\|Teudat" README.md CLAUDE.md
```
Expect: at least one match in each file (the replacement content).

**Acceptance criteria:**
- [ ] No Hebrew/heBERT/RTL references remain in README.md or CLAUDE.md
- [ ] Israeli PII coverage is still documented as a supported feature
- [ ] Setup steps in README.md are correctly renumbered
- [ ] No broken markdown (orphaned table rows, empty bullets, dangling punctuation)

---

### Task 7: Build the project end-to-end before committing

**Independent:** No — depends on Task 6
**Scope:** Medium (no file changes, multiple build steps)

**Purpose:** Prove the changes actually work — fresh venv install, pytest green, and Docker image builds — BEFORE any git write operation. If any step fails, stop and fix before moving to Task 8.

**Steps:**

1. **Fresh venv install from the new `requirements.txt`** (catches any hidden transitive dependency on transformers/torch):
   ```bash
   cd /home/user/clean-data
   rm -rf .venv-verify
   python3 -m venv .venv-verify
   .venv-verify/bin/pip install --no-cache-dir --upgrade pip
   .venv-verify/bin/pip install --no-cache-dir -r requirements.txt
   .venv-verify/bin/python -m spacy download en_core_web_sm
   ```
   Expect: exit 0, no torch or transformers downloaded. Watch the pip output — if you see `torch` or `transformers` being fetched, a transitive dep pulled them in and Task 2 needs revisiting.

2. **Import check from the fresh venv:**
   ```bash
   .venv-verify/bin/python -c "
   import sys
   from processor.pii_detector import PIIDetector
   d = PIIDetector()
   print('entities:', d.detect_entities('John Smith, ID 123456789, phone 052-1234567'))
   assert 'transformers' not in sys.modules
   assert 'torch' not in sys.modules
   print('OK')
   "
   ```
   Expect: output ends with `OK`, entities list contains PERSON, IL_ID_NUMBER, and PHONE_NUMBER hits.

3. **Full per-file pytest run from the existing dev venv** (same commands as Task 5, re-run to catch any regressions introduced during Task 6 doc edits — unlikely but cheap):
   ```bash
   source .venv/bin/activate
   for f in tests/test_*.py; do
     pytest "$f" -v || { echo "FAILED: $f"; exit 1; }
   done
   ```
   Expect: every file green.

4. **Docker image build:**
   ```bash
   docker build -t clean-data-verify .
   ```
   Expect: build succeeds. Note the final image size — should be roughly 1/3 of the previous build (no torch means ~800 MB–1 GB instead of ~2.2 GB).

5. **Docker smoke test:**
   ```bash
   docker run --rm clean-data-verify python -c "
   from processor.pii_detector import PIIDetector
   d = PIIDetector()
   print(d.detect_entities('John Smith 123456789'))
   print('image OK')
   "
   ```
   Expect: output ends with `image OK`, entities list present.

6. **Clean up verification artifacts:**
   ```bash
   rm -rf .venv-verify
   docker rmi clean-data-verify
   ```

**Verification (sanity summary):**
```bash
# Must all pass before Task 8
source .venv/bin/activate && \
  python -c "from processor.pii_detector import PIIDetector; PIIDetector()" && \
  pytest tests/test_pii_detector.py -v && \
  echo "BUILD VERIFIED"
```

**Acceptance criteria:**
- [ ] Fresh venv install from `requirements.txt` does NOT fetch torch, transformers, or langdetect
- [ ] `python -m spacy download en_core_web_sm` succeeds from the fresh venv
- [ ] Every test file passes individually
- [ ] `docker build .` succeeds without errors
- [ ] Docker image smoke test runs `PIIDetector()` successfully
- [ ] Verification venv and test image are removed after passing (no lingering artifacts)

**If any step fails:** stop, diagnose, fix the underlying task (1–6), re-run Task 7 from the top. Do NOT proceed to Task 8 on a partial pass.

---

### Task 8: Create branch and commit the change (with user approval)

**Independent:** No — depends on Task 7 passing cleanly
**Scope:** Small (git only)

**IMPORTANT:** The global CLAUDE.md rule is *"NEVER commit, push, merge, or perform any git write operation without explicit user approval."* This task MUST stop and ask before running `git commit`.

**Steps:**

1. Show the user the change summary before any git operation:
   ```bash
   git status
   git diff --stat
   ```

2. If the user confirms the diff looks right, create the branch:
   ```bash
   git checkout -b refactor/remove-hebrew-pipeline
   ```

3. Stage exactly the expected files (avoid `git add -A` — don't want stray untracked files sneaking in):
   ```bash
   git add processor/pii_detector.py
   git add requirements.txt
   git add Dockerfile
   git add tests/test_pii_detector.py
   git add README.md
   git add CLAUDE.md
   git rm docs/cybersecurity.md
   git status  # show the staged set to the user one more time
   ```

4. **ASK USER EXPLICITLY: "Ready to commit these staged changes?"** Wait for "yes" before proceeding.

5. On confirmation, commit:
   ```bash
   git commit -m "$(cat <<'EOF'
   refactor: remove Hebrew NER pipeline and heavy ML deps

   Strips the avichr/heBERT_NER pipeline, transformers, torch, and
   langdetect from the project. The dual-language PII detector collapses
   to English-only spaCy while preserving the Israeli regex recognizers
   for Teudat Zehut IDs and 05X/+972 phone patterns — Israeli PII
   coverage is unchanged.

   Removes ~720 MB of installed dependencies and ~230 MB of runtime RAM,
   making the project feasible on resource-constrained hardware. A
   separate plan will add an opt-in Raspberry Pi 3 deployment overlay.

   Also deletes docs/cybersecurity.md per user request and scrubs
   references from README.md and CLAUDE.md. Adds a regression guard
   test that fails if transformers, torch, or langdetect are ever
   re-imported at pii_detector load time.

   Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
   EOF
   )"
   ```

6. Verify the commit landed:
   ```bash
   git log --oneline -1
   git show --stat HEAD
   ```

**Verification:**
```bash
git log --oneline -1
```
Expect: one line matching `<hash> refactor: remove Hebrew NER pipeline and heavy ML deps`.

```bash
git show --stat HEAD
```
Expect: 6 files modified + 1 file deleted (`docs/cybersecurity.md`), roughly −150 / +40 lines.

**Acceptance criteria:**
- [ ] Branch `refactor/remove-hebrew-pipeline` exists
- [ ] Exactly one commit on the branch diverging from main
- [ ] Commit contains only the 7 expected files (6 modified, 1 deleted)
- [ ] User explicitly approved the commit before it was created
- [ ] No push to remote performed (push is a separate explicit request)

---

## Verification summary

| Task | Verification command | Expected |
|---|---|---|
| 1 | `python -c "from processor.pii_detector import PIIDetector; PIIDetector().detect_entities('John Smith 123456789')"` | PERSON + IL_ID_NUMBER detected, no ImportError |
| 2 | `grep -E "transformers\|torch\|langdetect" requirements.txt && wc -l requirements.txt` | Zero grep matches, 8 lines |
| 3 | `grep -nE "hebrew\|heBERT\|huggingface\|transformers" Dockerfile` | Zero matches |
| 4 | `grep -rn "cybersecurity.md" . --include="*.md"` | Zero matches |
| 5 | `pytest tests/test_pii_detector.py -v` (+ all other test files) | All green incl. guard test |
| 6 | `grep -niE "hebrew\|heBERT\|avichr\|RTL" README.md CLAUDE.md` | Zero matches |
| 7 | Fresh venv install + pytest + `docker build .` + smoke test | All steps pass, no transformers/torch fetched |
| 8 | `git log --oneline -1` | Single refactor commit on branch |

---

## Out of scope

Deliberately NOT changing in this plan:

- **Raspberry Pi 3 deployment overlay** — covered in a separate plan (`docs/plans/2026-04-08-rpi3-deployment.md`, to be written after this plan is executed).
- **`signal_bot.py`, `processor/pipeline.py`, `processor/pdf_cleaner.py`, `processor/docx_cleaner.py`, `processor/metadata.py`, `processor/custom_word_detector.py`, `utils/secure_delete.py`, `word_session_store.py`, `config.py`, `main.py`** — none reference Hebrew, transformers, or langdetect.
- **Hebrew `/replace` tests** in `tests/test_custom_word_detector.py` and `tests/test_replace_command.py` — they exercise case-insensitive string matching, not NLP. They stay and must continue to pass.
- **`.env.example`** — no Hebrew content.
- **Defaults in `config.py`** — `MAX_FILE_SIZE_MB` and `PROCESSING_TIMEOUT` stay at their current values (25 MB / 300 s). Tuning for constrained hardware belongs in the RPi3 plan.
- **`docker-compose.yml` memory limits** — stay at `mem_limit: 2g`. Tuning belongs in the RPi3 plan.
- **Git push, PR creation, remote operations** — this plan only creates a local commit. Pushing is a separate explicit user request.
