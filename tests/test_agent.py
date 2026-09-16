import csv
from pathlib import Path
from types import SimpleNamespace

import pytest
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


def test_append_maintenance_csv_rejects_second_record_for_same_source(tmp_path: Path) -> None:
    csv_path = tmp_path / "maintenance_schedule.csv"
    append_maintenance_csv(
        csv_path,
        {"notice.pdf"},
        "한빛복합 2호기",
        "2026-10-14 09:00",
        "2026-10-16 18:00",
        "notice.pdf",
    )

    result = append_maintenance_csv(
        csv_path,
        {"notice.pdf"},
        "다른 발전기",
        "2026-10-20 09:00",
        "2026-10-21 18:00",
        "notice.pdf",
    )

    assert result["status"] == "error"
    assert "이미" in result["error"]
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 1


def test_append_maintenance_csv_initializes_empty_file(tmp_path: Path) -> None:
    csv_path = tmp_path / "maintenance_schedule.csv"
    csv_path.touch()

    result = append_maintenance_csv(
        csv_path,
        {"notice.pdf"},
        "한빛복합 2호기",
        "2026-10-14 09:00",
        "2026-10-16 18:00",
        "notice.pdf",
    )

    assert result["status"] == "saved"
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 1


def test_append_maintenance_csv_rejects_non_string_model_values(tmp_path: Path) -> None:
    result = append_maintenance_csv(
        tmp_path / "maintenance_schedule.csv",
        {"notice.pdf"},
        None,  # type: ignore[arg-type]
        "2026-10-14 09:00",
        "2026-10-16 18:00",
        "notice.pdf",
    )

    assert result["status"] == "error"
    assert "문자열" in result["error"]


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


