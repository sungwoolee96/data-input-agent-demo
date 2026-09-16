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
    if not auto:
        input(f"\n[Enter] {message}")


# STEP 2. 에이전트가 사용할 PDF 읽기 툴의 실제 동작입니다.
def extract_pdf_text(pdf_path: str, allowed_input_dir: Path) -> dict[str, object]:
    """Extract page-marked text from one PDF inside the allowed directory."""
    allowed_dir = allowed_input_dir.resolve()
    candidate = Path(pdf_path).resolve()

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


# STEP 3. 에이전트가 사용할 CSV 쓰기 툴의 실제 동작입니다.
def append_maintenance_csv(
    csv_path: Path,
    allowed_sources: set[str],
    generator_name: str,
    maintenance_start: str,
    maintenance_end: str,
    source_pdf: str,
) -> dict[str, str]:
    """Validate and append one maintenance record to a UTF-8 BOM CSV."""
    generator_name = generator_name.strip()
    maintenance_start = maintenance_start.strip()
    maintenance_end = maintenance_end.strip()
    source_pdf = source_pdf.strip()

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

    if csv_path.exists():
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames != CSV_HEADERS:
                    return {"status": "error", "error": "기존 CSV의 컬럼 형식이 예상과 다릅니다."}
                if row in reader:
                    return {"status": "duplicate", "source_pdf": source_pdf}
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
    try:
        result = json.loads(result_text)
    except json.JSONDecodeError:
        print(f"[툴 결과] {result_text}")
        return

    if tool_name == "extract_pdf_text" and result.get("status") == "ok":
        print("[툴 결과] PDF 텍스트 추출 완료")
        print(f"  페이지 수: {result['page_count']}")
        print(f"  추출 문자 수: {result['char_count']}")
        preview = str(result["text"])
        if len(preview) > 600:
            preview = preview[:600].rstrip() + "\n... (미리보기 생략)"
        print("\n--- 추출 내용 미리보기 ---")
        print(preview)
        print("--------------------------")
        return

    if result.get("status") in {"saved", "duplicate"}:
        label = "CSV 저장 완료" if result["status"] == "saved" else "이미 저장된 기록"
        print(f"[툴 결과] {label}: {result.get('source_pdf', '')}")
        return

    print(f"[툴 결과] {result.get('error', result_text)}")


# STEP 4. 모델이 툴을 고르고 결과를 다시 관찰하는 에이전트 반복문입니다.
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
    state = {"pdf_extracted": False}
    saved = False

    def extract_pdf_text_tool(pdf_path: str) -> str:
        """Extract text from a PDF in the selected input folder.

        Args:
            pdf_path: The PDF path supplied in the current task.

        Returns:
            JSON containing page-marked text and extraction metadata.
        """
        result = extract_pdf_text(pdf_path, input_dir)
        state["pdf_extracted"] = result.get("status") == "ok"
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
        if not state["pdf_extracted"]:
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
    available_tools = {
        "extract_pdf_text": extract_pdf_text_tool,
        "append_maintenance_csv": append_maintenance_csv_tool,
    }
    tool_list = list(available_tools.values())
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
    pause_for_user(auto, "로컬 모델에 작업을 전달합니다...")

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

        if not tool_calls:
            final_text = (message.content or "").strip()
            print(f"\n[에이전트 최종 답변] {final_text or '응답 없음'}")
            return saved

        for tool_call in tool_calls:
            tool_name = tool_call.function.name
            arguments = dict(tool_call.function.arguments or {})
            print(f"\n[툴 호출] {tool_name}")
            print(json.dumps(arguments, ensure_ascii=False, indent=2))
            pause_for_user(auto, "이 툴을 실행합니다...")

            function = available_tools.get(tool_name)
            if function is None:
                result_text = json.dumps(
                    {"status": "error", "error": f"허용되지 않은 툴: {tool_name}"},
                    ensure_ascii=False,
                )
            else:
                try:
                    result_text = function(**arguments)
                except TypeError as exc:
                    result_text = json.dumps(
                        {"status": "error", "error": f"툴 인자 오류: {exc}"},
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

            messages.append(
                {
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": result_text,
                }
            )
            pause_for_user(auto, "툴 결과를 모델에 전달합니다...")

    print(f"[오류] 최대 에이전트 반복 횟수({MAX_AGENT_TURNS})를 초과했습니다.")
    return False


# STEP 5. 폴더의 PDF를 모아 하나씩 에이전트에게 맡깁니다.
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
    print("Data Input Agent Demo")
    print(f"모델: {args.model}")
    print(f"입력 PDF: {len(pdf_files)}개")
    print(f"출력 CSV: {csv_path}")

    succeeded = 0
    try:
        for pdf_path in pdf_files:
            if run_agent_for_pdf(
                pdf_path=pdf_path,
                input_dir=input_dir,
                csv_path=csv_path,
                model=args.model,
                auto=args.auto,
            ):
                succeeded += 1
    except (ConnectError, ConnectionError):
        print("\n[오류] Ollama에 연결할 수 없습니다. Ollama를 설치하고 실행해 주세요.")
        print("설치 안내: https://ollama.com/download")
        return 2
    except ResponseError as exc:
        print(f"\n[오류] Ollama 요청 실패: {exc}")
        print(f"모델이 없다면 실행하세요: ollama pull {args.model}")
        return 2
    except KeyboardInterrupt:
        print("\n[중단] 사용자가 실행을 중단했습니다. 이미 저장된 행은 유지됩니다.")
        return 130

    failed = len(pdf_files) - succeeded
    print(f"\n[완료] 성공 {succeeded}개, 실패 {failed}개")
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
