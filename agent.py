"""Minimal local-model PDF data input agent."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from httpx import ConnectError
from ollama import ResponseError, chat
from pypdf import PdfReader


# STEP 1. 모델, 출력 양식, 에이전트 규칙을 정의합니다.
# 이 값들을 파일 위쪽에 모아 두면 학습자가 에이전트의 입력 계약을 먼저 확인하고,
# 모델이나 출력 양식을 바꿀 때 실행 로직 전체를 찾지 않아도 됩니다.
DEFAULT_MODEL = "qwen3:4b"
DATETIME_FORMAT = "%Y-%m-%d %H:%M"
CSV_HEADERS = [
    "generator_name",
    "maintenance_start",
    "maintenance_end",
    "source_pdf",
]
MAX_AGENT_TURNS = 5
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
    # 기본 모드는 발표자가 로그를 설명할 시간을 주고, --auto일 때만 멈춤을 건너뜁니다.
    if not auto:
        input(f"\n[Enter] {message}")


def print_learning_step(number: int, code_step: int, title: str) -> None:
    """Connect an observable demo action to the numbered code block."""
    # 숫자는 프로그램의 내부 판단이 아니라 학습자가 따라 읽을 순서를 뜻합니다.
    print(f"\n[학습 {number}/5 | 코드 STEP {code_step}: {title}]")


# STEP 2. 에이전트가 사용할 PDF 읽기 툴의 실제 동작입니다.
# 모델에 범용 파일 읽기 권한을 주지 않고, 지정 폴더의 PDF만 읽는 좁은 도구를 줍니다.
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

    # pypdf는 페이지의 텍스트 레이어를 읽습니다. 이미지뿐인 스캔 PDF는 OCR이 필요합니다.
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


# STEP 3. 에이전트가 사용할 CSV 쓰기 툴의 실제 동작입니다.
# 언어 모델은 의미를 해석하지만, 날짜 형식과 쓰기 조건은 결정적인 코드로 다시 검증합니다.
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

    # 비어 있는 값, 잘못된 출처, 날짜 순서를 차례로 검사해 오염된 행의 저장을 막습니다.
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

    # 기존 파일의 헤더가 예상과 다르면 덧붙이지 않습니다. 같은 행은 중복 저장하지 않습니다.
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

    # utf-8-sig는 Windows의 스프레드시트 프로그램에서도 한글을 쉽게 열 수 있게 합니다.
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
    # 진행 중에는 도구 실행의 핵심 결과만 보여주고 원문 미리보기는 문서 끝으로 보냅니다.
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
    # 일부 로컬 모델은 think=False여도 내부 분석 뒤에 </think> 구분자를 남깁니다.
    # 데모 화면에는 그 앞부분을 노출하지 않고, 구분자 뒤의 최종 답변만 표시합니다.
    if "</think>" in content:
        content = content.rsplit("</think>", maxsplit=1)[1]
    return content.strip()


def print_document_details(details: list[str]) -> None:
    """Show bounded tool details after the learning narrative ends."""
    if details:
        print("\n[상세 로그 | 도구 인자와 PDF 원문 미리보기]")
        print("\n\n".join(details))


# STEP 4. 모델이 툴을 고르고 결과를 다시 관찰하는 에이전트 반복문입니다.
# 이 함수가 예제의 핵심입니다. 모델은 다음 행동을 고르고, Python은 허용된 행동만 실행합니다.
def run_agent_for_pdf(
    pdf_path: Path,
    input_dir: Path,
    csv_path: Path,
    model: str,
    auto: bool,
    chat_fn: Callable[..., object] | None = None,
) -> bool:
    """Run a bounded model-tool loop for one PDF and report write success."""
    # 테스트에서는 가짜 chat 함수를 주입하고, 실제 실행에서는 로컬 Ollama를 사용합니다.
    call_model = chat_fn or chat
    current_pdf = pdf_path.resolve()
    state: dict[str, str | None] = {"extracted_source": None}
    saved = False
    details: list[str] = []

    # Ollama가 함수 설명과 인자 형식을 읽을 수 있도록, 실제 툴을 내부 함수로 감쌉니다.
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
        # 모델이 순서를 건너뛰더라도 PDF 원문을 읽기 전에는 CSV를 쓸 수 없습니다.
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
    # 대화 기록에는 목표뿐 아니라 매 툴 결과도 누적됩니다. 모델은 다음 턴에 그 결과를 관찰합니다.
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
    print("모델은 PDF를 직접 읽을 수 없으므로 PDF 읽기 툴을 선택해야 합니다.")
    pause_for_user(auto, "로컬 모델에 작업을 전달합니다...")

    # 무한 반복을 막기 위해 턴 수를 제한한 작은 에이전트 루프입니다.
    for _turn in range(MAX_AGENT_TURNS):
        response = call_model(
            model=model,
            messages=messages,
            tools=tool_list,
            think=False,
            options={"temperature": 0},
        )
        message = response.message
        messages.append(message)
        tool_calls = message.tool_calls or []

        # 툴 호출이 없는 응답은 모델이 작업을 끝냈다는 신호로 사용합니다.
        if not tool_calls:
            final_text = _visible_model_content(message.content or "")
            print(f"\n[에이전트 최종 답변] {final_text or '응답 없음'}")
            print_document_details(details)
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
                print("이 값은 아직 저장되지 않았습니다. CSV 쓰기 툴이 검증한 뒤 저장합니다.")
            print(f"\n[툴 호출] {tool_name}")
            details.append(f"{tool_name} 인자:\n{json.dumps(arguments, ensure_ascii=False, indent=2)}")
            if tool_name == "append_maintenance_csv" and valid_call:
                pause_for_user(auto, "append_maintenance_csv CSV 쓰기 툴을 실행합니다...")
            else:
                pause_for_user(auto, "이 툴을 실행합니다...")

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
                if len(preview) > 600:
                    preview = preview[:600].rstrip() + "\n... (미리보기 생략)"
                details.append(f"PDF 텍스트 미리보기:\n{preview}")
                print("\n에이전트가 호출한 툴을 통해 PDF 내용이 텍스트로 추출됐습니다.")
                print("이제 로컬 언어 모델은 이 내용에서 발전기와 정비 기간을 해석합니다.")
                print("아직 CSV에는 아무것도 저장되지 않았습니다.")
            elif tool_name == "append_maintenance_csv" and result_data.get("status") in {
                "saved", "duplicate"
            }:
                print_learning_step(4, 3, "CSV 쓰기 툴")
                print("CSV 쓰기 툴이 필수 값, 날짜 형식과 순서, 출처, 기존 기록을 검증했습니다.")
                if result_data["status"] == "saved":
                    print(f"검증된 결과가 CSV에 저장됐습니다: {csv_path}")
                else:
                    print("이미 동일한 결과가 있어 새 행을 추가하지 않았습니다.")
            elif tool_name == "append_maintenance_csv":
                print("CSV 쓰기 툴의 검증을 통과하지 못해 저장하지 않았습니다.")

            # 실행 결과를 tool 메시지로 넣어야 모델이 성공/오류를 보고 다음 행동을 정할 수 있습니다.
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
    print_document_details(details)
    return False


# STEP 5. 폴더의 PDF를 모아 하나씩 에이전트에게 맡깁니다.
# 명령행 옵션을 읽고 입력을 검증하는 바깥쪽 흐름이며, PDF마다 대화 상태를 새로 시작합니다.
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

    # 실행 순서를 예측할 수 있도록 파일명을 기준으로 정렬합니다.
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
    print("먼저 agent.py 스크립트와 첫 번째 PDF를 열어 확인해보세요.")
    print("지금은 학습을 위해 행동 사이를 Enter로 명시적으로 나누어 보여줍니다.")
    print("잘 설계된 에이전틱 시스템의 목표는 이러한 판단과 툴 사용을 자율적으로 이어가는 것입니다.")
    if args.auto:
        print("--auto 모드에서는 같은 학습 설명을 표시하지만 Enter 대기는 생략합니다.")

    # 한 PDF의 실패가 이미 저장된 다른 결과를 지우지 않도록 문서별 성공 수를 집계합니다.
    succeeded = 0
    try:
        pause_for_user(args.auto, "준비되었다면 첫 번째 문서를 처리합니다...")
        for pdf_path in pdf_files:
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
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
