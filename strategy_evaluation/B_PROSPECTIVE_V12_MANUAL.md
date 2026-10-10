# B Prospective V1.2 — Activation Evidence & Manual Runner

상태: 개발 준비. 실제 승인, activation, 신규 prospective 수집은 수행하지 않았습니다.
V1.1 canonical 계약은 변경하지 않습니다:
`c38c83913c6a96ca7e59e8b243f311f783f7e7ff1007c4cc828017bf86565e7a`.
V1.2는 실행/증거 계층이며 전략, 평가 계산식, V1.1 가설과 discovery16을 변경하지 않습니다.

## 실행기

`python -B -m strategy_evaluation.b_prospective_manual <command>`

- `status`: 승인 파일이 없으면 로컬 계약과 코드 revision만 확인, 네트워크/쓰기 없음.
- `dry-run`: 승인 없이 준비 계획 확인. `--journal-revision <40자리 SHA>`로 로컬 Git에 이미
  존재하는 전체 journal 원본을 읽어 lineage/discovery를 검증할 수 있습니다. Git fetch는 하지 않습니다.
- `prepare-activation`: 별도 실제 승인 이후에만 사용. 기존 사용자 GitHub 댓글을 읽어
  미래 activation 기록을 연구 root에 생성합니다. GitHub에는 아무것도 쓰지 않습니다.
- `population`: 전체 검증 lineage의 적격 신호를 context/outcome보다 먼저 등록합니다.
- `collect`: 모집단의 미확보 context에 대해 한 번씩 요청합니다. 성공 context는 고정하고 건너뜁니다.
- `evaluate`: 기존 `runner.fetch_evaluation`/공통 evaluator로 +1/+3/+7일 평가합니다.
- `report`: 전체 모집단으로 보고합니다. 기본 stdout, `--save-report`를 명시한 경우만 불변 저장합니다.

실제 쓰기 명령은 코드 commit 일치, 실행 코드 tracked diff 없음, 승인 activation 바이트 SHA256,
GitHub 승인/증인 재조회, 시작 경계 통과, 격리 root를 모두 요구합니다.
공개 GitHub API rate limit을 고려하여 필요하면 `GITHUB_TOKEN` 환경변수에 읽기 권한 토큰을
설정하십시오. 토큰과 Authorization 헤더를 기록하지 않습니다. API 오류/권한 실패 시 중단합니다.
기존 A/B/C/BTC workflow와 스케줄을 변경하거나 자동 실행하지 않습니다.

## 사용자 승인 및 서버 증거 절차 — 지금 수행하지 않음

GitHub PR #37의 issue comment를 증거로 사용합니다. 작성자는 `nh1018` User여야 하며,
본문은 아래 helper가 출력하는 canonical JSON과 정확히 같아야 합니다. 편집된 댓글은 거부합니다.
댓글을 작성하는 명령은 실행기에 없습니다. 사용자께서 별도 승인 후 직접 게시하셔야 합니다.

1. 검토한 최종 코드 commit을 고정합니다. 이후 코드가 바뀌면 재승인이 필요합니다.
2. `record_text(COMMIT)` 내용을 PR #37에 게시합니다. 서버 `created_at`이 계약 기록 시각입니다.
3. 미래 UTC 경계와 재시도 정책을 승인합니다. `approval_text(COMMIT, BOUNDARY_MS, 3, 3600000)`
   내용을 사용자가 게시합니다. 승인 원문·참조·서버 생성 시각이 기록됩니다.
4. `prepare-activation`으로 로컬 기록을 생성합니다. 현재 GitHub 서버 시각 상한보다
   최소 60초 뒤 경계만 허용하므로, 실제로는 게시/검증 여유를 충분히 두십시오.
5. 출력된 `activation_file_sha256`과 `required_user_witness`를 확인하고 해당 증인 본문을
   PR #37에 별도로 게시합니다. 증인 댓글 서버 생성 시각은 기록 준비 이후이며 경계 이전이어야 합니다.
6. activation 경계를 지난 뒤 원본 activation SHA와 증인 comment ID를 지정해 명령을 실행합니다.

예시 템플릿 생성(출력만 하며 승인이나 댓글을 생성하지 않습니다):

