# PR #27 최종 병합 전 감사

## 1. 검토 대상과 판정

**PASS — RESEARCH_ONLY 수동 연구 시스템의 병합 범위에 한정.** 실전 전략 승인, 영구 백업 구축 완료, 모든 기존 운영 데이터의 현재 건강 상태를 의미하지 않는다. Draft 해제·main 병합·Release 발행·외부 업로드·자동 실행 활성화는 수행하지 않았다.

- PR: https://github.com/nh1018/upbit-scanner/pull/27
- 감사 기준 PR HEAD: `b2f81f833bff7e3360a62f9d81b123936e6219c1`
- 비교·병합 시뮬레이션 main: `6220ba258d9d2e78a1a78aa41dc771680f1d8d0b`
- 수정 전 merge-tree: `8992f90192882e82c71a3a84588ee4f7d21472e6` (exit 0, 충돌 없음)
- 테스트: 위 병합 트리를 별도 디렉터리로 export하고 아래 최소 수정 및 신규 테스트만 overlay. 실제 main checkout/병합은 하지 않았다.
- 검사 overlay SHA256: runner `39b9c8f5e1c07d005709500073f67ee1f03ef26fb61bc7100b0ab84fcdf3394d`, test `5042610e1f132f862a85466226f893c82df888692478f9e26419d6595a3c65be` (작업 파일 bytes).

### MASTER 대조

루트의 `코인매매_MASTER.md`(최종 갱신 2026-10-08)를 읽었다. 원문은 수정·스테이징·커밋하지 않는다. SHA256: `c34e598968a050581611205d7a0109091aa7d0e6501dec8f9f4ae7916c1c4f6d`.

MASTER의 B/C 예정 상태(72·84·604–605행) 및 B 설계 최우선(617–621행)은 실제 B 구현 및 본 C V1.0–V1.4 PR보다 앞선 문서 상태다. V1.2 Cold Archive, V1.3 Actions, V1.4 Release 패키지는 MASTER에 기재되지 않았다. 이는 문서/코드 차이이며, 임의로 MASTER를 갱신하거나 실전 승인으로 해석하지 않았다. A 보호, BTC raw 불변, 연구/실전 분리 원칙은 유지한다.

## 2. 전체 변경 범위

기준 PR diff 37개 파일. 이번 감사 추가 변경은 기존 신규 파일 `research_runner.py`의 보호 수정, 신규 회귀 테스트, 이 보고서다. 공통 라이브러리·A/B 전략·BTC production 코드 변경 없음.

