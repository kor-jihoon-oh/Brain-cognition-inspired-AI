# Mirror: 거울·빛 벤치마크

10×10 격자에서 거울과 스플리터를 배치해 두 센서에 목표 색의 빛을 보내는 벤치마크입니다. 한 문제는 5라운드이며, 라운드마다 광원·필터·센서의 위치, 규칙, 목표 색이 바뀝니다. 모델은 매 시도 제공되는 배치와 센서별·전체 정오답 피드백을 이용합니다.

이 공개본은 **100문제 실험을 완료한 10×10 버전**입니다. 포함된 GPT-6.1 Sol 결과의 **MPE는 38.80%**입니다. 데이터, 영어 프롬프트, 채점 기준 및 원본 응답을 함께 제공합니다.

## 구성

```text
mirror/
├── code/                  # Python 실행·시뮬레이션·채점·데이터 생성
│   └── dataset_generation/
├── dataset/
│   ├── private/           # 평가용 규칙·목표·정답 경로
│   ├── public_boards/     # 모델 입력에 사용하는 배치 정보
│   ├── prompt_en.txt      # 실제 영어 프롬프트
│   └── DATASET-OVERVIEW.html
├── result/
│   └── 2026-10-28_16-50-18.540_KST/  # 완료된 100문제 결과
├── tests/                 # 네트워크를 사용하지 않는 테스트
└── docs/                  # 방법, 결과, 공개 전 검증 기록
```

`private`는 모델에게 보내지 않는 평가자 정보를 뜻합니다. 접근 권한이나 암호화를 뜻하지는 않습니다. 데이터와 정답은 이 저장소에서 연구·검증용으로 공개합니다.

## 실행 환경

- Python **3.10 이상**. Python 표준 라이브러리만 사용하므로 별도 `pip install`은 필요하지 않습니다.
- Windows, macOS, Linux에서 Python으로 실행할 수 있습니다. 이번 공개 정리의 실행 검증 환경은 Windows입니다.
- 유료 실험에는 OpenAI API 키와 `gpt-6.1-sol` 사용 권한이 필요합니다.
- 모든 명령은 **이 README가 있는 `mirror` 폴더**에서 실행합니다. macOS/Linux에서 명령 이름이 `python3`이면 `python` 대신 사용하세요.

```powershell
cd <내려받은-팀-저장소의-mirror-경로>
python --version
```

## API 없이 먼저 확인하기

```powershell
# 데이터와 실행 설정 점검
python code/start_gpt_with_key.py --dry-run

# 1번 문제의 실제 첫 모델 입력 확인
python code/start_gpt_with_key.py --preview 1

# 데이터 100문제와 정답 경로 500개 검증
python code/generate_dataset.py --verify-only

# 포함된 결과의 지표를 다시 계산
python code/summarize_results.py --run-folder result/2026-10-28_16-50-18.540_KST

# 채점·프로토콜·인증 입력 테스트
python -m unittest discover -s tests -v
```

이 명령들은 API 키 없이 사용할 수 있으며 API를 호출하지 않습니다. 배치 그림은 [DATASET-OVERVIEW.html](dataset/DATASET-OVERVIEW.html)을 로컬 브라우저에서 열어 확인할 수 있습니다.

## 새 실험 실행 — 유료 API 사용

```powershell
python code/start_gpt_with_key.py
```