```python
from strategy_evaluation.b_activation_evidence import record_text, approval_text, millis
print(record_text('<검토한 40자리 COMMIT>'))
print(approval_text('<같은 COMMIT>', millis('<별도 승인한 UTC: ...Z>'), 3, 3600000))
```

```powershell
python -B -m strategy_evaluation.b_prospective_manual prepare-activation `
  --record-root D:/repos/b-prospective-evidence-v12 `
  --contract-comment <계약 댓글 ID> --approval-comment <사용자 승인 댓글 ID> `
  --activation-utc <승인한 미래 UTC> --max-attempts 3 --retry-interval-seconds 3600
```

동일 승인 입력으로 재실행하면 기존 기록을 검증하여 유지합니다. 다른 기록으로 교체하지 않습니다.
`activation.json` 파일 SHA는 파일 자체에 자기 참조로 넣지 않고, CLI 출력과 외부 증인 댓글에 보존합니다.
생성 시각, 승인 서버 시각, activation 경계는 서로 다릅니다. 늦게 수집한 자료의 수신 시각도 소급하지 않습니다.

### 증거의 신뢰 범위

GitHub API를 TLS로 재조회하여 작성자·본문·ID·서버 `created_at`/`updated_at`을 확인합니다.
해시는 바이트 무결성이지 승인자 서명이 아닙니다. GitHub 계정/서비스와 TLS 신뢰에 의존합니다.
GitHub 댓글은 수정·삭제될 수 있어 영구 불변 공증이 아닙니다. 삭제/변경/조회 실패는 fail-closed입니다.
서버 HTTP Date는 인증 NTP가 아니며 시계의 과거 진위를 증명하지 않습니다. 요청/응답과 monotonic
경과시간을 비교하고 RTT 1초 이내, HTTP Date의 1초 해상도를 포함한 오차 상한 2초 이내일 때만 사용합니다.
서버 기록이 과거의 activation 파일 해시를 경계 이전에 가리켜야 하므로 로컬 시각만 바꿔 소급할 수 없습니다.
Windows 시간 서비스, 시간 서버, 레지스트리, 방화벽을 변경하지 않습니다.

## 실제 운영 명령 — 향후 승인 후

모든 실행은 기본 전체 모집단 순서(관측 시각, signal ID)입니다. 종목 선택 옵션은 없습니다.
`--journal-revision`은 로컬 Git object에 존재하는 전체 B journal snapshot의 정확한 SHA입니다.
새로운 신호가 게시되면 새로운 revision을 지정하여 population을 먼저 실행하십시오.

```powershell
$common = @('--record-root','D:/repos/b-prospective-evidence-v12',
 '--activation','D:/repos/b-prospective-evidence-v12/activation.json',
 '--activation-sha256','<실제 외부 고정 SHA256>', '--witness-comment','<증인 ID>',
 '--journal-revision','<원본 B journal을 포함한 40자리 Git revision>')
python -B -m strategy_evaluation.b_prospective_manual status @common
python -B -m strategy_evaluation.b_prospective_manual dry-run @common
python -B -m strategy_evaluation.b_prospective_manual population @common
python -B -m strategy_evaluation.b_prospective_manual collect @common
python -B -m strategy_evaluation.b_prospective_manual evaluate @common
python -B -m strategy_evaluation.b_prospective_manual report @common
```

자료 미확보는 FAILED/PARTIAL/CONFLICT 및 모집단 분모에 남습니다. 기본 재시도는 **최대 3회,
최소 1시간 간격**이며 승인 본문에 고정됩니다. 명령 한 번에 각 적격 신호당 최대 한 번 시도합니다.
재시도는 다음 수동 `collect` 실행에서만 수행하며, 대기·자동 스케줄은 추가하지 않습니다.
공식 Upbit 1시간봉 25개와 Binance BTCUSDT spot 25개를 원래 완료 cutoff로 제한합니다.
API 재조회는 HISTORICAL_AS_RETRIEVED이며 AS_OBSERVED로 승격하지 않습니다.
기존 데이터가 없는 시간대를 생성하지 않습니다. 미래 가격 경로는 성숙한 평가에만 조회합니다.
성과는 gross NEXT_1H_OPEN_PROXY 연구 결과이며 실제 체결가/수익이 아닙니다.

## Dry-run 및 fixture

승인 없는 `dry-run`은 NOT_ACTIVATED 준비 계획이며 실제 실행 성공을 의미하지 않습니다.
전체 lifecycle은 오프라인 합성 테스트로 검사합니다. 승인 이후 fixture를 사용한 CLI 검증은:

```powershell
python -B -m strategy_evaluation.b_prospective_manual dry-run @common `
 --fixture-bundle <격리 fixture JSON> --fixture-sha256 <원본 파일 SHA256>
```

