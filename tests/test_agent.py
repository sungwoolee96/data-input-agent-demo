import csv
from pathlib import Path
from types import SimpleNamespace

from reportlab.pdfgen import canvas

from agent import (
    append_maintenance_csv,
    extract_pdf_text,
    main,
    pause_for_user,
    run_agent_for_pdf,
)


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


def test_append_maintenance_csv_writes_valid_utf8_bom_row(tmp_path: Path) -> None:
    csv_path = tmp_path / "output" / "maintenance_schedule.csv"

    result = append_maintenance_csv(
        csv_path,
        {"notice.pdf"},
        "한빛복합 2호기",
        "2026-10-14 09:00",
        "2026-10-16 18:00",
        "notice.pdf",
    )

    assert result == {"status": "saved", "source_pdf": "notice.pdf"}
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf")
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [
        {
            "generator_name": "한빛복합 2호기",
            "maintenance_start": "2026-10-14 09:00",
            "maintenance_end": "2026-10-16 18:00",
            "source_pdf": "notice.pdf",
        }
    ]


def test_append_maintenance_csv_rejects_missing_generator(tmp_path: Path) -> None:
    csv_path = tmp_path / "maintenance_schedule.csv"

    result = append_maintenance_csv(
        csv_path,
        {"notice.pdf"},
        "   ",
        "2026-10-14 09:00",
        "2026-10-16 18:00",
        "notice.pdf",
    )

    assert result["status"] == "error"
    assert "발전기" in result["error"]
    assert not csv_path.exists()


def test_append_maintenance_csv_rejects_invalid_timestamp(tmp_path: Path) -> None:
    result = append_maintenance_csv(
        tmp_path / "maintenance_schedule.csv",
        {"notice.pdf"},
        "한빛복합 2호기",
        "2026/10/14",
        "2026-10-16 18:00",
        "notice.pdf",
    )

    assert result["status"] == "error"
    assert "YYYY-MM-DD HH:MM" in result["error"]


def test_append_maintenance_csv_rejects_end_before_start(tmp_path: Path) -> None:
    result = append_maintenance_csv(
        tmp_path / "maintenance_schedule.csv",
        {"notice.pdf"},
        "한빛복합 2호기",
        "2026-10-16 18:00",
        "2026-10-14 09:00",
        "notice.pdf",
    )

    assert result["status"] == "error"
    assert "종료" in result["error"]


def test_append_maintenance_csv_rejects_unknown_source(tmp_path: Path) -> None:
    result = append_maintenance_csv(
        tmp_path / "maintenance_schedule.csv",
        {"notice.pdf"},
        "한빛복합 2호기",
        "2026-10-14 09:00",
        "2026-10-16 18:00",
        "other.pdf",
    )

    assert result["status"] == "error"
    assert "입력 PDF" in result["error"]


def test_append_maintenance_csv_skips_exact_duplicate(tmp_path: Path) -> None:
    csv_path = tmp_path / "maintenance_schedule.csv"
    arguments = (
        csv_path,
        {"notice.pdf"},
        "한빛복합 2호기",
        "2026-10-14 09:00",
        "2026-10-16 18:00",
        "notice.pdf",
    )

    first = append_maintenance_csv(*arguments)
    second = append_maintenance_csv(*arguments)

    assert first["status"] == "saved"
    assert second == {"status": "duplicate", "source_pdf": "notice.pdf"}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 1


def test_pause_for_user_waits_in_teaching_mode(monkeypatch) -> None:
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "")

    pause_for_user(auto=False, message="다음 단계")

    assert prompts == ["\n[Enter] 다음 단계"]


def test_pause_for_user_does_not_wait_in_auto_mode(monkeypatch) -> None:
    def fail_if_called(_prompt: str) -> str:
        raise AssertionError("automatic mode must not call input")

    monkeypatch.setattr("builtins.input", fail_if_called)

    pause_for_user(auto=True, message="다음 단계")


def tool_call(name: str, arguments: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def response_with(*calls: SimpleNamespace, content: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=list(calls)),
    )


class FakeChat:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> SimpleNamespace:
        captured = dict(kwargs)
        captured["messages"] = list(kwargs["messages"])
        self.calls.append(captured)
        return self.responses.pop(0)


def test_run_agent_for_pdf_executes_real_tools_in_sequence(tmp_path: Path) -> None:
    pdf_path = tmp_path / "notice.pdf"
    csv_path = tmp_path / "output" / "maintenance_schedule.csv"
    make_text_pdf(pdf_path, "Hanbit unit two maintenance notice")
    fake_chat = FakeChat(
        [
            response_with(tool_call("extract_pdf_text", {"pdf_path": str(pdf_path)})),
            response_with(
                tool_call(
                    "append_maintenance_csv",
                    {
                        "generator_name": "한빛복합 2호기",
                        "maintenance_start": "2026-10-14 09:00",
                        "maintenance_end": "2026-10-16 18:00",
                        "source_pdf": "notice.pdf",
                    },
                )
            ),
            response_with(content="CSV 저장을 완료했습니다."),
        ]
    )

    succeeded = run_agent_for_pdf(
        pdf_path=pdf_path,
        input_dir=tmp_path,
        csv_path=csv_path,
        model="test-model",
        auto=True,
        chat_fn=fake_chat,
    )

    assert succeeded is True
    assert len(fake_chat.calls) == 3
    tool_messages = [
        message
        for message in fake_chat.calls[-1]["messages"]
        if isinstance(message, dict) and message.get("role") == "tool"
    ]
    assert [message["tool_name"] for message in tool_messages] == [
        "extract_pdf_text",
        "append_maintenance_csv",
    ]
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["generator_name"] == "한빛복합 2호기"
    assert rows[0]["maintenance_start"] == "2026-10-14 09:00"
    assert rows[0]["maintenance_end"] == "2026-10-16 18:00"


def test_main_rejects_missing_input_directory(tmp_path: Path, capsys) -> None:
    exit_code = main([str(tmp_path / "missing"), "--auto"])

    assert exit_code == 2
    assert "입력 폴더" in capsys.readouterr().out


def test_main_rejects_empty_input_directory(tmp_path: Path, capsys) -> None:
    exit_code = main([str(tmp_path), "--auto"])

    assert exit_code == 1
    assert "PDF" in capsys.readouterr().out
    assert not (tmp_path / "output" / "maintenance_schedule.csv").exists()
