# Data Input Agent Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and publish a reproducible local-model agent demo that reads two differently formatted Korean maintenance PDFs and appends validated maintenance records to CSV through explicit tools.

**Architecture:** One learner-facing `agent.py` contains the CLI, two tool implementations, the Ollama agent loop, and step-by-step terminal presentation. Pure validation and file functions remain independently testable; the Ollama call is injected in tests so only the external model boundary is replaced. Two prebuilt text PDFs provide deterministic unstructured inputs, while uv manages the Python environment.

**Tech Stack:** Python 3.11+, uv, Ollama Python SDK, `qwen3:4b`, pypdf, pytest, reportlab for sample generation, Poppler for PDF rendering, Git, GitHub CLI.

**Spec:** `docs/superpowers/specs/2026-09-16-data-input-agent-demo-design.md`

## Global Constraints

- Keep all learner-facing application logic in one `agent.py` file.
- Use Ollama with `qwen3:4b`; do not add LangChain, LangGraph, remote LLM APIs, OCR, a web UI, or a database.
- Support ordinary text PDFs only and process exactly one maintenance record per PDF.
- Expose only `extract_pdf_text` and `append_maintenance_csv` to the model.
- Write `generator_name`, `maintenance_start`, `maintenance_end`, and `source_pdf` to `output/maintenance_schedule.csv` using UTF-8 with BOM.
- Default to Enter-controlled teaching mode and support uninterrupted execution with `--auto`.
- Never show model chain-of-thought; show only requests, tool calls, arguments, bounded tool results, and final status.
- Publish synthetic data only and never commit generated output, model files, virtual environments, credentials, or machine-specific paths.
- Work on `feature/initial-demo` until verification is complete; merge to `main` only for the requested public release.

---

### Task 1: Reproducible project and first failing tests

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `tests/test_agent.py`
- Create: `agent.py`

**Interfaces:**
- Produces: `extract_pdf_text(pdf_path: str, allowed_input_dir: Path) -> dict[str, object]`
- Produces: `append_maintenance_csv(csv_path: Path, allowed_sources: set[str], generator_name: str, maintenance_start: str, maintenance_end: str, source_pdf: str) -> dict[str, str]`
- Produces: `pause_for_user(auto: bool, message: str) -> None`
- Produces: `run_agent_for_pdf(pdf_path: Path, input_dir: Path, csv_path: Path, model: str, auto: bool, chat_fn: Callable[..., object]) -> bool`

- [ ] **Step 1: Create the feature branch and uv configuration**

Run:

```powershell
git switch -c feature/initial-demo
uv init --bare
uv add ollama pypdf
uv add --dev pytest reportlab
```

Set `requires-python = ">=3.11"` and configure pytest to use `tests` with quiet output.

- [ ] **Step 2: Add repository exclusions**

Create `.gitignore` entries for `.venv/`, `__pycache__/`, `.pytest_cache/`, `output/*.csv`, `tmp/`, and generated render images while retaining `output/.gitkeep`.

- [ ] **Step 3: Write the first failing extraction tests**

Use reportlab in a test helper to create a text PDF in `tmp_path`. Test that `extract_pdf_text` returns literal values for `status`, `page_count`, `char_count`, `source_pdf`, and extracted text. Add separate tests that a `.txt` file and a path outside the allowed input directory return `status == "error"` and do not expose external file contents.

- [ ] **Step 4: Run the extraction tests and confirm RED**

Run:

```powershell
uv run pytest tests/test_agent.py -k extract_pdf_text -q
```

Expected: collection fails because `extract_pdf_text` is not yet defined.

- [ ] **Step 5: Add an importable empty `agent.py` only if needed for test collection**

The file may contain imports and constants but no implementation of `extract_pdf_text`; rerun until the test fails specifically because the function is missing.

- [ ] **Step 6: Commit the red test and project configuration**

```powershell
git add pyproject.toml uv.lock .gitignore tests/test_agent.py agent.py
git commit -m "test: define PDF extraction contract"
```

### Task 2: PDF extraction and CSV write tools

**Files:**
- Modify: `agent.py`
- Modify: `tests/test_agent.py`

**Interfaces:**
- `extract_pdf_text(pdf_path, allowed_input_dir)` returns a dictionary with `status`, `source_pdf`, `page_count`, `char_count`, and `text`, or `status` and `error`.
- `append_maintenance_csv(...)` returns `{"status": "saved", "source_pdf": name}`, `{"status": "duplicate", ...}`, or `{"status": "error", "error": message}`.