fixture schema: `{"kind":"OFFLINE_FIXTURE_ONLY","responses":{ "정확한 요청 URL":
{"raw_base64":"...","sha256":"..."}}}`. 실제 시장 HTTP를 사용하지 않습니다.
읽기 시각은 **simulation_only**로 구분되며 결과를 실제 관측 증거로 저장하지 않습니다.
전용 fixture 옵션은 collect/evaluate 등 실제 명령에서 거부됩니다.
승인 재조회에는 GitHub read-only API가 필요합니다. CLI dry-run은 모든 파일/lock 생성 0건입니다.

## 저장과 복구

명시적인 `--record-root`가 없으면 쓰지 않습니다. 예시 root는 기존 저장소·A baseline·백업과 분리됩니다.

- activation.json: 승인/서버 증거/경계/코드 SHA/재시도 정책
- activation_witness/: activation 경계 이전 GitHub 증인 댓글 원문
- journal_sources/: Git blob 원본 바이트, SHA256 주소
- journal_manifests/: 각 source revision, 파일 경로·크기·해시·activation 연결
- events/: POPULATION 및 ATTEMPT append-only 이벤트, context 포함
- attempt_starts/: 요청 전 불변 시작 기록
- raw_responses/: 원본 API 응답 .bin (실패/잘못된 응답 포함)
- response_receipts/: URL/수신 시각/SHA256
- outcomes/: 기존 공통 엔진의 append-only 평가 이벤트
- reports/: 명시적 --save-report 결과만 content-addressed 저장

원본 바이트는 절대 재직렬화해 대체하지 않습니다. 읽기/재실행 때 retained raw hash와 context,
MATURED outcome을 다시 연결·검증합니다. 손상/누락이면 중단합니다.
원본 root 전체와 외부 activation SHA/증인 참조를 함께 보존하십시오. 검토한 코드 commit과
journal snapshot을 확보한 다음 status → population 재검증 → report 순서로 복구합니다.
없는 activation/원본을 새로 만들거나 조용히 초기화하지 마십시오.

쓰기 명령은 root의 .manual-writer-lock으로 직렬화합니다. 중단 후 lock은 자동 해제하지 않습니다.
운영자가 실행 중 프로세스가 없음을 확인하고 lock을 증거용으로 이동한 뒤 재개해야 합니다.
요청 시작만 있고 완료 이벤트가 없으면 다음 collect는 원래 시작을 보존하여 FAILED_INTERRUPTED_RECOVERED를
추가하고 해당 실행에서는 재요청하지 않습니다. 이후 승인된 재시도 간격을 따릅니다.
부분 파일과 orphan raw는 삭제하지 않습니다. 명확한 오류와 원본 보존을 우선합니다.

## 알려진 최소 수정

V1.1 context 검증의 Python 객체 직접 비교를 canonical digest 비교로 변경했습니다.
JSON 저장 후 Decimal→문자열 변환 때문에 정상 context가 재시작 시 거부되던 문제를 수정한 것입니다.
V1.1 계약 해시·계산식·기존 원본은 변경하지 않았습니다.

## Activation 전 별도 승인

최종 코드 commit, 고정 V1.1 계약, GitHub 증거 절차와 신뢰 한계, 실제 UTC 경계,
연구 root 및 재시도 정책을 승인해야 합니다. 실제 활성화·신규 표본 수집은 이번 개발에 포함되지 않습니다.
main 병합은 하지 않습니다. pinned PR commit으로 수동 연구가 가능하며 자동 운영은 별도 승인입니다.

GitHub issue comment API 근거: https://docs.github.com/en/rest/issues/comments
