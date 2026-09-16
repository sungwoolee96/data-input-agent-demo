# Data Input Agent Demo Design

## Purpose

Create a small public educational project that demonstrates why an LLM agent is useful for unstructured input. The demo reads two differently formatted Korean PDF maintenance notices, extracts the same three maintenance fields, and appends validated records to a CSV file by calling explicit Python tools.

The project also teaches the role of a local model. Ollama runs the model locally, Qwen3 4B chooses tools and interprets the extracted text, and one Python script connects the model to the allowed tools.

## Audience

The primary audience is a Python beginner who wants to see an agent use tools without first learning LangChain. A learner should be able to clone the repository, follow the README, run one command, step through each action by pressing Enter, and inspect the generated CSV.

## Goals

- Demonstrate an agent loop directly, without LangChain or another agent framework.
- Show that one extraction goal can be satisfied from substantially different PDF layouts and writing styles.
- Make tool selection, tool arguments, tool results, validation, and final output observable in the terminal.
- Run inference locally with Ollama and `qwen3:4b`.
- Keep all executable application logic in one readable `agent.py` file.
- Make setup and execution reproducible with uv.
- Publish the completed repository publicly as `data-input-agent-demo` under the user's personal GitHub account.

## Non-goals

- OCR or image-only PDF support.
- LangChain, LangGraph, a web interface, a database, multiple agents, or remote model APIs.
- Production-grade document ingestion, access control, or high-volume batch processing.
- Extracting several independent maintenance records from one PDF in the first version.
- General-purpose schema configuration. The first version uses one fixed maintenance schema.

## Repository Layout

```text
data-input-agent-demo/
|-- agent.py
|-- pyproject.toml
|-- uv.lock
|-- README.md
|-- LICENSE
|-- .gitignore
|-- samples/
|   |-- maintenance_notice_a.pdf
|   `-- maintenance_notice_b.pdf
|-- output/
|   `-- .gitkeep
`-- docs/
    `-- superpowers/
        `-- specs/
            `-- 2026-09-16-data-input-agent-demo-design.md
```

Only `agent.py` contains application code. The sample PDFs are ready-to-use fixtures rather than generated during normal execution.

## Data Contract

Each successfully processed PDF produces one CSV row with these columns:

| Column | Meaning | Format |
| --- | --- | --- |
| `generator_name` | Maintenance target generator name | Non-empty text |
| `maintenance_start` | Scheduled maintenance start | `YYYY-MM-DD HH:MM` |
| `maintenance_end` | Scheduled maintenance end | `YYYY-MM-DD HH:MM` |
| `source_pdf` | Source PDF filename | Filename only |

The output file is `output/maintenance_schedule.csv`. It is written using UTF-8 with a BOM so Korean text opens correctly in common Windows spreadsheet applications.

## Sample PDFs

Both PDFs are ordinary text PDFs and contain synthetic information only.

### Notice A

- Formal official-notice layout.
- Contains an organization name, document number, greeting, background, cooperation request, and other irrelevant language.
- Contains one explicit sentence identifying the generator and maintenance interval.

Expected record:

```text
generator_name: 한빛복합 2호기
maintenance_start: 2026-10-14 09:00
maintenance_end: 2026-10-16 18:00
```

### Notice B

- Informal work-plan or narrative report layout that is visually and structurally different from Notice A.
- Places the generator name, start time, and end time in separate sections.
- Mentions unrelated facilities and dates, while keeping the intended maintenance record unambiguous to a human reader.

Expected record:

```text
generator_name: 제주내연 3호기
maintenance_start: 2026-11-02 08:30
maintenance_end: 2026-11-04 17:30
```

The documents should be rendered and visually inspected after generation, and text extraction should be independently checked before they are committed.

## Runtime Components

### Local model

- Ollama is the local model runtime.
- `qwen3:4b` is the default model.
- The script checks whether Ollama is reachable and reports a concise setup command if it is not.
- The model name is defined once near the top of `agent.py` so learners can experiment with a smaller or larger compatible model.

### Tool 1: `extract_pdf_text`

Input:

- `pdf_path`

Responsibilities:

- Resolve the requested file only within the input directory supplied by the user.
- Reject missing files, non-PDF paths, and paths outside that directory.
- Extract text from every page with pypdf.
- Preserve page boundaries using readable page markers.
- Return the extracted text and basic metadata.
- Return an explicit error when no usable text exists, explaining that OCR is outside this demo.

This tool does not identify generators or dates.

### Tool 2: `append_maintenance_csv`

Inputs:

- `generator_name`
- `maintenance_start`
- `maintenance_end`
- `source_pdf`

Responsibilities:

- Require all four values.
- Parse both timestamps using the fixed output format.
- Reject a maintenance end earlier than its start.
- Reject a source filename that is not one of the current input PDFs.
- Avoid duplicate records for the same source PDF and extracted values.
- Create the CSV header when necessary and append one valid row.
- Return a concise success, duplicate, or validation result.