환경변수 `OPENAI_API_KEY`가 설정되어 있으면 사용합니다. 없으면 화면에 표시되지 않는 숨김 입력으로 키를 받습니다. 키는 실행 중 메모리에서만 사용하며 코드·결과 파일에 저장하지 않습니다. 키를 명령행 인수나 저장소 파일에 적지 마세요. 환경변수 설정 방식은 [OpenAI 공식 안내](https://developers.openai.com/api/docs/quickstart)를 참고하세요.

Windows에서 클립보드에 복사한 키를 사용하려면:

```powershell
python code/start_gpt_with_key.py --clipboard
```

이 명령은 **1번부터 새 100문제 실험을 시작**합니다. 결과는 `result/날짜_시간_KST/`에 생성됩니다. 포함된 과거 실험 결과를 덮어쓰지 않습니다.

고정 실험 설정:

| 설정 | 값 |
|---|---|
| 모델 | `gpt-6.1-sol` |
| 추론 설정 | `medium`, `all_turns` |
| 문제·라운드 | 100문제 × 5라운드 |
| 시도 제한 T | 라운드당 20회 |
| 통과 조건 k | 연속 정답 2회 |
| 입력 언어 | 영어 |
| 배치 제공 | 매 시도 현재 전체 배치 |
| 기록 유지 | 문제 안에서 대화·응답 항목 유지, 문제 사이 초기화 |

요청 재시도·로컬 시간 제한·최대 출력 토큰 제한은 별도로 추가하지 않았습니다. 중단되면 저장된 결과를 이용해 재개합니다.

## 중단 후 이어서 실행

```powershell
python code/start_gpt_with_key.py --resume latest
```

`latest`는 `result` 안의 가장 최근 실행을 선택합니다. 특정 실행을 지정하려면 `--resume result/해당_실행_폴더`를 사용하세요. `--clipboard`와 함께 사용할 수도 있습니다.

저장 완료된 응답은 다시 API에 요청하지 않습니다. 응답을 저장하기 전에 끊긴 요청은 같은 시도부터 다시 실행될 수 있습니다. 이미 완료된 실행은 API를 호출하지 않고 종료합니다. 실행 중인 다른 터미널과 같은 결과 폴더를 동시에 사용하지 마세요.

저장된 상태만 확인할 때:

```powershell
python code/start_gpt_with_key.py --inspect --resume latest
```

데이터·프롬프트·코드 해시가 달라지면 재개를 거부합니다. 실험 중에는 코드를 수정하지 않는 것이 원칙입니다.

## 데이터·결과 재현

### 데이터 재생성

```powershell
python code/generate_dataset.py
```

고정 시드 **2026102806**으로 후보 배치를 생성하고, 정답 경로를 찾은 배치만 채택합니다. 저장된 후보 캐시를 사용하지 않습니다. 현재 공개본 100문제의 데이터·정답·공개 배치와 비교하며, 내용이 다른 기존 파일은 덮어쓰지 않고 중단합니다. `--workers 1`처럼 병렬 작업 수를 조절할 수 있습니다.

### 실제 저장 응답 재채점

```powershell
python code/verify_results.py --run-folder result/2026-10-28_16-50-18.540_KST
```

저장된 API 응답 2,734개로 요청 기록과 채점을 복원하고, 원본 응답 해시·시도별 판정·결과 보고서를 대조합니다. 비교용 파일은 임시 폴더에 생성하며 원본 결과는 수정하지 않습니다. 생성형 모델을 다시 호출하여 같은 답변을 얻는다는 뜻은 아닙니다.

## 포함된 실험 결과

| 모델 | N | MPE (%) | Mean error (%) | API time (min) | Cost (USD) |
|---|---:|---:|---:|---:|---:|
| GPT-6.1 Sol | 85/100 | 38.80 | 64.07 | 204.14 | 8.3832 |

- N은 MPE를 계산할 수 있는 문제 수 / 완료된 문제 수입니다.
- 5라운드 모두 통과: **54/100문제**. 성공 라운드: **337/500개**, 실패 46개, 미진행 117개입니다.
- 비용은 저장된 사용량과 당시 단가에 따른 추정치입니다. 시간은 API 요청 소요시간의 합이며 요청 사이 대기는 제외합니다.
- 자세한 정의와 수치는 [평가 방법](docs/METHOD.md), [결과 요약](docs/RESULTS.md)에 있습니다.

## 결과 저장 형식

각 실행 폴더에는 API 요청·응답 원문, 시도별 판정, 라운드·문제별 집계와 전체 요약이 저장됩니다. 상세 결과는 JSON·JSONL·CSV로, 열람 화면은 `RESULTS.html`로 제공합니다. `code-snapshot/`에는 Python 실행·채점 코드가, `config.json`에는 설정과 스냅샷 해시가 저장됩니다. 파일별 설명은 [결과 폴더 안내](result/README.md)를 참고하세요.

## Git에 포함되는 파일

`.gitignore`는 Python 캐시, 키 파일, `.env`, 임시 파일과 **새로 생성한 실험 결과**를 기본 제외합니다. 포함된 100문제 결과 폴더는 예외로 추적합니다. 새 결과를 공개하려면 검사 후 해당 결과 폴더만 추적 예외에 추가하세요.

`.gitattributes`는 해시 검증 대상 파일의 줄바꿈 자동 변환을 막습니다. API 기록과 스냅샷은 해시 검증에 사용하므로 포맷터로 다시 저장하지 마세요.