- [ ] **Step 1: Implement the minimum safe PDF extraction tool**

Resolve both paths, require the PDF to be an existing file below `allowed_input_dir`, require a `.pdf` suffix, read every page with `PdfReader`, add `--- PAGE N ---` markers, and reject blank extracted text.

- [ ] **Step 2: Run extraction tests and confirm GREEN**

```powershell
uv run pytest tests/test_agent.py -k extract_pdf_text -q
```

Expected: all extraction tests pass.

- [ ] **Step 3: Write failing CSV behavior tests**

Add literal cases for a valid row, the exact four-column header, UTF-8 BOM, missing generator name, invalid timestamp, end before start, disallowed `source_pdf`, and duplicate suppression. Assert file contents and row counts rather than implementation details.

- [ ] **Step 4: Run CSV tests and confirm RED**

```powershell
uv run pytest tests/test_agent.py -k append_maintenance_csv -q
```

Expected: tests fail because `append_maintenance_csv` is not defined.

- [ ] **Step 5: Implement minimal CSV validation and append behavior**

Use `datetime.strptime(value, "%Y-%m-%d %H:%M")`, `csv.DictReader`, and `csv.DictWriter`. Create parent directories, open new files with `encoding="utf-8-sig"`, and treat an exact four-field match as a duplicate.

- [ ] **Step 6: Run all tool tests and confirm GREEN**

```powershell
uv run pytest tests/test_agent.py -k "extract_pdf_text or append_maintenance_csv" -q
```

- [ ] **Step 7: Commit the tools**

```powershell
git add agent.py tests/test_agent.py
git commit -m "feat: add guarded PDF and CSV tools"
```

### Task 3: Teaching pauses and Ollama agent loop

**Files:**
- Modify: `agent.py`
- Modify: `tests/test_agent.py`

**Interfaces:**
- `pause_for_user(auto, message)` waits only when `auto` is false.
- `run_agent_for_pdf(...)` supplies two bound tool functions to `chat_fn`, executes allowlisted calls, appends tool results to message history, enforces a five-turn limit, and returns whether the CSV tool saved or confirmed a duplicate.

- [ ] **Step 1: Write failing pause tests**

Monkeypatch `builtins.input`. Assert teaching mode calls it once with the supplied label and automatic mode never calls it.

- [ ] **Step 2: Run pause tests and confirm RED**

```powershell
uv run pytest tests/test_agent.py -k pause_for_user -q
```

- [ ] **Step 3: Implement the pause helper and confirm GREEN**

Implement one conditional `input()` call and rerun the focused tests.

- [ ] **Step 4: Write a failing real-loop behavior test**

Provide a `FakeChat` that returns three complete Ollama-shaped messages: first an `extract_pdf_text` tool call, then an `append_maintenance_csv` call containing the literal expected record, then a final text response. Use a real temporary PDF and CSV. Assert the CSV row, three model calls, tool-result messages in history, and `True` return value. The fake replaces only the external local-model call; both tools and the dispatcher remain real.

- [ ] **Step 5: Run the loop test and confirm RED**

```powershell
uv run pytest tests/test_agent.py -k run_agent_for_pdf -q
```

- [ ] **Step 6: Implement the agent loop and observable logging**

Add two nested tool wrappers with clear docstrings, an explicit name-to-function allowlist, JSON tool-result serialization with `ensure_ascii=False`, a bounded PDF preview for terminal output, tool-result messages, a five-turn loop, and success tracking. Call Ollama with temperature zero and thinking disabled. Do not print hidden thinking fields.

- [ ] **Step 7: Add and test batch CLI behavior**

Implement `main(argv: list[str] | None = None) -> int` using argparse with positional `input_dir`, `--auto`, and `--model`. Sort `*.pdf`, process failures independently, print a final summary, and return nonzero when no PDF succeeds. Add tests for missing directories and empty directories, then verify their initial failure and final pass.

- [ ] **Step 8: Run the complete test suite and commit**

```powershell
uv run pytest -q
git add agent.py tests/test_agent.py
git commit -m "feat: add observable local agent loop"
```

### Task 4: Create and verify the two sample PDFs

**Files:**
- Create: `samples/maintenance_notice_a.pdf`
- Create: `samples/maintenance_notice_b.pdf`
- Temporary: `tmp/pdfs/make_samples.py`
- Temporary: `tmp/pdfs/rendered/*.png`

**Interfaces:**
- Notice A extracts the expected 한빛복합 2호기 record.
- Notice B extracts the expected 제주내연 3호기 record.

- [ ] **Step 1: Mark the PDF authoring operation**

