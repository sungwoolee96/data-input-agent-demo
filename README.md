# Data Input Agent Demo

서로 다른 형식의 발전기 정비 공지 PDF를 로컬 AI 에이전트가 읽고, 아래처럼 동일한 CSV 양식으로 정리하는 최소 예제입니다.

```csv
generator_name,maintenance_start,maintenance_end,source_pdf
한빛복합 2호기,2026-10-14 09:00,2026-10-16 18:00,maintenance_notice_a.pdf
제주내연 3호기,2026-11-02 08:30,2026-11-04 17:30,maintenance_notice_b.pdf
```

핵심 실행 코드는 [`agent.py`](agent.py) 하나입니다. LangChain 같은 에이전트 프레임워크를 사용하지 않고, 모델 호출부터 툴 실행과 결과 전달까지의 반복문을 직접 보여줍니다.

## 이 예제에서 배우는 것

- 비정형 문서의 표현이 달라도 같은 의미를 정형 데이터로 바꾸는 방법
- 로컬 모델, 모델 실행기, 에이전트, 툴의 역할 차이
- 모델이 Python 함수를 선택하고 호출하는 과정
- 모델에게 파일을 직접 수정시키지 않고 허용된 툴만 제공하는 방법
- 쓰기 전에 날짜와 출처를 검증하는 방법
- 에이전트의 내부 추론 대신 관찰 가능한 행동을 로그로 보여주는 방법

## 전체 흐름

```text
사용자 실행
  -> 에이전트가 PDF 읽기 툴 선택
  -> extract_pdf_text가 텍스트 반환
  -> 로컬 모델이 발전기와 정비기간 해석
  -> 에이전트가 CSV 쓰기 툴 선택
  -> append_maintenance_csv가 검증 후 한 행 추가
  -> 에이전트가 완료 결과 설명
```

이 프로젝트에서 모델은 파일을 직접 읽거나 쓰지 않습니다. 모델이 사용할 수 있는 기능은 다음 두 개뿐입니다.

| 툴 | 책임 |
| --- | --- |
| `extract_pdf_text` | 허용된 폴더 안의 일반 PDF에서 텍스트 추출 |
| `append_maintenance_csv` | 네 필드를 검증하고 CSV에 한 행 추가 |

## 로컬 모델 구성

- **Ollama**: 로컬 컴퓨터에서 모델을 실행하고 Python 요청을 받습니다.
- **Qwen3 4B**: PDF 텍스트를 이해하고 어떤 툴을 사용할지 결정합니다.
- **`agent.py`**: 모델, 대화 기록, 툴 실행 결과를 연결합니다.
- **툴 함수**: PDF 읽기와 CSV 쓰기처럼 실제 컴퓨터 작업을 수행합니다.

