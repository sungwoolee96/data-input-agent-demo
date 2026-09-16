"""Minimal local-model PDF data input agent."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader


DEFAULT_MODEL = "qwen3:4b"
DATETIME_FORMAT = "%Y-%m-%d %H:%M"
CSV_HEADERS = [
    "generator_name",
    "maintenance_start",
    "maintenance_end",
    "source_pdf",
]


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
