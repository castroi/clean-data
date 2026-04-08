# Add optional Raspberry Pi 3 deployment overlay

**Goal:** Add an opt-in deployment path for running clean-data on a 1 GB Raspberry Pi 3B/3B+ without altering the default Docker deployment used by desktop/VPS users.

**Architecture:** A new `docker-compose.rpi3.yml` override file layers on top of the base `docker-compose.yml`, tightening memory limits, shrinking tmpfs, capping the JVM heap on signal-cli, and lowering `MAX_FILE_SIZE_MB` and `PROCESSING_TIMEOUT` for the clean-data container. The base compose file is unchanged — desktop and VPS users see no behavioral difference. RPi3 users explicitly opt in with `docker compose -f docker-compose.yml -f docker-compose.rpi3.yml up -d`. A new `docs/rpi3-deployment.md` guide documents host prep (64-bit Raspberry Pi OS Lite, zram-tools, GPU memory split, disabled services), deploy commands, verification, and troubleshooting. The README grows a new three-tier Deployment section (default Docker / bare-metal / RPi3).

**Key decisions:**
- **Opt-in, not default.** `docker-compose.yml` stays as-is. RPi3 is a second path for constrained hardware, not the new default. Users explicitly pass `-f docker-compose.rpi3.yml`.
- **zram-tools, not a SD swap file.** Compression gives ~2.5–3× effective headroom with zero SD write wear. Installed via `sudo apt install zram-tools` — 2-line config in `/etc/default/zramswap`. Avoids SD card death from swap thrashing.
- **JVM `-Xmx256m` cap on signal-cli.** Without this, the JVM can grow past 500 MB on its own and OOM the Pi. 256 MB heap + metaspace/native overhead fits inside the 320 MB container limit.
- **`MAX_FILE_SIZE_MB=10` on RPi3.** The relationship between input file size and peak RAM is 5–8× for DOCX (python-docx holds the full XML DOM + spaCy Doc objects). 25 MB input → ~150 MB peak allocation, too risky on a 1 GB board. 10 MB → ~60 MB peak, comfortable.
- **`PROCESSING_TIMEOUT=180`** (down from 300). Slower CPU means longer clean times, but a shorter ceiling prevents runaway thrashing.
- **First build on the Pi**, not cross-built. ~5 min once; subsequent builds use layer cache. Keeps the plan simple. A buildx cross-build recipe can be added later if build time becomes painful.
- **Branch: `feat/rpi3-deployment`**, single commit, conventional type `feat` — a new capability (new deployment target).
- **Depends on the Hebrew removal plan** (`docs/plans/2026-04-08-remove-hebrew-pipeline.md`) having been executed first. Without the Hebrew strip, the Docker image is ~2.2 GB and torch alone consumes ~150 MB RAM — the 600 MB container limit in this plan would OOM immediately.

---

## Dependency graph

```
[prerequisite: Hebrew removal plan executed and merged]
          │
          ▼
Task 1 (compose overlay) ──┐
Task 2 (rpi3 guide)        ├──► Task 4 (build verify) ──► Task 5 (commit)
Task 3 (README tiers)    ──┘
```

**Parallelizable:** Tasks 1, 2, 3
**Sequential:** Task 4 (after 1–3), Task 5 (after 4)

---

## Prerequisites

Before executing any task in this plan:

1. The plan `docs/plans/2026-04-08-remove-hebrew-pipeline.md` has been executed end-to-end and the `refactor: remove Hebrew NER pipeline and heavy ML deps` commit exists in `main` (or the base branch this work will branch from).
2. Verify with:
   ```bash
   grep -E "transformers|torch|langdetect" requirements.txt
   ```
   Expect: zero matches. If there are matches, stop and execute the Hebrew removal plan first.
3. Verify the Docker image still builds:
   ```bash
   docker build -t clean-data-verify . && docker rmi clean-data-verify
   ```
   Expect: clean build. If it fails, fix the Hebrew removal before touching this plan.

---

## Tasks