Run the required artifact marker once with operation `create`, expected output count `2`, and output format `pdf` immediately before PDF generation.

- [ ] **Step 2: Generate synthetic Korean PDFs with an embedded Korean font**

Use reportlab and an available Korean TrueType font. Give Notice A a formal memo layout and Notice B a distinct report/card layout. Include irrelevant prose and unrelated dates, but keep one target maintenance interval per PDF unambiguous.

- [ ] **Step 3: Add failing sample-contract tests before accepting the files**

Test each committed PDF through `extract_pdf_text`. Assert meaningful Korean text, the target generator name, and the literal source date/time fragments are present. Run before the PDFs are copied into `samples` to confirm failure due to missing fixtures, then add the generated PDFs and confirm pass.

- [ ] **Step 4: Render and visually inspect both PDFs**

Use Poppler to render every page to PNG. Inspect every rendered page for Korean glyphs, clipping, overlap, hierarchy, and visual distinction. Regenerate and rerender if any defect appears.

- [ ] **Step 5: Confirm text extraction and commit samples**

```powershell
uv run pytest tests/test_agent.py -k sample_pdf -q
git add samples tests/test_agent.py
git commit -m "test: add contrasting maintenance PDF fixtures"
```

Delete temporary builders and renders after verification.

### Task 5: Learner documentation and repository metadata

**Files:**
- Create: `README.md`
- Create: `LICENSE`
- Create: `output/.gitkeep`
- Modify: `pyproject.toml`

**Interfaces:**
- The documented commands are `ollama pull qwen3:4b`, `uv sync`, `uv run agent.py samples`, and `uv run agent.py samples --auto`.

- [ ] **Step 1: Write the README**

Explain the demo outcome first, then prerequisites, Windows-friendly installation links, exact uv and Ollama commands, expected step logs, expected four-column CSV, architecture, two tool boundaries, local-model roles, why this is not LangChain, exercises, limitations, and troubleshooting.

- [ ] **Step 2: Add public repository metadata**

Add the MIT license, package description, and empty output directory marker. Confirm `.gitignore` excludes generated CSV output.

- [ ] **Step 3: Verify README commands and commit**

Run `uv sync`, `uv run agent.py --help`, and the deterministic test suite. Correct any stale command or filename before committing.

```powershell
git add README.md LICENSE pyproject.toml uv.lock output/.gitkeep .gitignore
git commit -m "docs: add local agent learning guide"
```

### Task 6: End-to-end verification and public release

**Files:**
- Verify: all committed project files
- Generate but do not commit: `output/maintenance_schedule.csv`

**Interfaces:**
- Public GitHub repository: `https://github.com/sungwoolee96/data-input-agent-demo`
- Final local checkout: `%USERPROFILE%\Desktop\data-input-agent-demo`

- [ ] **Step 1: Run clean deterministic verification**

```powershell
uv run pytest -q
uv run python -m compileall -q agent.py tests
git diff --check
git status --short
```

- [ ] **Step 2: Verify actual local-model availability**

Run `ollama list`. If `qwen3:4b` is absent, download it with `ollama pull qwen3:4b`. Confirm Ollama responds before the end-to-end test.

- [ ] **Step 3: Run automatic end-to-end processing**

Remove only the generated `output/maintenance_schedule.csv` if present, then run:

```powershell
uv run agent.py samples --auto
```

Independently inspect the CSV and require exactly the header plus the two expected rows.

- [ ] **Step 4: Verify teaching-mode pauses**

Run the default command in a PTY, confirm it stops at the first Enter prompt, advance through at least one complete PDF, and terminate only after confirming pauses before and after tool execution.

- [ ] **Step 5: Verify duplicate behavior**

Run automatic mode a second time and confirm the CSV remains at two records.

- [ ] **Step 6: Review scope and repository cleanliness**

Confirm no model files, credentials, local paths, generated CSV, `.venv`, temporary builders, rendered PNGs, or unrelated files are tracked. Re-read the design completion criteria against the final tree.

- [ ] **Step 7: Complete the feature branch**

Use the finishing-development-branch workflow, merge the verified feature branch into `main`, and create a release commit only if needed.

- [ ] **Step 8: Publish and create the requested Desktop checkout**

Confirm GitHub CLI authentication for `sungwoolee96` and that `sungwoolee96/data-input-agent-demo` does not already exist. Create it as a public repository from verified `main` and push. Clone the public repository to `%USERPROFILE%\Desktop\data-input-agent-demo`, then verify its `HEAD` matches `origin/main` and the working tree is clean.