기본 모델은 [`qwen3:4b`](https://ollama.com/library/qwen3:4b)입니다. 모델 파일은 약 2.5GB이며 최초 한 번 내려받습니다. 이 저장소에는 원격 LLM API 호출 코드나 API 키가 없습니다.

## 준비 사항

- Windows 10 이상, macOS 또는 Linux
- [Ollama](https://ollama.com/download)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- 로컬 모델을 위한 여유 저장공간과 메모리

### Windows 설치 예시

PowerShell에서 uv를 설치합니다.

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Ollama는 [Windows 다운로드 페이지](https://ollama.com/download/windows)에서 설치하거나 공식 PowerShell 명령을 사용할 수 있습니다.

```powershell
irm https://ollama.com/install.ps1 | iex
```

설치 후 새 PowerShell 창을 열고 버전을 확인합니다.

```powershell
uv --version
ollama --version
```

## 내려받기와 실행

```powershell
git clone https://github.com/sungwoolee96/data-input-agent-demo.git
cd data-input-agent-demo
ollama pull qwen3:4b
uv sync
```

### 1. 단계별 학습 모드

```powershell
uv run agent.py samples
```

PDF 발견, 모델 요청, 툴 선택, 툴 결과, CSV 저장 앞뒤에서 멈춥니다. 화면을 읽은 후 Enter를 누르면 다음 단계로 이동합니다.

```text
[문서] maintenance_notice_a.pdf

[Enter] 로컬 모델에 작업을 전달합니다...

[툴 호출] extract_pdf_text
{
  "pdf_path": ".../maintenance_notice_a.pdf"
}

[Enter] 이 툴을 실행합니다...
```

표시되는 내용은 모델의 숨겨진 사고과정이 아닙니다. 모델 요청, 툴 이름과 인자, 툴 결과처럼 프로그램 밖에서 관찰할 수 있는 정보만 보여줍니다.

### 2. 자동 모드

```powershell
uv run agent.py samples --auto
```

같은 과정을 Enter 대기 없이 실행합니다.

다른 호환 모델을 실험하려면 다음처럼 지정할 수 있습니다.

```powershell
uv run agent.py samples --auto --model qwen3:1.7b
```

작은 모델은 메모리 사용량이 줄지만, 한국어 정보 추출이나 툴 선택의 안정성이 낮아질 수 있습니다.

## 결과 확인

실행 결과는 다음 파일에 추가됩니다.

```text
output/maintenance_schedule.csv
```

CSV 컬럼은 고정되어 있습니다.

| 컬럼 | 의미 |
| --- | --- |
| `generator_name` | 정비 대상 발전기 |
| `maintenance_start` | 정비 시작, `YYYY-MM-DD HH:MM` |
| `maintenance_end` | 정비 종료, `YYYY-MM-DD HH:MM` |
| `source_pdf` | 값을 가져온 PDF 파일명 |

같은 결과를 다시 처리하면 중복 행을 추가하지 않습니다. CSV는 Windows 스프레드시트 프로그램에서 한글이 잘 보이도록 UTF-8 BOM으로 저장됩니다.

## 샘플 PDF

두 파일은 실제 운영자료가 아닌 교육용 합성 문서입니다.

- `maintenance_notice_a.pdf`: 문서번호, 수신처, 협조 요청이 포함된 공문 형식
- `maintenance_notice_b.pdf`: 정보가 여러 카드와 표에 나뉜 현장 브리핑 형식

두 문서는 레이아웃과 문체가 다르고 관련 없는 날짜와 설명도 포함합니다. 그러나 사람이 읽으면 정비 대상과 기간을 명확히 판단할 수 있도록 만들었습니다.

## `agent.py` 읽는 순서

파일 안의 `STEP` 주석을 따라가면 됩니다.

1. 모델, CSV 양식, 시스템 프롬프트
2. PDF 읽기 툴
3. CSV 쓰기 툴
4. 모델과 툴을 연결하는 에이전트 반복문
5. 폴더의 PDF를 순서대로 처리하는 CLI

에이전트 반복문의 핵심은 다음 구조입니다.

```text
while 최대 반복 횟수 이내:
    모델 호출
    if 툴 호출이 없으면 종료
    선택된 툴을 허용 목록에서 찾기
    툴 실행
    결과를 대화 기록에 추가
```

## 왜 LangChain이 아닌가

이 프로젝트는 LangChain 라이브러리를 사용하지 않습니다. 다만 LangChain 에이전트가 제공하는 기본 패턴을 직접 구현합니다.

| 이 프로젝트에서 직접 구현 | LangChain이 일반적으로 제공 |
| --- | --- |
| 모델 호출과 메시지 기록 | 표준 모델 인터페이스 |
| 툴 호출 확인과 실행 | 툴 실행 노드 |
| 툴 결과를 모델에 다시 전달 | 에이전트 상태 관리 |
| 반복 횟수와 종료 판단 | 에이전트 런타임 |

먼저 이 코드를 읽으면 프레임워크가 대신 처리하는 일이 무엇인지 이해하기 쉽습니다. 그다음 동일한 툴을 LangChain의 `create_agent()`에 연결해 비교해 볼 수 있습니다.

## 검증

```powershell
uv run pytest
uv run python -m compileall -q agent.py tests
```

테스트는 PDF 경로 제한, 텍스트 추출, 날짜 검증, 종료-시작 순서, 출처 검증, 중복 방지, Enter 대기와 에이전트 툴 호출 순서를 확인합니다.

## 제한 사항

- 텍스트를 선택할 수 있는 일반 PDF만 처리합니다.
- 스캔 이미지 PDF와 OCR은 지원하지 않습니다.
- PDF 하나에서 정비 일정 하나만 추출합니다.
- 작은 로컬 모델의 응답은 실행마다 조금 달라질 수 있습니다.
- 교육용 예제이며 실제 발전소 운영 판단이나 자동 등록에 사용하면 안 됩니다.

## 실습 아이디어

1. 샘플 PDF의 문장 순서를 바꾸고 결과를 비교합니다.
2. 정비 종료일을 모호하게 만든 뒤 에이전트가 저장을 거부하는지 확인합니다.
3. `qwen3:1.7b`와 `qwen3:4b`의 결과를 비교합니다.
4. CSV에 `maintenance_type` 컬럼을 추가합니다.
5. 같은 툴을 LangChain 에이전트에 연결해 코드 차이를 비교합니다.

## 문제 해결

### Ollama에 연결할 수 없음

Ollama 앱이 실행 중인지 확인합니다. Linux에서는 필요할 경우 다음 명령으로 서버를 시작합니다.

```bash
ollama serve
```

### 모델을 찾을 수 없음

```powershell
ollama pull qwen3:4b
```

### PDF에서 텍스트를 추출할 수 없음

해당 PDF가 스캔 이미지일 가능성이 있습니다. 이 예제는 OCR을 지원하지 않습니다.

### 결과를 처음부터 다시 만들기

`output/maintenance_schedule.csv`를 삭제한 뒤 다시 실행합니다. 샘플 PDF와 코드는 삭제하지 않습니다.

## 라이선스

MIT License