| 파일 | 분류 |
|---|---|
| `.github/workflows/upbit-c-market-research.yml` | C 연구 Actions |
| `.github/workflows/upbit-c-research-smoke.yml` | C 연구 Actions |
| `btc_anytime/tests/test_evaluation_production.py` | 테스트 (BTC는 테스트 전용) |
| `tests/upbit_b/test_c_market_research.py` | 테스트 (BTC는 테스트 전용) |
| `tests/upbit_b/test_c_research_actions_storage.py` | 테스트 (BTC는 테스트 전용) |
| `tests/upbit_b/test_c_research_archive.py` | 테스트 (BTC는 테스트 전용) |
| `tests/upbit_b/test_c_research_diagnostics.py` | 테스트 (BTC는 테스트 전용) |
| `tests/upbit_b/test_c_research_release.py` | 테스트 (BTC는 테스트 전용) |
| `tests/upbit_b/test_c_research_score.py` | 테스트 (BTC는 테스트 전용) |
| `tests/upbit_b/test_c_research_segments.py` | 테스트 (BTC는 테스트 전용) |
| `upbit_c/INITIAL_SCORE_V0_PROPOSAL.md` | 문서·검증 자료 |
| `upbit_c/MARKET_RESEARCH_V1.md` | 문서·검증 자료 |
| `upbit_c/RESEARCH_ACTIONS_V13.md` | 문서·검증 자료 |
| `upbit_c/RESEARCH_OPERATIONS_V11.md` | 문서·검증 자료 |
| `upbit_c/RESEARCH_PRESERVATION_V14.md` | 문서·검증 자료 |
| `upbit_c/RESEARCH_STORAGE_V12.md` | 문서·검증 자료 |
| `upbit_c/research_actions_storage.py` | C 연구 전용 |
| `upbit_c/research_archive.py` | C 연구 전용 |
| `upbit_c/research_audits/actions_20261009.json` | 문서·검증 자료 |
| `upbit_c/research_audits/actions_storage_v13_20261009.json` | 문서·검증 자료 |
| `upbit_c/research_audits/coverage_20261009_0320.json` | 문서·검증 자료 |
| `upbit_c/research_audits/merge_readiness_v14_20261009.json` | 문서·검증 자료 |
| `upbit_c/research_audits/native_rerun_20261009.json` | 문서·검증 자료 |
| `upbit_c/research_audits/release_storage_v14_20261009.json` | 문서·검증 자료 |
| `upbit_c/research_audits/source_probes_20261009.json` | 문서·검증 자료 |
| `upbit_c/research_audits/storage_v12_20261009.json` | 문서·검증 자료 |
| `upbit_c/research_diagnostics.py` | C 연구 전용 |
| `upbit_c/research_history.py` | C 연구 전용 |
| `upbit_c/research_operations.py` | C 연구 전용 |
| `upbit_c/research_outcomes.py` | C 연구 전용 |
| `upbit_c/research_release.py` | C 연구 전용 |
| `upbit_c/research_runner.py` | C 연구 전용 |
| `upbit_c/research_scan.py` | C 연구 전용 |
| `upbit_c/research_score.py` | C 연구 전용 |
| `upbit_c/research_segments.py` | C 연구 전용 |
| `upbit_c/research_smoke.py` | C 연구 전용 |
| `upbit_c/research_storage.py` | C 연구 전용 |

추가: `tests/upbit_b/test_c_final_audit.py`, `upbit_c/PR27_FINAL_AUDIT.md`.

PR의 기존 파일 수정은 C smoke workflow/runner 및 BTC 테스트 1개다. BTC 테스트는 실제 evaluation workflow의 staging whitelist를 검증하도록 오래된 문자열 기대값을 수정한 것이며 evaluator/parameter 변경이 아니다. 가장 큰 추가 검증 JSON은 약 194KB이고 전체 raw/ZIP 원본을 Git에 넣지 않는다. 변경 파일의 일반 PAT/private-key/AWS 키 패턴 검사에서 노출 흔적은 발견되지 않았다(모든 가능한 비밀 탐지의 보장은 아님).

## 3. 기존 시스템 영향 및 최소 수정

A/B 운영 코드, BTC production 코드·Worker·Pine·parameter·raw/provenance·기존 운영 출력 경로는 본 diff에서 불변이다. C는 기존 B의 공개 HTTP/완료봉/feature 계약을 읽기 전용 재사용하며 해당 모듈을 수정하지 않는다. 기존 비-C workflow의 schedule/permissions는 불변이다.

**발견·수정한 결함:** `upbit_c/research_runner.py:26–33`의 구형 CLI output guard는 고정 이름 목록만 검사해 `.git`, `.github`, `upbit_c`, `tests`, `output_custom` 등을 차단하지 못했다. 수정 전 HEAD를 메모리에 로드하고 scanner를 실제 쓰기/API 전에 중단시킨 fixture에서 우회 도달을 재현했다. 파일 손상은 발생시키지 않았다.

기존 `research_archive.isolated()`를 호출하도록 통일했다. protected namespace는 API/client 생성 전에 거부한다. 정상 isolated destination은 유지한다. 점수·성과·재시도·workflow 동작 변경 없음. V1.3 Actions 경로는 이미 이 보호 함수를 사용해 이번 결함과 구분된다.