The model never writes files directly. CSV mutation is available only through this tool.

## Agent Loop

For each PDF in the selected input directory, the outer Python loop creates a fresh message history and gives the agent one goal: extract the maintenance target and interval, then save one validated record.

The inner agent loop performs these steps:

1. Send the task, system instructions, and two tool definitions to Ollama.
2. Receive either tool calls or a final response.
3. For each recognized tool call, display its name and arguments.
4. Pause in teaching mode before execution.
5. Execute the corresponding allowlisted Python function.
6. Display a bounded, readable representation of the tool result.
7. Pause again in teaching mode.
8. Add the result to the conversation and call the model again.
9. Stop when the model returns a final response or the iteration limit is reached.

The dispatcher rejects unknown tool names. The iteration limit prevents an accidental infinite loop. A PDF is counted as successful only when the CSV tool returns success or a verified duplicate result.

## Teaching Interaction

Teaching mode is the default:

```powershell
uv run agent.py samples
```

At meaningful boundaries, the terminal displays the current step and waits for Enter. The pauses control presentation speed; they do not ask the user to make the model's decisions.

The terminal shows:

- The current PDF.
- The request being sent to the local model.
- The selected tool and its arguments.
- PDF page count, extracted character count, and a bounded text preview.
- The structured maintenance values passed to the write tool.
- Validation and CSV results.
- A per-file and final summary.

The terminal does not display hidden chain-of-thought. It exposes only observable inputs, actions, and outputs.

Automatic mode runs the same logic without Enter pauses:

```powershell
uv run agent.py samples --auto
```

## Prompt Rules

The system prompt tells the model to:

- Use `extract_pdf_text` before making claims about a PDF.
- Extract only the generator maintenance event requested by the task.
- Ignore greetings, background explanations, unrelated facilities, and unrelated dates.
- Preserve the explicit time when present.
- Never invent missing values.
- Refuse to call the CSV tool if the generator, start, or end is ambiguous.
- Normalize timestamps to `YYYY-MM-DD HH:MM` before calling the CSV tool.
- Use only the two supplied tools.
- Finish with a concise statement of what was or was not saved.

The model runs with low temperature for more repeatable extraction and tool selection.

## Errors and Recovery

- Missing input directory: exit with the expected command form.
- Directory contains no PDFs: exit without creating an empty result file.
- Ollama unavailable: show how to install or start Ollama.
- Model unavailable: show `ollama pull qwen3:4b`.
- Empty extracted text: identify the file as unsupported and continue to the next PDF.
- Invalid or ambiguous model arguments: return the validation error to the agent once so it can correct them.
- Repeated invalid calls or iteration-limit exhaustion: mark that PDF as failed without writing partial data.
- Keyboard interrupt: exit cleanly and preserve already written valid rows.

Failures in one PDF do not prevent the next PDF from being processed.

## Dependencies and Setup

Runtime dependencies are limited to:

- `ollama`
- `pypdf`

The repository includes `pyproject.toml` and `uv.lock`. The README explains:

1. Install uv.
2. Install and start Ollama.
3. Download `qwen3:4b` with `ollama pull qwen3:4b`.
4. Clone the repository.
5. Run `uv sync`.
6. Run the teaching or automatic command.
7. Inspect the generated CSV.

It also explains the distinction among the local model, Ollama, the Python agent loop, and the tools; why the project is not LangChain; and how the same loop relates to LangChain's agent abstraction.

## Verification

Before publication:

- Render both sample PDFs and visually inspect every page.
- Extract both PDFs with the same pypdf path used by the tool and confirm meaningful Korean text is present.
- Unit-check CSV validation for missing fields, invalid timestamp formats, reversed intervals, valid records, and duplicates.
- Run the complete project against both PDFs using `qwen3:4b` when Ollama is available.
- Confirm the generated CSV has exactly the two expected records and four expected columns.
- Run automatic mode and confirm it completes without interactive input.
- Run teaching mode far enough to confirm that Enter pauses occur before and after tool execution.
- Confirm a rerun does not duplicate records.
- Confirm no secrets, machine-specific paths, generated CSV output, model files, or local environments are committed.
- Review the README commands from a clean checkout perspective.

If the local model cannot be executed in the build environment, deterministic tool tests and PDF extraction tests still run, but publication notes must clearly identify the unverified end-to-end model step. The project is not presented as fully verified until the end-to-end model run passes.

## Completion Criteria

The project is complete when:

- The public repository is named `data-input-agent-demo`.
- The local Desktop checkout contains the same committed state as the public `main` branch.
- A learner can follow the README without needing unstated setup knowledge.
- All application logic remains in one approachable `agent.py` file.
- The default run visibly demonstrates model selection of PDF-reading and CSV-writing tools.
- Both differently formatted sample PDFs produce the expected normalized records.
- The output is validated, traceable to its source PDF, and duplicate-safe.