### Task 1: Create `docker-compose.rpi3.yml` override

**Independent:** Yes
**Scope:** Small (1 new file)

**Files:**
- Create: `docker-compose.rpi3.yml`

**Steps:**

Write the file with exactly this content:

```yaml
# Raspberry Pi 3 deployment overlay
#
# Usage:
#   docker compose -f docker-compose.yml -f docker-compose.rpi3.yml up -d
#
# This overlay tightens memory, CPU, and tmpfs limits to fit a 1 GB RAM
# Raspberry Pi 3B/3B+. Requires zram-tools active on the host — see
# docs/rpi3-deployment.md for host prep (zram, GPU memory, disabled services).
#
# Do not use this overlay on desktop or VPS deployments — the limits are
# intentionally tight and will cause OOM kills on larger documents. The
# default docker-compose.yml is the right path for any machine with 2 GB+
# of RAM.

services:
  signal-cli:
    environment:
      - MODE=normal
      - JAVA_OPTS=-Xmx256m -Xms64m
    mem_limit: 320m
    mem_reservation: 200m
    healthcheck:
      # Java startup on RPi3 is slow; raise the grace period
      start_period: 120s

  clean-data:
    mem_limit: 600m
    mem_reservation: 400m
    cpus: "1.5"
    tmpfs:
      - /tmp/clean-data:size=128M,mode=0700,uid=1000,gid=1000
      - /tmp:size=64M
    environment:
      - SIGNAL_CLI_URL=http://signal-cli:8080
      - TEMP_DIR=/tmp/clean-data
      - MAX_FILE_SIZE_MB=10
      - PROCESSING_TIMEOUT=180
```

**Verification:**
```bash
cd /home/user/clean-data
docker compose -f docker-compose.yml -f docker-compose.rpi3.yml config > /tmp/merged-compose.yml
grep -E "mem_limit: 600m|JAVA_OPTS.*Xmx256m|MAX_FILE_SIZE_MB=10" /tmp/merged-compose.yml
rm /tmp/merged-compose.yml
```
Expect: `docker compose config` exits 0 (validates YAML and merge semantics without starting anything). The grep finds all three overrides in the merged output.

```bash
# Sanity check: base compose is still a valid standalone
docker compose -f docker-compose.yml config > /dev/null
```
Expect: exit 0. Base file is untouched.

**Acceptance criteria:**
- [ ] File `docker-compose.rpi3.yml` exists at project root
- [ ] `docker compose config` with both files merges cleanly
- [ ] Merged config shows `mem_limit: 600m` on clean-data, `320m` on signal-cli
- [ ] Merged config shows `JAVA_OPTS=-Xmx256m -Xms64m` on signal-cli
- [ ] Merged config shows `MAX_FILE_SIZE_MB=10` and `PROCESSING_TIMEOUT=180` on clean-data
- [ ] Base `docker-compose.yml` still validates standalone

---

### Task 2: Create `docs/rpi3-deployment.md` guide

**Independent:** Yes
**Scope:** Small (1 new file)

**Files:**
- Create: `docs/rpi3-deployment.md`

**Steps:**

Write a runbook with these sections in order. The guide is read and executed by a user on an actual Pi, so every command must be correct — no placeholders.

**Section 1: Hardware prerequisites**
- Raspberry Pi 3B or 3B+ (1 GB RAM). **Not** 3A+ (only 512 MB, will not fit).
- 16 GB+ SD card (Class 10 / A1 or better). 8 GB is too small once the Docker base image is pulled.
- Power supply: 2.5 A minimum. Undervoltage causes random container crashes.
- Network access for initial setup and `git clone`.

**Section 2: OS install**
- Flash Raspberry Pi OS Lite **64-bit** (Bookworm or later). 32-bit breaks some Python wheels.
- Use Raspberry Pi Imager; in the settings dialog set hostname, enable SSH, set a user, set locale. Makes headless setup much easier.

