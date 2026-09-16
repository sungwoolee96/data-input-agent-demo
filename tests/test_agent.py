from pathlib import Path

from reportlab.pdfgen import canvas

from agent import extract_pdf_text


def make_text_pdf(path: Path, text: str = "Generator Alpha maintenance") -> None:
    pdf = canvas.Canvas(str(path))
    pdf.drawString(72, 760, text)
    pdf.save()


def test_extract_pdf_text_returns_page_markers_and_metadata(tmp_path: Path) -> None:
    pdf_path = tmp_path / "notice.pdf"
    make_text_pdf(pdf_path)

    result = extract_pdf_text(str(pdf_path), tmp_path)

    assert result["status"] == "ok"
    assert result["source_pdf"] == "notice.pdf"
    assert result["page_count"] == 1
    assert result["char_count"] > 20
    assert "--- PAGE 1 ---" in result["text"]
    assert "Generator Alpha maintenance" in result["text"]


def test_extract_pdf_text_rejects_non_pdf_files(tmp_path: Path) -> None:
    text_path = tmp_path / "notice.txt"
    text_path.write_text("not a PDF", encoding="utf-8")

    result = extract_pdf_text(str(text_path), tmp_path)

    assert result["status"] == "error"
    assert "PDF" in result["error"]


def test_extract_pdf_text_rejects_paths_outside_allowed_directory(tmp_path: Path) -> None:
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    outside_pdf = tmp_path / "outside.pdf"
    make_text_pdf(outside_pdf, "SECRET OUTSIDE CONTENT")

    result = extract_pdf_text(str(outside_pdf), allowed_dir)

    assert result["status"] == "error"
    assert "outside" not in result.get("text", "").lower()
    assert "SECRET OUTSIDE CONTENT" not in str(result)