기존 main의 BTC latest 분석에서 RAW_CONTINUITY_ERROR 경고가 관측된 이력이 있다. 본 PR이 해당 파일/수집 경로를 바꾸지 않으므로 별도 기존 운영 이슈다. 본 감사의 영향 없음 판정으로 BTC의 모든 데이터가 현재 정상이라고 확대 해석하지 않는다.

## 4. C 연구 격리·계산 검증

- `research_scan.scan`: 공식 KRW 거래지원 목록, 1H/4H/1D 완료봉, 고정 scan cutoff, 종목/시간봉 실패 분리. 진행봉·누락·stale·부족은 검증 실패로 남기며 값을 보충하지 않는다.
- C 전용 client pacing 및 기존 HTTP retry 사용. 논리 요청 수와 실제 retry 포함 wire 요청 수는 다르며 shared-IP 합산 부하까지 보장하지 않는다.
- `research_score`: RESEARCH_ONLY, 초기 가설 파라미터. 5그룹/TF 가중치·임계값을 최적화하지 않음. 저장 파라미터 hash `d0b423bf1e00e03ac57858402e0fa5803df334236de2acf171647be41889f476`.
- 실제 scan source/input hashes와 Decimal 재계산, 부족 상태 검증. `candidate=NOT_EVALUATED`, entry signal 없음. 주문 실행 경로 없음.
- `research_history`: 최초 실제 관측 및 immutable unique ID, 동일 입력 replay no-op. 미래 데이터로 과거 신호를 만들지 않는다.
- `research_outcomes`: 관측 이후 다음 1H boundary의 open proxy, +1/+3/+7일 gross LONG 연구 수익·MFE·MAE. 실제 체결/수수료 포함 PnL이 아니다. 미성숙 PENDING, 불완전 UNVERIFIABLE, 연속 완료 path 및 evidence 충족 시 MATURED.
- 실제 연구 조건 PASS 0을 유지한다. 후행 성과 수치는 격리 fixture 검증이며 실제 승률·기대수익 증거가 아니다.

## 5. 원본 보존·복원 감사

V1.1 sealed incremental manifests/parent lineage → V1.2 content-addressed gzip originals/Active State → V1.3 index/cold 분리·checkpoint → V1.4 local Release package 순으로 기존 원본 참조와 bytes를 보존한다. 충돌은 overwrite 대신 실패한다.

실제 보존 패키지 3개를 현재 다시 full verify했다. 아래 SHA는 ZIP bytes 기준이며 manifest, 내부 originals, gzip/raw hash, import 연결 및 Active State까지 검증했다.

| 실제 출처 | ZIP bytes | SHA256 | 결과 |
|---|---:|---|---|
| V1.1 보존 원본 | 8,160,143 | `6fec7a281dbd9920b26933fc43223635210ce7994cd71e89a6b5624a75974f81` | VERIFIED |
| V1.2 V1.1 migration | 8,167,976 | `12992b2c7008a9478dec5f851dfef5b615b1c34cf861030b855f937e717febbf` | VERIFIED |
| V1.3 독립 checkpoint | 16,325,276 | `5bb5dcd1b385f51b7baa52d3f37c67a141f55e704fa50490fa854bc3ce9e6796` | VERIFIED |

실제 bytes 복원 동등성과 Active/lineage 연결은 기존 `research_audits/release_storage_v14_20261009.json`에 기록되어 있고 이번에는 그 패키지를 다시 검증했다. 3개 패키지에 중복되는 원본이 있으므로 독립 실데이터 샘플 수를 과장하지 않는다. 초기 V1.1 remote Artifact가 404인 경우 이전에 hash 검증된 로컬 보존 원본을 사용한 사실을 구분한다. GitHub에서 사라진 원본을 복구했다고 주장하지 않는다.

### 실패 시나리오 — 격리 fixture 검사