**Section 3: Host prep commands**
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose-plugin zram-tools git
sudo usermod -aG docker $USER
# Log out and back in for the group change to take effect
```

**Section 4: Configure zram swap**
Edit `/etc/default/zramswap` so it contains:
```
PERCENT=50
ALGO=zstd
```
Then:
```bash
sudo systemctl enable --now zramswap
cat /proc/swaps
```
Expect `cat /proc/swaps` to show `/dev/zram0`. If it doesn't, run `sudo systemctl status zramswap` to diagnose.

**Section 5: Reclaim memory from GPU and unused services**
Edit `/boot/firmware/config.txt`, add this line anywhere:
```
gpu_mem=16
```
Disable services not needed for a headless bot:
```bash
sudo systemctl disable --now bluetooth hciuart triggerhappy
sudo reboot
```
After reboot, `free -h` should show ~960 MB total (vs ~920 MB before the gpu_mem change).

**Section 6: Deploy clean-data**
```bash
git clone <repo-url>
cd clean-data
cp .env.example .env
# Edit .env — set SIGNAL_PHONE_NUMBER (the Signal number this bot will use)
docker compose -f docker-compose.yml -f docker-compose.rpi3.yml build
# First build takes ~5 min on RPi3. Subsequent builds use layer cache and are faster.
docker compose -f docker-compose.yml -f docker-compose.rpi3.yml up -d
```

**Section 7: Register the Signal number**
The bot's Signal number must be registered before it can receive messages. See the **"Set up a phone number"** section in the main `README.md` for the registration + verification curl commands (including the CAPTCHA workaround).

**Section 8: Verification**
```bash
# Confirm zram swap is active and being used
free -h
zramctl

# Confirm containers are running under their mem_limits
docker stats --no-stream

# Confirm clean-data started cleanly
docker compose -f docker-compose.yml -f docker-compose.rpi3.yml logs clean-data | tail -50

