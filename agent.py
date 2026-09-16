"""Minimal local-model PDF data input agent."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Callable

from httpx import ConnectError
from ollama import ResponseError, chat
from pypdf import PdfReader


# STEP 1. 모델에게 맡길 일과 결과 양식을 정합니다.
# 모델은 문서를 해석하고, CSV_HEADERS는 결과가 따라야 할 공통 형식을 정합니다.

# 사용할 언어모델입니다. 본 예제는 사용자의 컴퓨터에서 직접 서빙하는 로컬 언어 모델을 사용합니다.
DEFAULT_MODEL = "qwen3:4b"
DATETIME_FORMAT = "%Y-%m-%d %H:%M"
CSV_HEADERS = [
    "generator_name",
    "maintenance_start",
    "maintenance_end",
    "source_pdf",
]
MAX_AGENT_TURNS = 5

# 언어 모델에게 제공되는 프롬프트입니다.
# 목표와 판단 규칙을 전달하고, PDF를 읽은 뒤에만 CSV 쓰기 툴을 고르도록 안내합니다.
SYSTEM_PROMPT = """당신은 발전기 정비 공지 PDF를 정형 데이터로 바꾸는 에이전트입니다.

규칙:
1. 반드시 extract_pdf_text로 PDF 원문을 읽은 뒤 판단합니다.
2. 공지의 핵심 정비 대상 발전기 하나와 정비 시작/종료 시각만 찾습니다.
3. 인사말, 배경 설명, 관련 없는 설비와 날짜는 무시합니다.
4. 값이 명확할 때만 append_maintenance_csv를 호출합니다.
5. 날짜는 YYYY-MM-DD HH:MM 형식으로 전달합니다.
6. 없는 값은 추측하지 않습니다. 애매하면 CSV를 쓰지 말고 이유를 답합니다.
7. 제공된 두 툴 외에는 사용할 수 없습니다.
"""


def pause_for_user(auto: bool, message: str) -> None:
    """Pause a teaching-mode run until the learner presses Enter."""
    # [예제와 무관합니다, 무시해주세요] 기본 모드는 발표자가 로그를 설명할 시간을 주고, --auto일 때만 멈춤을 건너뜁니다.
    if not auto:
        input(f"\n[Enter] {message}")


def print_learning_step(number: int, code_step: int, title: str) -> None:
    """Connect an observable demo action to the numbered code block."""
    # [예제와 무관합니다, 무시해주세요] 숫자는 프로그램의 내부 판단이 아니라 학습자가 따라 읽을 순서를 뜻합니다.
    print(f"\n[학습 {number}/5 | 코드 STEP {code_step}: {title}]")


# STEP 2. PDF 내용을 모델에 전달할 수 있도록 읽기 툴을 정의합니다.
# 모델은 파일을 직접 열지 않고 이 툴의 호출을 요청합니다.
# Python은 요청된 경로를 확인한 뒤 허용된 PDF의 텍스트만 추출합니다.
def extract_pdf_text(pdf_path: str, allowed_input_dir: Path) -> dict[str, object]:
    """Extract page-marked text from one PDF inside the allowed directory."""
    if not isinstance(pdf_path, str):
        return {"status": "error", "error": "PDF 경로는 문자열이어야 합니다."}

    allowed_dir = allowed_input_dir.resolve()
    candidate = Path(pdf_path).resolve()

    # 경로를 먼저 정규화한 뒤 입력 폴더 밖으로 나가는 요청을 차단합니다.
    if not candidate.is_relative_to(allowed_dir):
        return {"status": "error", "error": "허용된 입력 폴더 밖의 파일은 읽을 수 없습니다."}
    if candidate.suffix.lower() != ".pdf":
        return {"status": "error", "error": "PDF 파일만 읽을 수 있습니다."}
    if not candidate.is_file():
        return {"status": "error", "error": "PDF 파일을 찾을 수 없습니다."}

    try:
        reader = PdfReader(candidate)
        page_blocks = []
        extracted_pages = []
        for page_number, page in enumerate(reader.pages, start=1):
            page_text = (page.extract_text() or "").strip()
            extracted_pages.append(page_text)
            page_blocks.append(f"--- PAGE {page_number} ---\n{page_text}")
    except Exception as exc:
        return {"status": "error", "error": f"PDF 읽기 실패: {exc}"}

    text = "\n\n".join(page_blocks).strip()
    extracted_content = "".join(extracted_pages)
    if not extracted_content:
        return {
            "status": "error",
            "error": "추출 가능한 텍스트가 없습니다. 이 데모는 OCR을 지원하지 않습니다.",
        }

    return {
        "status": "ok",
        "source_pdf": candidate.name,
        "page_count": len(reader.pages),
        "char_count": len(text),
        "text": text,
    }


# STEP 3. 모델이 제안한 정비 정보를 CSV에 저장하는 툴을 정의합니다.
# 모델의 해석이 곧바로 저장되지는 않습니다. Python이 필수 값, 날짜와 출처를 검증한 뒤 기록하도록 코드 기반의 안전장치를 둡니다.
def append_maintenance_csv(
    csv_path: Path,
    allowed_sources: set[str],
    generator_name: str,
    maintenance_start: str,
    maintenance_end: str,
    source_pdf: str,
) -> dict[str, str]:
    """Validate and append one maintenance record to a UTF-8 BOM CSV."""
    values = (generator_name, maintenance_start, maintenance_end, source_pdf)
    if not all(isinstance(value, str) for value in values):
        return {"status": "error", "error": "CSV에 저장할 값은 모두 문자열이어야 합니다."}

    generator_name = generator_name.strip()
    maintenance_start = maintenance_start.strip()
    maintenance_end = maintenance_end.strip()
    source_pdf = source_pdf.strip()

    # 모델이 값을 빠뜨리거나 다른 PDF를 출처로 적어도 잘못된 행은 저장하지 않습니다.
    if not generator_name:
        return {"status": "error", "error": "발전기 이름이 필요합니다."}
    if not maintenance_start or not maintenance_end:
        return {"status": "error", "error": "정비 시작과 종료 시각이 모두 필요합니다."}
    if source_pdf != Path(source_pdf).name or source_pdf not in allowed_sources:
        return {"status": "error", "error": "현재 입력 PDF의 파일명만 사용할 수 있습니다."}

    try:
        start = datetime.strptime(maintenance_start, DATETIME_FORMAT)
        end = datetime.strptime(maintenance_end, DATETIME_FORMAT)
    except ValueError:
        return {
            "status": "error",
            "error": "날짜는 YYYY-MM-DD HH:MM 형식이어야 합니다.",
        }
    if start.strftime(DATETIME_FORMAT) != maintenance_start or end.strftime(DATETIME_FORMAT) != maintenance_end:
        return {
            "status": "error",
            "error": "날짜는 YYYY-MM-DD HH:MM 형식이어야 합니다.",
        }
    if end < start:
        return {"status": "error", "error": "정비 종료 시각은 시작 시각보다 빠를 수 없습니다."}

    row = {
        "generator_name": generator_name,
        "maintenance_start": maintenance_start,
        "maintenance_end": maintenance_end,
        "source_pdf": source_pdf,
    }

    # 기존 결과의 형식이 다르거나 같은 PDF의 기록이 이미 있으면 새 행을 추가하지 않습니다.
    if csv_path.exists() and csv_path.stat().st_size > 0:
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames != CSV_HEADERS:
                    return {"status": "error", "error": "기존 CSV의 컬럼 형식이 예상과 다릅니다."}
                existing_rows = list(reader)
                if row in existing_rows:
                    return {"status": "duplicate", "source_pdf": source_pdf}
                if any(existing.get("source_pdf") == source_pdf for existing in existing_rows):
                    return {
                        "status": "error",
                        "error": "이 PDF에는 이미 다른 정비 기록이 저장되어 있습니다.",
                    }
        except OSError as exc:
            return {"status": "error", "error": f"기존 CSV 읽기 실패: {exc}"}

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    try:
        with csv_path.open("a", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
            if write_header:
                writer.writeheader()
            writer.writerow(row)
    except OSError as exc:
        return {"status": "error", "error": f"CSV 쓰기 실패: {exc}"}

    return {"status": "saved", "source_pdf": source_pdf}


def _print_tool_result(tool_name: str, result_text: str) -> None:
    """Print an observable, bounded view of a tool result."""
    # [예제와 무관합니다, 무시해주세요] 시연 화면에 툴 결과를 짧게 보여주는 출력 코드입니다.
    try:
        result = json.loads(result_text)
    except json.JSONDecodeError:
        print(f"[툴 결과] {result_text}")
        return

    if tool_name == "extract_pdf_text" and result.get("status") == "ok":
        print("[툴 결과] PDF 텍스트 추출 완료")
        print(f"  페이지 수: {result['page_count']}")
        print(f"  추출 문자 수: {result['char_count']}")
        return

    if result.get("status") in {"saved", "duplicate"}:
        label = "CSV 저장 완료" if result["status"] == "saved" else "이미 저장된 기록"
        print(f"[툴 결과] {label}: {result.get('source_pdf', '')}")
        return

    print(f"[툴 결과] {result.get('error', result_text)}")


def _visible_model_content(content: str) -> str:
    """Return only the user-facing portion of a model response."""
    # [예제와 무관합니다, 무시해주세요] 시연 화면에는 모델의 최종 답변만 표시합니다.
    if "</think>" in content:
        content = content.rsplit("</think>", maxsplit=1)[1]
    return content.strip()


def _call_model_with_preview(
    call_model: Callable[..., object], model: str, messages: list[object], tools: list[object]
) -> object:
    """Collect one streamed reply while showing a temporary terminal preview."""
    # [예제와 무관합니다, 무시해주세요] 모델이 답하는 동안 한 줄만 갱신하고 완료되면 지웁니다.
    terminal = sys.stdout.isatty()
    shown_width = 0

    def display_width(value: str) -> int:
        return sum(2 if unicodedata.east_asian_width(char) in "FW" else 1 for char in value)

    def show(value: str) -> None:
        nonlocal shown_width
        width = display_width(value)
        sys.stdout.write("\r" + value + " " * max(0, shown_width - width))
        sys.stdout.flush()
        shown_width = max(shown_width, width)

    if terminal:
        sys.stdout.write("\n")
        show("[모델] 로컬 언어 모델 추론 중...")
    else:
        print("\n[모델] 로컬 언어 모델 추론 중...", flush=True)

    thinking_parts: list[str] = []
    content_parts: list[str] = []
    tool_calls: list[object] = []
    thought_tail = ""
    message = None
    try:
        stream = call_model(
            model=model,
            messages=messages,
            tools=tools,
            stream=True,
            think=True,
            options={"temperature": 0},
        )
        for chunk in stream:
            message = chunk.message
            thinking = getattr(message, "thinking", None) or ""
            if thinking:
                thinking_parts.append(thinking)
                thought_tail = (thought_tail + thinking)[-100:]
                if terminal:
                    visible = " ".join(
                        "".join(char for char in thought_tail if char.isprintable() or char.isspace()).split()
                    )[-24:]
                    if visible:
                        show(f"[모델 생각] {visible}")
            content_parts.append(message.content or "")
            tool_calls.extend(message.tool_calls or [])
    finally:
        if terminal:
            sys.stdout.write("\r" + " " * shown_width + "\r")
            sys.stdout.flush()

    if message is None:
        raise ValueError("모델이 빈 스트림을 반환했습니다.")
    message.thinking = "".join(thinking_parts)
    message.content = "".join(content_parts)
    message.tool_calls = tool_calls
    return message


# STEP 4. 모델의 선택과 Python의 툴 실행을 실제로 연결하는 부분입니다.
# 모델에 목표와 툴을 제공하고, 모델이 선택한 툴을 실행한 뒤 결과를 다시 모델에 전달합니다.
# 모델은 각 단계의 결과를 프롬프트로 받아본 다음 행동을 정합니다. 이 구조가 본 예제의 에이전틱 구조의 핵심입니다.
def run_agent_for_pdf(
    pdf_path: Path,
    input_dir: Path,
    csv_path: Path,
    model: str,
    auto: bool,
    chat_fn: Callable[..., object] | None = None,
) -> bool:
    """Run a bounded model-tool loop for one PDF and report write success."""
    call_model = chat_fn or chat
    current_pdf = pdf_path.resolve()
    state: dict[str, str | None] = {"extracted_source": None}
    saved = False

    # STEP 2·3의 함수를 모델이 선택할 수 있는 툴 형태로 연결합니다.
    # Ollama는 아래 함수의 이름·설명·인자를 모델에 알려주고, 모델은 호출할 툴과 인자만 제안합니다.
    # 실제 경로 확인과 파일 읽기·쓰기는 모델이 아니라 이 Python 함수들이 수행합니다.
    def extract_pdf_text_tool(pdf_path: str) -> str:
        """Extract text from a PDF in the selected input folder.

        Args:
            pdf_path: The PDF path supplied in the current task.

        Returns:
            JSON containing page-marked text and extraction metadata.
        """
        if not isinstance(pdf_path, str) or Path(pdf_path).resolve() != current_pdf:
            result: dict[str, object] = {
                "status": "error",
                "error": "현재 처리 중인 PDF만 읽을 수 있습니다.",
            }
        else:
            result = extract_pdf_text(pdf_path, input_dir)
        state["extracted_source"] = (
            str(result["source_pdf"]) if result.get("status") == "ok" else None
        )
        return json.dumps(result, ensure_ascii=False)

    extract_pdf_text_tool.__name__ = "extract_pdf_text"

    def append_maintenance_csv_tool(
        generator_name: str,
        maintenance_start: str,
        maintenance_end: str,
        source_pdf: str,
    ) -> str:
        """Validate and append one generator maintenance record to CSV.

        Args:
            generator_name: Exact generator name from the PDF.
            maintenance_start: Start timestamp in YYYY-MM-DD HH:MM format.
            maintenance_end: End timestamp in YYYY-MM-DD HH:MM format.
            source_pdf: Filename of the PDF that supplied the values.

        Returns:
            JSON describing whether the row was saved, duplicated, or rejected.
        """
        # 모델이 CSV 쓰기를 먼저 요청해도, 현재 PDF의 읽기 툴이 성공한 뒤에만 저장하도록 제한합니다.
        if state["extracted_source"] != pdf_path.name:
            return json.dumps(
                {"status": "error", "error": "CSV 저장 전에 PDF 추출 툴을 사용해야 합니다."},
                ensure_ascii=False,
            )
        result = append_maintenance_csv(
            csv_path=csv_path,
            allowed_sources={pdf_path.name},
            generator_name=generator_name,
            maintenance_start=maintenance_start,
            maintenance_end=maintenance_end,
            source_pdf=source_pdf,
        )
        return json.dumps(result, ensure_ascii=False)

    append_maintenance_csv_tool.__name__ = "append_maintenance_csv"
    # 이 허용 목록에 없는 이름은 모델이 요청해도 실행하지 않습니다.
    available_tools = {
        "extract_pdf_text": extract_pdf_text_tool,
        "append_maintenance_csv": append_maintenance_csv_tool,
    }
    tool_list = list(available_tools.values())
    # 첫 메시지에 목표를 넣고, 이후 모델의 선택과 툴 결과를 같은 대화 기록에 이어 붙입니다.
    messages: list[object] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"다음 PDF에서 발전기 정비 정보를 찾아 CSV에 저장하세요.\n"
                f"PDF 경로: {pdf_path.resolve()}\n"
                f"출처 파일명: {pdf_path.name}"
            ),
        },
    ]

    print(f"\n{'=' * 64}")
    print(f"[문서] {pdf_path.name}")
    print_learning_step(1, 4, "에이전트 반복문")
    print("로컬 언어 모델에 PDF 처리 목표와 사용할 수 있는 툴 목록을 전달합니다.")
    print("처리 목표: PDF를 읽어 발전기와 정비 시작·종료 시각을 찾아 CSV로 정리합니다.")
    print("사용 가능한 툴:")
    print("  extract_pdf_text: 허용된 PDF의 내용을 텍스트로 읽습니다.")
    print("  append_maintenance_csv: 모델이 찾은 값을 검증해 CSV에 저장합니다.")
    pause_for_user(auto, "로컬 모델에 작업을 전달합니다...")

    # 이 for문은 에이전트가 다음 행동을 결정하는 핵심 반복문입니다.
    # 매 턴 모델에 대화 기록과 툴 목록을 보내고, 응답에서 툴 호출 요청을 확인합니다.
    # 아래에서 툴을 실행하고 결과를 기록하면 다음 턴의 모델이 그 결과를 보고 이어서 판단합니다.
    # MAX_AGENT_TURNS는 이 과정을 끝없이 반복하지 않도록 제한합니다.
    for _turn in range(MAX_AGENT_TURNS):
        message = _call_model_with_preview(call_model, model, messages, tool_list)
        messages.append(message)
        tool_calls = message.tool_calls or []

        # 툴 호출이 없는 응답은 모델이 작업을 끝냈다는 신호로 사용합니다.
        if not tool_calls:
            final_text = _visible_model_content(message.content or "")
            print(f"\n[에이전트 최종 답변] {final_text or '응답 없음'}")
            return saved

        for tool_call in tool_calls:
            function_call = getattr(tool_call, "function", None)
            raw_tool_name = getattr(function_call, "name", None)
            raw_arguments = getattr(function_call, "arguments", None)
            valid_call = isinstance(raw_tool_name, str) and isinstance(raw_arguments, dict)
            tool_name = raw_tool_name if isinstance(raw_tool_name, str) else "invalid_tool_call"
            arguments = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
            if tool_name == "extract_pdf_text" and valid_call:
                print_learning_step(2, 2, "PDF 읽기 툴")
                print("모델이 PDF 읽기 툴을 선택했습니다. Python이 현재 문서인지 확인한 뒤 텍스트를 추출합니다.")
                print(f"\n[툴 호출] {tool_name}")
            elif tool_name == "append_maintenance_csv" and valid_call:
                print_learning_step(3, 4, "모델의 정보 해석")
                print("로컬 언어 모델이 PDF에서 다음 핵심 정보를 추출했다고 제안했습니다.")
                for label, field in (
                    ("발전기", "generator_name"),
                    ("정비 시작", "maintenance_start"),
                    ("정비 종료", "maintenance_end"),
                    ("출처 PDF", "source_pdf"),
                ):
                    value = arguments.get(field)
                    print(f"  {label}: {value if isinstance(value, str) else '(값 없음 또는 형식 오류)'}")
                print(f"\n[툴 호출] {tool_name}")
                print("로컬 모델이 CSV 쓰기 툴을 호출했습니다.")
                print("CSV 쓰기 툴은 이 값이 요구사항에 맞는지 검증한 뒤 저장합니다.")
            else:
                print(f"\n[툴 호출] {tool_name}")
            if tool_name == "append_maintenance_csv" and valid_call:
                pause_for_user(auto, "append_maintenance_csv CSV 쓰기 툴을 실행합니다...")
            else:
                pause_for_user(auto, "툴이 실행됩니다...")

            # 모델이 보낸 툴 이름과 인자를 신뢰하지 않고 형식, 허용 목록, 함수 시그니처로 확인합니다.
            function = available_tools.get(tool_name) if valid_call else None
            if not valid_call:
                result_text = json.dumps(
                    {"status": "error", "error": "툴 호출 형식이 올바르지 않습니다."},
                    ensure_ascii=False,
                )
            elif function is None:
                result_text = json.dumps(
                    {"status": "error", "error": f"허용되지 않은 툴: {tool_name}"},
                    ensure_ascii=False,
                )
            else:
                try:
                    result_text = function(**arguments)
                except TypeError:
                    result_text = json.dumps(
                        {"status": "error", "error": "툴 인자 형식이 올바르지 않습니다."},
                        ensure_ascii=False,
                    )
                except Exception:
                    result_text = json.dumps(
                        {"status": "error", "error": "툴 실행 중 예상하지 못한 오류가 발생했습니다."},
                        ensure_ascii=False,
                    )

            _print_tool_result(tool_name, result_text)
            try:
                result_data = json.loads(result_text)
            except json.JSONDecodeError:
                result_data = {}
            if tool_name == "append_maintenance_csv" and result_data.get("status") in {
                "saved",
                "duplicate",
            }:
                saved = True

            if tool_name == "extract_pdf_text" and result_data.get("status") == "ok":
                preview = str(result_data["text"])
                if len(preview) > 1200:
                    preview = preview[:1200].rstrip() + "\n... (이하 생략)"
                print("\n추출된 내용은 아래와 같습니다:")
                print(preview)
                print("\n에이전트가 호출한 툴을 통해 PDF 내용이 텍스트로 추출됐습니다.")
                print("이제 로컬 언어 모델은 이 내용을 주입받아 발전기와 정비 기간을 해석합니다.")
            elif tool_name == "append_maintenance_csv" and result_data.get("status") in {
                "saved", "duplicate"
            }:
                print_learning_step(4, 3, "CSV 쓰기 툴")
                print("CSV 쓰기 툴이 필수 값, 날짜 형식과 순서, 출처, 기존 기록을 검증했습니다.")
                if result_data["status"] == "saved":
                    print(f"검증된 결과가 CSV에 저장됐습니다: {csv_path}")
                else:
                    print("이미 maintenance_schedule.csv 파일에 동일한 결과 행이 있어 새 행을 추가하지 않았습니다.")
            elif tool_name == "append_maintenance_csv":
                print("CSV 쓰기 툴의 검증을 통과하지 못해 저장하지 않았습니다.")

            # 툴 결과를 대화에 넣어야 모델이 성공이나 오류를 보고 다음 행동을 정할 수 있습니다.
            messages.append(
                {
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": result_text,
                }
            )
            if tool_name == "extract_pdf_text" and result_data.get("status") == "ok":
                pause_for_user(auto, "추출 결과를 모델에 전달합니다...")
            else:
                pause_for_user(auto, "툴 결과를 모델에 전달합니다...")

    print(f"[오류] 최대 에이전트 반복 횟수({MAX_AGENT_TURNS})를 초과했습니다.")
    return False


# STEP 5. 입력 폴더의 PDF마다 위 에이전트 반복문을 새로 실행합니다.
# [예제와 무관합니다, 무시해주세요] 인자 파싱과 실행 안내는 터미널 실습을 위한 보조 코드입니다.
def main(argv: list[str] | None = None) -> int:
    """Process every PDF in a directory through the local agent."""
    parser = argparse.ArgumentParser(
        description="비정형 발전기 정비 PDF를 로컬 에이전트로 읽어 CSV로 저장합니다."
    )
    parser.add_argument("input_dir", help="처리할 PDF가 들어 있는 폴더")
    parser.add_argument(
        "--auto",
        action="store_true",
        help="단계별 Enter 대기 없이 자동으로 실행",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Ollama 모델 이름 (기본값: {DEFAULT_MODEL})",
    )
    args = parser.parse_args(argv)

    input_dir = Path(args.input_dir).resolve()
    if not input_dir.is_dir():
        print(f"[오류] 입력 폴더를 찾을 수 없습니다: {input_dir}")
        return 2

    pdf_files = sorted(
        (path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: path.name.lower(),
    )
    if not pdf_files:
        print(f"[오류] 입력 폴더에 PDF 파일이 없습니다: {input_dir}")
        return 1

    csv_path = Path(__file__).resolve().parent / "output" / "maintenance_schedule.csv"
    print("=" * 64)
    print("Data Input Agent Demo")
    print("=" * 64)
    print(f"모델: {args.model}")
    print("\n[실습 안내 | 코드 STEP 5: 입력과 결과]")
    print(f"함께 볼 코드: {Path(__file__).resolve()}")
    print(f"입력 폴더: {input_dir}")
    print(f"처리할 PDF ({len(pdf_files)}개):")
    for number, pdf_path in enumerate(pdf_files, start=1):
        print(f"  {number}. {pdf_path.name}")
    print(f"결과 CSV: {csv_path}")
    print("agent.py 스크립트와 첫 번째 PDF를 참고하며 학습해보세요")
    print()
    print("지금은 학습을 위해 행동 사이를 Enter로 명시적으로 나누어 보여줍니다.")
    print("실제 사용 시 이러한 과정들은 자동으로 진행됩니다.")
    print("\n[프롬프트 정의 | 코드 STEP 1]")
    print("언어 모델에 아래와 같은 프롬프트가 주입됩니다.")
    print()
    print(SYSTEM_PROMPT.strip())
    if args.auto:
        print("--auto 모드에서는 같은 학습 설명을 표시하지만 Enter 대기는 생략합니다.")

    succeeded = 0
    try:
        pause_for_user(args.auto, "첫 번째 문서를 처리하는 예시로 진행합니다...")
        for number, pdf_path in enumerate(pdf_files, start=1):
            if number == 2:
                print("\n이번에는 완전히 동일한 에이전트가 전혀 다른 형식의 문서를 입력으로 받는 경우의 예제입니다.")
            try:
                document_succeeded = run_agent_for_pdf(
                    pdf_path=pdf_path,
                    input_dir=input_dir,
                    csv_path=csv_path,
                    model=args.model,
                    auto=args.auto,
                )
            except (ConnectError, ConnectionError):
                print(f"\n[문서 실패] {pdf_path.name}: Ollama에 연결할 수 없습니다.")
                continue
            except ResponseError as exc:
                print(f"\n[문서 실패] {pdf_path.name}: Ollama 요청 실패: {exc}")
                continue
            except Exception:
                print(f"\n[문서 실패] {pdf_path.name}: 예상하지 못한 오류가 발생했습니다.")
                continue

            if document_succeeded:
                succeeded += 1
    except KeyboardInterrupt:
        print("\n[중단] 사용자가 실행을 중단했습니다. 이미 저장된 행은 유지됩니다.")
        return 130

    failed = len(pdf_files) - succeeded
    print_learning_step(5, 5, "실행 결과")
    print(f"\n[완료] 성공 {succeeded}개, 실패 {failed}개")
    print(f"결과 파일: {csv_path}")
    if csv_path.exists():
        print("CSV를 열어 서로 다른 PDF가 같은 열 구조로 정리됐는지 확인해보세요.")
    else:
        print("저장된 CSV가 없습니다. 위의 문서별 결과와 Ollama 실행 상태를 확인해보세요.")
    if succeeded:
        print("\n[인사이트]")
        print("1. 에이전틱 워크플로우는 형식이 다른 문서에서도 모델의 해석을 활용해 같은 목표의 결과물을 만듭니다.")
        print("2. 모델에 제공하는 툴은 할 수 있는 작업을 열어주면서, 그 작업과 데이터에 대한 권한도 제한합니다.")
        print("3. 간단한 작업은 로컬 모델로 실행할 수 있습니다. 모델 호출 부분을 분리하면 OpenAI·Anthropic 등 다른 제공자로 바꾸기도 쉽습니다.")
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