| 상황 | 검사 및 결과 |
|---|---|
| 부모 Artifact 만료/누락 | Actions storage/segments expiry·missing-parent 테스트: 새 root로 조용히 초기화하지 않고 실패 |
| 원본 손상 | archive/release object hash 실패, publication 중단 |
| Active State 누락/손상 | missing-signal/corrupt-active 검사, 평가 거부; explicit recovery는 독립 출력 |
| Manifest 불일치 | seal, wrong pin, import manifest 검사 실패 |
| 실행 중단 | orphan object retry 테스트, manifest-last commit 경계 |
| 동일 실행 중복 | replay 동일 bytes no-op, 다른 bytes 충돌 실패 |
| 일부 파일만 저장 | partial download/checkpoint/restore 검사, 검증되지 않은 완료 게시 거부 |
| lineage 불일치 | parent reference·fixture isolation·restore lineage identity 검사 |
| 체크포인트 복원 실패 | 누락/손상/충돌 원본 거부, 부분 retry 및 ancestor 독립 복원 검사 |
| Release ZIP/원본 hash 불일치 | external pinned digest 및 내부 원본 digest 검사 실패 |

routine outcomes-only는 Active/index 검증과 Cold 존재/만료 receipt 확인을 사용한다. 매번 Cold 전체 bytes를 재다운로드하는 full audit와 동일하다고 표현하지 않는다. 정기 full audit/독립 checkpoint가 필요하다. runner 강제 종료 또는 Artifact upload 자체 실패까지 durable recovery가 보장되지는 않는다.

## 6. Actions 운영 안전성

`upbit-c-market-research.yml`: 수동 dispatch 및 owner/same-repo 조건의 명시적 PR label 실행. schedule 없음. permissions contents:read/actions:read, checkout persist-credentials:false, C 전용 concurrency, cancel-in-progress:false, timeout 40분. RUNNER_TEMP 아래 연구 경로, 실패 시 recovery와 always Artifact 업로드. 저장/검증 실패는 성공으로 처리하지 않는다. Git push/Release upload/외부 객체 저장소 write 없음.

기존 `upbit-c-research-smoke.yml`의 **cron `55 2 * * *`는 원래 존재하며 유지**했다. PR/push smoke는 5개 종목 읽기 전용 검사다. 전체 연구 workflow가 모두 수동이라는 뜻은 아니다. checkout main 고정 제거는 PR 코드를 실제 검사하기 위한 기존 C workflow 변경이며 A/B/BTC 동작과 분리된다.

Artifact 90일은 영구 보존이 아니다. Native Re-run guard는 이미 GitHub가 기존 Artifact를 무효화한 뒤 실행될 수 있어 원본 보존 보장이 아니다. 원본이 필요한 run은 native Re-run 대신 새 run/명시적 parent, 만료 전 checkpoint/독립 백업을 사용해야 한다. Action major tags는 commit SHA pin이 아니므로 향후 공급망 hardening 과제로 남긴다.

## 7. 실제 Actions 증거와 이번 로컬 검사 구분

기존 V1.3 실행 기록:

| run ID | 실제 결과 |
|---|---|
| 37890440818 | V1.1 migration 성공 |
| 37890567339 | 전체 293종목 / 879 TF, 168 scoring 성공 / 125 불가, 조건 PASS 0; 논리 요청 920 |
| 37892180825 | outcomes-only 성공, index 18,979B, cold 다운로드 0 |
| 37892235950 | replay 성공, 신규 기록 0 |
| 37892289260 | 독립 checkpoint 성공 |
| 37892414525 | 의도적 failproof 실패 및 recovery Artifact; 정상 실행 성공으로 집계하지 않음 |

해당 run과 원본을 위험한 native Re-run으로 재검증하지 않았다. 상세 source hashes/bytes/time는 committed `actions_storage_v13_20261009.json` 참조. 이번 감사에서는 live 전체시장 scan을 새로 실행하지 않았다.