# Confirm signal-cli is healthy
docker compose -f docker-compose.yml -f docker-compose.rpi3.yml ps
```
Expect: both containers `running (healthy)`. `docker stats` shows clean-data under ~500 MB at idle and signal-cli under ~280 MB.

**Section 9: Troubleshooting**

| Symptom | Likely cause | Fix |
|---|---|---|
| `docker logs clean-data` ends with `Killed` | OOM on large document | Lower `MAX_FILE_SIZE_MB` further (try 5), or verify zram is active with `zramctl` |
| `docker compose build` fails with "no space left on device" | SD card too small (8 GB) | Use 16 GB+ SD card |
| signal-cli healthcheck keeps failing | Java startup >120 s | Raise `start_period` in the overlay file to `180s` |
| Random container crashes under load | Power undervoltage | Check `dmesg \| grep -i voltage`. Use a 2.5 A+ supply |
| Build hangs or OOMs during `pip install spacy` | No swap active during build | Ensure `zramctl` shows zram0 before `docker compose build` |
| `docker stats` shows clean-data near 600 MB at idle | Transformers/torch leaked back in somehow | Verify `grep -E "transformers\|torch" requirements.txt` returns zero — re-run Hebrew removal plan if not |

**Section 10: Expected performance**
- Small PDF (<1 MB): ~5–10 s end-to-end
- 5 MB DOCX: ~30–60 s
- 10 MB (the RPi3 ceiling): ~90–120 s
- First cold-start after `docker compose up`: ~45–60 s (Java + spaCy model load)

**Verification:**
```bash
test -f docs/rpi3-deployment.md
wc -l docs/rpi3-deployment.md
grep -c "docker compose -f docker-compose.yml -f docker-compose.rpi3.yml" docs/rpi3-deployment.md
```
Expect: file exists, roughly 80–150 lines, at least 3 references to the overlay command (build, up, logs).

**Acceptance criteria:**
- [ ] File exists at `docs/rpi3-deployment.md`
- [ ] All 10 sections present and in order
- [ ] Every command is complete (no `<placeholder>` except `<repo-url>` in the git clone step)
- [ ] File references `docker-compose.rpi3.yml` (matches the actual filename from Task 1)
- [ ] Troubleshooting table has at least 6 rows

---

### Task 3: Add three-tier deployment section to `README.md`

**Independent:** Yes
**Scope:** Small (1 file)

**Files:**
- Modify: `README.md`

**Steps:**

1. Read the current `README.md` to find the "Docker Deployment" and "Setup" sections.

2. Restructure into a single **"Deployment"** top-level section with three subsections:

   **a. `### Default: Docker (desktop or VPS)`** — the existing Docker Deployment content (docker-compose instructions, env config, register Signal number, view logs, stop). No content changes, just a subsection heading.

   **b. `### Bare-metal Python`** — move the existing "Setup" section (venv creation, pip install, spaCy download, signal-cli install, run `python3 main.py`) under this subsection. Add a one-sentence intro: *"Use this path if you don't want Docker, or if you need to debug the Python process directly."*

   **c. `### Raspberry Pi 3 (optional, advanced)`** — new content:
   ```markdown
   clean-data can run on a Raspberry Pi 3B/3B+ using an opt-in deployment overlay
   that tightens memory limits and requires zram swap on the host. This path is
   **advanced** — it requires Raspberry Pi OS 64-bit, zram-tools configured, GPU
   memory reclaimed, and a 16 GB+ SD card.

   **Minimum requirements:** 1 GB RAM (RPi3B or 3B+, not 3A+), 16 GB SD card,
   Raspberry Pi OS Lite 64-bit, network for initial setup.

   See [`docs/rpi3-deployment.md`](docs/rpi3-deployment.md) for full host prep
   and deploy instructions.

   Quick deploy (after host prep):
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.rpi3.yml up -d
   ```
   ```

3. Leave all other README sections untouched (Features, Stack, Architecture, Configuration, Usage, Docker Architecture, Running Tests, Security, Privacy Principles, Known Limitations, Set up a phone number, License).

**Verification:**
```bash
grep -n "^## Deployment\|^### Default: Docker\|^### Bare-metal Python\|^### Raspberry Pi 3" README.md
```
Expect: 4 matches in order (one section heading + three subsection headings).

```bash
grep -n "docker-compose.rpi3.yml\|rpi3-deployment.md" README.md
```
Expect: at least 2 matches (the command and the doc link).

**Acceptance criteria:**
- [ ] README has a single `## Deployment` top-level section
- [ ] Three subsections exist in order: Default Docker, Bare-metal Python, Raspberry Pi 3
- [ ] RPi3 subsection clearly labeled "optional, advanced"
- [ ] Link to `docs/rpi3-deployment.md` resolves (file exists from Task 2)
- [ ] Old "Docker Deployment" and "Setup" top-level sections no longer exist (their content moved into subsections)

---

### Task 4: Build and validate the overlay before committing

**Independent:** No — depends on Tasks 1, 2, 3
**Scope:** Medium (no file changes, multiple validation steps)

**Purpose:** Prove the overlay actually works on an x86 host (where the build can be tested quickly) BEFORE any git write. We cannot test on the actual Pi in this plan, but we can validate the compose YAML, the merged config, and the image build.

**Steps:**

1. **Validate base compose still standalone:**
   ```bash
   cd /home/user/clean-data
   docker compose -f docker-compose.yml config > /dev/null
   ```
   Expect: exit 0. If this fails, the base file was accidentally touched — stop and fix.

