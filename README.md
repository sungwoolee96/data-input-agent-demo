# Data Input Agent Demo

서로 다른 형식의 발전기 정비 공지 PDF를 로컬 AI 에이전트가 읽고, 하나의 CSV 양식으로 정리하는 실행 예제입니다.

## 준비 사항

- Windows 10 이상, macOS 또는 Linux
- [Ollama](https://ollama.com/download)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- 로컬 모델을 위한 약 3GB 이상의 여유 저장공간

### Windows 설치

PowerShell에서 uv와 Ollama를 설치합니다.

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
irm https://ollama.com/install.ps1 | iex
```

설치 후 새 PowerShell 창에서 확인합니다.

```powershell
uv --version
ollama --version
```

## 내려받기

```powershell
git clone https://github.com/sungwoolee96/data-input-agent-demo.git
cd data-input-agent-demo
ollama pull qwen3:4b
uv sync
```

## 실행

### 단계별 모드

```powershell
uv run agent.py samples
```

각 모델 요청과 툴 실행 전후에 멈춥니다. 화면의 로그를 확인하고 Enter를 누르면 다음 단계로 이동합니다.

### 자동 모드

```powershell
uv run agent.py samples --auto
```

다른 Ollama 모델을 사용하려면 다음처럼 지정합니다.

```powershell
ollama pull qwen3:1.7b
uv run agent.py samples --auto --model qwen3:1.7b
```

## 결과 확인

결과는 다음 파일에 저장됩니다.

```text
output/maintenance_schedule.csv
```

예상 형식은 다음과 같습니다.

```csv
generator_name,maintenance_start,maintenance_end,source_pdf
한빛복합 2호기,2026-10-14 09:00,2026-10-16 18:00,maintenance_notice_a.pdf
제주내연 3호기,2026-11-02 08:30,2026-11-04 17:30,maintenance_notice_b.pdf
```

같은 PDF를 다시 처리해도 동일한 행은 중복 저장되지 않습니다.

## 테스트

```powershell
uv run pytest
uv run python -m compileall -q agent.py tests
```

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

이 예제는 텍스트를 선택할 수 있는 일반 PDF만 지원합니다. 스캔 이미지 PDF와 OCR은 지원하지 않습니다.

### 결과를 처음부터 다시 만들기

`output/maintenance_schedule.csv`만 삭제한 뒤 다시 실행합니다.

## 학습 문서

프로젝트의 맥락, 에이전트·로컬 모델·툴의 역할, 코드 흐름과 LangChain과의 관계는 [`LEARNING.md`](LEARNING.md)에서 설명합니다.

## 라이선스

MIT License