## 8. 전체 테스트 및 YAML

최신 pinned main 병합 export + 최소 수정에서:

| 범위 | PASS | FAIL |
|---|---:|---:|
| Upbit B/C Python (신규 경로 보호 3개 포함) | 473 | 0 |
| BTC Python | 389 | 0 |
| 공통 Python | 10 | 0 |
| Worker Node | 70 | 0 |
| **전체** | **942** | **0** |

명령: `python -B -m unittest discover -s tests/upbit_b -q`, `python -B -m unittest discover -s btc_anytime/tests -q`, `python -B -m unittest discover -s tests -p test_*.py -q`, `node --test btc_anytime/tests/worker.test.mjs btc_anytime/tests/production_compatibility.test.mjs btc_anytime/tests/fetch_binding.test.mjs btc_anytime/tests/diagnostics.test.mjs`.

20개 YAML을 PyYAML BaseLoader로 파싱하고 on/jobs 구조 검사 PASS. 이는 actionlint 전체 semantic 검사를 실행했다는 뜻이 아니다. 실제 PR Actions 검사는 별도로 확인했다. 테스트 disable/threshold 완화 없음. 예상 argparse 거부 stderr는 정상 negative fixture다.

## 9. GitHub 병합 상태

감사 시 GitHub API: open, Draft=true, mergeable=true, mergeable_state=clean. pinned main 로컬 merge-tree도 충돌 없음. HEAD 기준 `contract-tests`, `audit` 둘 다 SUCCESS. main protection API는 404 Branch not protected, effective rules 목록은 빈 배열이었다. 설정이 나중에 바뀔 수 있으므로 실제 병합 시 재확인해야 한다. 보호 규칙이 없는 것을 리뷰/승인 완료로 해석하지 않는다. Draft 해제와 병합은 사용자 승인 사항이다.

## 10. 남은 위험

- Release는 삭제/변경 가능한 저장소이고 실제 발행/외부 독립 백업은 미구축. D: 로컬 보존만으로 물리 디스크 장애에 대비하지 못한다.
- 90일 Artifact 만료, native Re-run, 업로드 실패, runner 중단으로 보존이 끊길 수 있다. 만료 receipt 및 주기적 full audit 필요.
- V1.4 package 약 2GiB/100k entries 한도 때문에 장기 누적 시 명시적인 checkpoint/package 분할이 필요하다.
- shared IP의 A/B/C API 요청 합산 부하는 별도 관찰 필요. 이번에는 자동 실행 주기를 늘리지 않았다.
- 실제 C 조건 PASS 사례가 없어 실데이터 후행 성과 검증은 아직 불가. fixture 성과를 전략 수익성 증거로 사용할 수 없다.
- MASTER의 B/C 단계 기술은 코드보다 이전 상태. 문서 관리 주체의 별도 현행화가 필요하다.

## 11. 병합 전 필수 조치

이번 보호 수정 및 전체 회귀 성공을 PR에 반영하고, 최종 SHA의 PR checks 성공과 최신 main 충돌 여부를 다시 확인한다. 본 감사는 사용자 병합 승인을 대체하지 않는다. 장기 정기 연구 운영을 시작하기 전 독립 백업/보존 담당·만료 전 checkpoint 정책을 승인해야 한다. 이것은 수동 연구 코드 병합과 별도 운영 승인이다.

## 12. 병합 후 초기 운영 권고

처음에는 수동 full scan → 명시적 parent 승계 → outcomes-only → independent checkpoint를 순서대로 실행하고 Artifact IDs/digests/만료일을 확인한다. production 신호/주문/자동 스케줄은 활성화하지 않는다. Release/외부 백업 발행은 별도 승인 후 시행한다. 첫 실제 연구 조건 PASS가 관측되면 미래 path가 성숙한 뒤 원본·evidence 기준으로 성과를 재검증한다.