2. **Validate overlay YAML merge:**
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.rpi3.yml config > /tmp/merged-compose.yml
   ```
   Expect: exit 0, produces a valid merged YAML document.

3. **Inspect merged values:**
   ```bash
   grep -E "mem_limit|JAVA_OPTS|MAX_FILE_SIZE_MB|PROCESSING_TIMEOUT|cpus" /tmp/merged-compose.yml
   ```
   Expect: `mem_limit: "600m"` on clean-data, `mem_limit: "320m"` on signal-cli, `JAVA_OPTS=-Xmx256m -Xms64m`, `MAX_FILE_SIZE_MB=10`, `PROCESSING_TIMEOUT=180`, `cpus: "1.5"`.

4. **Build the image (same Dockerfile for x86 and arm64 — this only validates the Dockerfile still builds cleanly after the Hebrew removal):**
   ```bash
   docker build -t clean-data-verify .
   ```
   Expect: build succeeds.

5. **Smoke test the built image:**
   ```bash
   docker run --rm clean-data-verify python -c "
   from processor.pii_detector import PIIDetector
   d = PIIDetector()
   print(d.detect_entities('John Smith 123456789'))
   print('OK')
   "
   ```
   Expect: output ends with `OK`, entities list present.

6. **Cleanup:**
   ```bash
   docker rmi clean-data-verify
   rm /tmp/merged-compose.yml
   ```

7. **Verify no behavioral regression on the base path:**
   ```bash
   source .venv/bin/activate
   pytest tests/test_pii_detector.py -v
   ```
   Expect: all green. The overlay file should NOT affect tests — this is a sanity check that Task 3's README edits didn't break anything by accident.

**What is NOT verified in this task (and must be verified manually on an actual Pi before shipping):**
- Actual RAM footprint on ARM (estimates are based on x86 behavior; ARM can differ)
- zram config taking effect
- signal-cli Java startup time under the 120s healthcheck grace period
- End-to-end document processing on real hardware

Document these as known gaps in the commit message so the user knows what's still untested.

**Verification summary:**
```bash
docker compose -f docker-compose.yml -f docker-compose.rpi3.yml config > /dev/null && \
docker compose -f docker-compose.yml config > /dev/null && \
echo "OVERLAY VALIDATED"
```

**Acceptance criteria:**
- [ ] Base `docker-compose.yml` validates standalone
- [ ] `docker-compose.yml` + `docker-compose.rpi3.yml` merged config validates
- [ ] Merged config shows all expected override values
- [ ] `docker build .` succeeds on x86 (Dockerfile integrity)
- [ ] Image smoke test runs `PIIDetector()` successfully
- [ ] `pytest tests/test_pii_detector.py` still green
- [ ] Known gaps documented for manual Pi testing

**If any step fails:** stop, fix the underlying task, re-run Task 4 from the top. Do NOT proceed to Task 5 on a partial pass.

---

### Task 5: Create branch and commit the change (with user approval)

**Independent:** No — depends on Task 4 passing cleanly
**Scope:** Small (git only)

**IMPORTANT:** Global CLAUDE.md rule — *"NEVER commit, push, merge, or perform any git write operation without explicit user approval."* This task MUST stop and ask before running `git commit`.

**Steps:**

1. Show the user the change summary:
   ```bash
   git status
   git diff --stat
   ```

2. If the user confirms the diff looks right, create the branch **from the commit produced by the Hebrew removal plan** (not from an older base):
   ```bash
   git checkout -b feat/rpi3-deployment
   ```

3. Stage exactly the expected files:
   ```bash
   git add docker-compose.rpi3.yml
   git add docs/rpi3-deployment.md
   git add README.md
   git status  # show the staged set to the user one more time
   ```

4. **ASK USER EXPLICITLY: "Ready to commit these staged changes?"** Wait for "yes" before proceeding.

5. On confirmation, commit:
   ```bash
   git commit -m "$(cat <<'EOF'
   feat: add optional Raspberry Pi 3 deployment overlay

   Adds docker-compose.rpi3.yml as an opt-in override for deploying
   clean-data on a 1 GB Raspberry Pi 3B/3B+. The default Docker deployment
   is unchanged — desktop and VPS users see no behavioral difference.

   The RPi3 overlay tightens mem_limits (clean-data 600M, signal-cli 320M),
   caps JVM heap (-Xmx256m -Xms64m), shrinks tmpfs to 128M, lowers
   MAX_FILE_SIZE_MB to 10, PROCESSING_TIMEOUT to 180s, and raises the
   signal-cli healthcheck grace period to 120s to accommodate slow ARM
   JVM startup. Requires zram-tools on the host for effective swap without
   SD card wear — see docs/rpi3-deployment.md.

   Adds docs/rpi3-deployment.md with host prep (64-bit OS Lite, zram
   config, gpu_mem=16, disabled services), deploy commands, verification,
   and a troubleshooting table. README gains a three-tier Deployment
   section (default Docker / bare-metal / RPi3).

   Prerequisite plan: docs/plans/2026-04-08-remove-hebrew-pipeline.md
   must be executed first. Without the Hebrew strip, the Docker image is
   ~2.2 GB and torch alone consumes ~150 MB RAM — the overlay's 600 MB
   container limit would OOM immediately.

   Known gaps (not tested in CI, needs manual Pi validation before
   shipping): actual ARM RAM footprint, zram config, signal-cli Java
   startup time, end-to-end document processing on real hardware.

   Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
   EOF
   )"
   ```

6. Verify the commit landed:
   ```bash
   git log --oneline -2
   git show --stat HEAD
   ```

**Verification:**
```bash
git log --oneline -2
```
Expect: two commits in order — top is `feat: add optional Raspberry Pi 3 deployment overlay`, below is `refactor: remove Hebrew NER pipeline and heavy ML deps` (from the prerequisite plan).

```bash
git show --stat HEAD
```
Expect: 3 files changed (1 new compose file, 1 new doc, 1 modified README), roughly +200 lines.

**Acceptance criteria:**
- [ ] Branch `feat/rpi3-deployment` exists
- [ ] Commit contains exactly the 3 expected files (2 new + 1 modified)
- [ ] Commit message notes the prerequisite Hebrew removal plan
- [ ] Commit message lists known gaps requiring manual Pi validation
- [ ] User explicitly approved the commit before it was created
- [ ] No push to remote performed (push is a separate explicit request)

---

## Verification summary

| Task | Verification command | Expected |
|---|---|---|
| 1 | `docker compose -f docker-compose.yml -f docker-compose.rpi3.yml config` | Exit 0, merged config shows overrides |
| 2 | `test -f docs/rpi3-deployment.md && wc -l docs/rpi3-deployment.md` | File exists, ~80–150 lines |
| 3 | `grep -n "### Raspberry Pi 3" README.md` | At least one match |
| 4 | `docker build .` + smoke test + `pytest tests/test_pii_detector.py` | All green |
| 5 | `git log --oneline -2` | Two commits, RPi3 feat on top of Hebrew refactor |

---

## Out of scope

Deliberately NOT doing in this plan:

- **Actual testing on real Pi hardware.** This plan produces files that validate via `docker compose config` on x86 but cannot be end-to-end tested without a physical Pi. Manual validation gaps are listed in Task 4 and the commit message.
- **Cross-build recipe** (`docker buildx` for arm64 from x86). First build on the Pi takes ~5 min, acceptable for now. Can be added in a follow-up if build time becomes painful.
- **CI/CD for ARM.** No GitHub Actions matrix for arm64 builds. Out of scope — can be added later if RPi3 becomes a supported deployment target.
- **Alternative overlays for Pi 4, Pi 5, Zero 2W.** This plan targets Pi 3 only. Pi 4 with 2 GB+ works fine on the default compose file already; Pi 5 is overkill.
- **Changes to defaults in `docker-compose.yml`.** The base file is untouched. All RPi3 tuning lives in the overlay.
- **Changes to `config.py` defaults.** `MAX_FILE_SIZE_MB=25` and `PROCESSING_TIMEOUT=300` stay as the code defaults. The overlay sets tighter values via environment variables only.
- **Bare-metal RPi3 install** (no Docker). Docker is the only supported RPi3 path in this plan. Bare-metal on RPi3 would need its own plan.
- **Auto-detection of RPi3 hardware** to apply the overlay automatically. The overlay is opt-in by design — users explicitly pass `-f docker-compose.rpi3.yml`.
- **Git push, PR creation, remote operations.** This plan only creates a local commit. Pushing is a separate explicit user request.