def test_teaching_log_explains_tool_flow_before_each_pause(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    pdf_path = tmp_path / "notice.pdf"
    make_text_pdf(pdf_path, "Hanbit unit two maintenance notice")
    csv_path = tmp_path / "maintenance.csv"
    fake_chat = FakeChat(
        [
            response_with(tool_call("extract_pdf_text", {"pdf_path": str(pdf_path)})),
            response_with(tool_call("append_maintenance_csv", {
                "generator_name": "한빛복합 2호기",
                "maintenance_start": "2026-10-14 09:00",
                "maintenance_end": "2026-10-16 18:00",
                "source_pdf": "notice.pdf",
            })),
            response_with(content="완료"),
        ]
    )
    snapshots = []

    def press_enter(prompt: str) -> str:
        snapshots.append((prompt, capsys.readouterr().out))
        return ""

    monkeypatch.setattr("builtins.input", press_enter)
    assert run_agent_for_pdf(pdf_path, tmp_path, csv_path, "test-model", False, fake_chat)

    first_prompt, first_log = snapshots[0]
    assert "STEP 4" in first_log
    assert "직접 읽을 수 없" in first_log
    assert "모델에 작업" in first_prompt
    read_prompt, read_log = next(
        (prompt, log) for prompt, log in snapshots if "이 툴을 실행" in prompt
    )
    assert "STEP 2" in read_log
    assert "extract_pdf_text" in read_log
    pdf_prompt, pdf_log = next(
        (prompt, log) for prompt, log in snapshots if "추출 결과" in prompt
    )
    assert "아직 CSV" in pdf_log
    assert "모델" in pdf_prompt
    write_prompt, write_log = next(
        (prompt, log) for prompt, log in snapshots if "CSV 쓰기 툴" in prompt
    )
    assert "STEP 4" in write_log
    assert "한빛복합 2호기" in write_log
    assert "2026-10-14 09:00" in write_log
    assert "아직 저장되지 않았" in write_log
    assert "append_maintenance_csv" in write_log
    saved_log = next(
        log for prompt, log in snapshots if "툴 결과를 모델" in prompt
    )
    assert "STEP 3" in saved_log
    assert "검증" in saved_log
    assert "CSV 저장 완료" in saved_log


def test_raw_pdf_preview_is_below_learning_flow(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    pdf_path = tmp_path / "notice.pdf"
    make_text_pdf(pdf_path, "Generator Alpha maintenance")
    fake_chat = FakeChat([
        response_with(tool_call("extract_pdf_text", {"pdf_path": str(pdf_path)})),
        response_with(content="확인 완료"),
    ])
    snapshots = []
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: snapshots.append((prompt, capsys.readouterr().out)) or "",
    )

    run_agent_for_pdf(pdf_path, tmp_path, tmp_path / "out.csv", "test-model", False, fake_chat)

    before_next_model = next(
        log for prompt, log in snapshots if "추출 결과" in prompt
    )
    assert "Generator Alpha maintenance" not in before_next_model
    final_output = capsys.readouterr().out
    assert final_output.index("[에이전트 최종 답변]") < final_output.index("[상세 로그")
    assert "Generator Alpha maintenance" in final_output


def test_auto_mode_prints_learning_log_without_enter(tmp_path: Path, capsys) -> None:
    pdf_path = tmp_path / "notice.pdf"
    make_text_pdf(pdf_path)
    fake_chat = FakeChat([response_with(content="확인할 정보가 없습니다.")])

    run_agent_for_pdf(pdf_path, tmp_path, tmp_path / "out.csv", "test-model", True, fake_chat)

    output = capsys.readouterr().out
    assert "STEP 4" in output
    assert "[Enter]" not in output
    assert "CSV 저장 완료" not in output


def test_run_agent_for_pdf_hides_reasoning_prefix_from_final_output(
    tmp_path: Path,
    capsys,
) -> None:
    pdf_path = tmp_path / "notice.pdf"
    make_text_pdf(pdf_path)
    fake_chat = FakeChat(
        [response_with(content="내부 분석은 표시하면 안 됩니다.</think>\n저장을 완료했습니다.")]
    )

    run_agent_for_pdf(
        pdf_path=pdf_path,
        input_dir=tmp_path,
        csv_path=tmp_path / "output.csv",
        model="test-model",
        auto=True,
        chat_fn=fake_chat,
    )

    output = capsys.readouterr().out
    assert "내부 분석" not in output
    assert "</think>" not in output
    assert "저장을 완료했습니다." in output


def test_run_agent_for_pdf_cannot_read_a_different_pdf(tmp_path: Path) -> None:
    current_pdf = tmp_path / "current.pdf"
    other_pdf = tmp_path / "other.pdf"
    make_text_pdf(current_pdf, "Current generator")
    make_text_pdf(other_pdf, "Other generator")
    csv_path = tmp_path / "output.csv"
    fake_chat = FakeChat(
        [
            response_with(tool_call("extract_pdf_text", {"pdf_path": str(other_pdf)})),
            response_with(
                tool_call(
                    "append_maintenance_csv",
                    {
                        "generator_name": "다른 발전기",
                        "maintenance_start": "2026-10-14 09:00",
                        "maintenance_end": "2026-10-16 18:00",
                        "source_pdf": "current.pdf",
                    },
                )
            ),
            response_with(content="완료"),
        ]
    )

    succeeded = run_agent_for_pdf(
        pdf_path=current_pdf,
        input_dir=tmp_path,
        csv_path=csv_path,
        model="test-model",
        auto=True,
        chat_fn=fake_chat,
    )

    assert succeeded is False
    assert not csv_path.exists()
    first_tool_result = fake_chat.calls[1]["messages"][-1]
    assert "현재 처리 중인 PDF" in first_tool_result["content"]


def test_run_agent_for_pdf_rejects_malformed_tool_arguments(
    tmp_path: Path,
    capsys,
) -> None:
    pdf_path = tmp_path / "notice.pdf"
    make_text_pdf(pdf_path)
    malformed_call = SimpleNamespace(
        function=SimpleNamespace(name="extract_pdf_text", arguments="not-a-mapping")
    )
    fake_chat = FakeChat(
        [
            response_with(malformed_call),
            response_with(content="잘못된 호출이라 저장하지 않았습니다."),
        ]
    )

    succeeded = run_agent_for_pdf(
        pdf_path=pdf_path,
        input_dir=tmp_path,
        csv_path=tmp_path / "output.csv",
        model="test-model",
        auto=True,
        chat_fn=fake_chat,
    )

    assert succeeded is False
    assert "툴 호출 형식" in capsys.readouterr().out


def test_main_rejects_missing_input_directory(tmp_path: Path, capsys) -> None:
    exit_code = main([str(tmp_path / "missing"), "--auto"])

    assert exit_code == 2
    assert "입력 폴더" in capsys.readouterr().out


def test_main_rejects_empty_input_directory(tmp_path: Path, capsys) -> None:
    exit_code = main([str(tmp_path), "--auto"])

    assert exit_code == 1
    assert "PDF" in capsys.readouterr().out
    assert not (tmp_path / "output" / "maintenance_schedule.csv").exists()


def test_main_introduces_files_and_learning_goal_before_processing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    make_text_pdf(tmp_path / "b.pdf")
    make_text_pdf(tmp_path / "a.pdf")
    observed = []

    def fake_run_agent_for_pdf(**kwargs) -> bool:
        observed.append((kwargs["pdf_path"].name, capsys.readouterr().out))
        return True

    monkeypatch.setattr("agent.run_agent_for_pdf", fake_run_agent_for_pdf)
    assert main([str(tmp_path), "--auto"]) == 0

    first_output = observed[0][1]
    assert "agent.py" in first_output
    assert str(tmp_path.resolve()) in first_output
    assert "1. a.pdf" in first_output
    assert "2. b.pdf" in first_output
    assert "maintenance_schedule.csv" in first_output
    assert "Enter" in first_output
    assert "자율적으로" in first_output
    assert "PDF를 열어" in first_output
    assert first_output.index("1. a.pdf") < first_output.index("2. b.pdf")


def test_main_handles_interrupt_at_new_intro_pause(tmp_path: Path, monkeypatch, capsys) -> None:
    make_text_pdf(tmp_path / "notice.pdf")

    def interrupt(_prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    assert main([str(tmp_path)]) == 130
    assert "[중단]" in capsys.readouterr().out


def test_main_continues_after_one_document_connection_failure(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    make_text_pdf(tmp_path / "a.pdf")
    make_text_pdf(tmp_path / "b.pdf")
    processed = []

    def fake_run_agent_for_pdf(**kwargs) -> bool:
        processed.append(kwargs["pdf_path"].name)
        if kwargs["pdf_path"].name == "a.pdf":
            raise ConnectionError("temporary failure")
        return True

    monkeypatch.setattr("agent.run_agent_for_pdf", fake_run_agent_for_pdf)

    exit_code = main([str(tmp_path), "--auto"])

    assert exit_code == 0
    assert processed == ["a.pdf", "b.pdf"]
    output = capsys.readouterr().out
    assert "a.pdf" in output
    assert "성공 1개, 실패 1개" in output


def test_main_continues_after_one_unexpected_document_failure(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    make_text_pdf(tmp_path / "a.pdf")
    make_text_pdf(tmp_path / "b.pdf")
    processed = []

    def fake_run_agent_for_pdf(**kwargs) -> bool:
        processed.append(kwargs["pdf_path"].name)
        if kwargs["pdf_path"].name == "a.pdf":
            raise ValueError("malformed model response")
        return True

    monkeypatch.setattr("agent.run_agent_for_pdf", fake_run_agent_for_pdf)

    exit_code = main([str(tmp_path), "--auto"])

    assert exit_code == 0
    assert processed == ["a.pdf", "b.pdf"]
    output = capsys.readouterr().out
    assert "예상하지 못한 오류" in output
    assert "성공 1개, 실패 1개" in output


@pytest.mark.parametrize(
    ("filename", "expected_fragments"),
    [
        (
            "maintenance_notice_a.pdf",
            ["한빛복합 2호기", "2026년 10월 14일 09시", "2026년 10월 16일 18시"],
        ),
        (
            "maintenance_notice_b.pdf",
            ["제주내연 3호기", "2026.11.02 08:30", "2026.11.04 17:30"],
        ),
    ],
)
def test_sample_pdf_contains_expected_extractable_facts(
    filename: str,
    expected_fragments: list[str],
) -> None:
    samples_dir = Path(__file__).resolve().parents[1] / "samples"

    result = extract_pdf_text(str(samples_dir / filename), samples_dir)

    assert result["status"] == "ok"
    assert result["page_count"] >= 1
    for fragment in expected_fragments:
        assert fragment in result["text"]
