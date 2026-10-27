# PR #40 읽기 전용 시험 검증 — 2026-10-11

## 변경과 범위

- repository root GET의 불필요한 trailing slash 제거. 기존 자격증명 인증은 HTTP200이었지만
  잘못된 root 경로는 실제 HTTP404였습니다. 정상 경로 사용 후 관측을 완료했습니다.
- unknown/missing 최신 run state는 전체 BLOCKED. queued/waiting/in_progress 등 알려진 active
  state도 최신 run에서 직접 차단하여 global active listing과의 시점 차이를 보수적으로 처리합니다.
- 기존 GCM 인증을 비대화형으로 읽는 선택적 `--git-credential`과 `--include-evidence`를 추가.
  토큰은 메모리에만 있고 신규 생성/저장하지 않습니다. 실패 시 exit2, dispatch0.
- 신규 PR 전용 Linux CI. `pull_request`만 있으며 schedule/workflow_dispatch가 없습니다.
  contents read만 사용하고 기존 Production workflow를 변경하지 않습니다.
- Windows 수동/작업 스케줄러 절차는 `WINDOWS_READONLY_TRIAL.md`. 실제 task 등록0.

## 실제 인증·관측 결과

기존 Git Credential Manager 계정 `nh1018`로 `/user` GET HTTP200을 확인했습니다.
그때 rate remaining4992였습니다. 새 토큰은 생성하지 않았습니다.

| 시각 UTC | 실제 결과 | 해석 |
|---|---|---|
| 수정 전 인증 검증 | /user200, repository root404 | 인증 실패가 아닌 URL 결함; 수정 전 관측 불가 |
| 02:15:52.696–02:16:00.563 | 21 GET 완료, NO_ACTION | A/B CURRENT, 최신 run 성공/게시 step 성공 |
| 02:17:56.818 | 무효 Authorization GET401 | 1회 요청 후 중단, 재시도/dispatch 없음 |
| 02:17:57경–02:18:04 | 21 GET 후 main 변경 감지 BLOCKED | 서로 다른 revision 자료를 정상 판단에 섞지 않음 |

완전한 관측 revision: `fc44ec187ae32e523a9f8e027f1f66f2615e9b67`.

| 대상 | 최신 run | Source | 게시 step | 판단 |
|---|---|---|---|---|
| A | [38101057532](https://github.com/nh1018/upbit-scanner/actions/runs/38101057532), schedule, success | generated 01:12:50 UTC | Commit scan results 완료01:16:53 UTC | CURRENT, age약63분, A90분 복구 제안 전 |
| B | [38103256468](https://github.com/nh1018/upbit-scanner/actions/runs/38103256468), schedule, success | `2026-10-11/01.jsonl`, cutoff01:00 UTC, COMPLETE | Verify published cycle on origin/main 완료01:52:20 UTC | CURRENT, age약76분 |

A source SHA256 `c6cffdfb3520fbbe5b4d8a6fac046379250a2d50715725c7b6585a6104325217`.
B source SHA256 `7b91096917bdd7c167716f669b26347aeaa3397906e73327c4ca373c09c6fbd6`.
`02.jsonl`은 해당 관측 main에 없었습니다. 현재 시각이 :16이므로 제안된 B :35 복구 판정 전이며
NO_ACTION이 맞습니다. 미기록 구간을 NO_SIGNAL로 처리하지 않았습니다.

그 관측에서 A/B active run은0이었고 BTC Direction/Evaluation run은 진행 중이었습니다.
따라서 **실제 A/B 실행/게시 대기 중의 live 검증 사례를 확보했다고 주장하지 않습니다**.
queued/waiting/requested/pending/in_progress, 다른 branch active, latest status active와
publish 미확인은 재현 가능한 로컬 테스트로 검증했습니다.
실제 WOULD_DISPATCH 표본은 아직 없습니다. NO_ACTION 및 main-race BLOCKED의 정확성만 확인했습니다.

## 보관 증거

원본/관측 기록은 Production과 분리된 `D:/repos/freshness-watchdog-evidence-v1`에 write-once 저장했습니다.
credentials/Authorization 헤더는 포함하지 않습니다. 대용량 공개 API 원본은 Git에 넣지 않습니다.

| 파일 | 바이트 | SHA256 |
|---|---:|---|
| observation-20261011T021600Z-7b69a1d5.json | 593165 | 9d32afd6b317043bf084513151328067ccef747eb3eab75dcaef67aa088d17e0 |
| invalid-auth-d71b3a20.json | 220 | faa1e473cf82aaa1c09e52bdaae578cf74fa4a78b6f1fe875478f3208b5c1b8a |
| observation-20261011T021804Z-a2421914.json | 5055 | ce95ab31d40fb91258865f5c975205289faeff00f64b71980f9abed6b2b760a1 |

## 오류 차단 테스트 구분

- 실제: 인증 성공200, 잘못된 root404, 무효 인증401, main 변경 감지.
- 모의: HTTP403/429/500, 네트워크 timeout/OSError, unknown state, pagination/budget,
  active/publish 대기, invalid/partial source, concurrent/ambiguous simulation.
- API 한도를 고의로 소진하거나 네트워크를 끊지 않았습니다.
- 신규 안전성 테스트25개. Windows 공통 테스트/전체 Linux 회귀 결과와 CI URL은 PR 검증 결과에 기록합니다.
  CI에는 인증 실시간 API 조회를 넣지 않아 Production main 갱신에 따라 회귀 테스트가 비결정적으로
  실패하지 않도록 했습니다. Linux CI는 offline 계약·회귀 검증입니다.

## 시험 준비 판정과 남은 위험

인증 GET 기반 **읽기 전용 수동 시험은 가능**합니다. Windows timer 설치·지속 관찰은 아직 하지 않았습니다.
복구 판단 정책은 승인 전 제안이며 자동 복구로 사용할 수 없습니다.
자주 갱신되는 main 때문에 안전 중단이 반복될 수 있으므로 유효 관측률을 장기 관찰해야 합니다.
실제 queued/publish 대기 및 WOULD_DISPATCH 사례도 추가 관찰이 필요합니다.
기존 GCM 자격증명 권한이 넓다면 향후 운영 전에 별도 read-only 권한으로 제한하는 방안을 승인받아야 합니다.
403은 권한 또는 제한 등 원인이 다양하므로 인증 성공 여부만으로 원인을 단정하지 않습니다.
토큰 없는 비인증 경로로 자동 fallback하지 않습니다.
기존 schedule와의 dispatch 경합/게시 결과 인과 결속/완료 복구는 이번에도 검증·실행하지 않았습니다.
실제 task 등록, 외부 배포, dispatch, 자동 복구, main 병합 모두0입니다.
