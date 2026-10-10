# Production 운영 감사 — 2026-10-11 KST

검증 main: `315e0f0b7a3a87b41b78eefc064855b392a95281`.
48시간 창: 2026-10-08 23:23:10 UTC ~ 2026-10-10 23:23:10 UTC.
MASTER는 로컬 원문을 읽기만 했으며 수정하거나 Git에 추가하지 않았다.
운영 실행, 배포, raw/history/기존 output 변경은 하지 않았다.

## 정상 확인

- 대상 workflow 모두 active, 기본 브랜치 main, workflow 파일 존재.
- BTC Snapshot: 2026-10-10T23:18:57.258Z. 완료 15m open=23:00/close=23:15 UTC.
  Direction `9c2a09bfbce4df909c4873b7557adc757707341abfebd848d4601adeace31878`과
  Entry upstream ID 일치. NEUTRAL → NO_ENTRY, matches_latest_15m=true.
- 15m OI BASIS_UNVERIFIED는 derivatives/change/state=null이며 0/중립으로 대체하지 않았다.
  1h/4h/1d는 tradingview/BTCUSDT_PERPETUAL/BTC/confirmed_oi_bar_close_boundary의 검증된 segment.
  Feature/Direction/Entry 계산식과 parameter/registry는 변경하지 않았다.
- A run [38089605913](https://github.com/nh1018/upbit-scanner/actions/runs/38089605913) 성공.
  latest_scan=2026-10-11T06:57:58+09:00, KRW294, scanned272, candidate30, Binance OK.
  제외22종목을 API 실패로 단정하지 않는다. completed daily와 rolling24H 필드를 분리한다.
  generated_at은 스캔 시작시각이며 게시 성공 시각 증명이 아니다.
- B run [38091687236](https://github.com/nh1018/upbit-scanner/actions/runs/38091687236) 성공.
  22.jsonl cutoff22:00 UTC, 수집22:30:58.225~22:47:18.662 UTC.
  universe/attempted/succeeded294, failed/unattempted0, insufficient11, candidate21, COMPLETE.
  API 성공과 지표 충분성은 별개다. B Compact History main 운영과 별도 V1.2 연구 실행기를 구분한다.
  최초 activation2026-10-10T10:00Z 및 승인된 코드 전환2026-10-13T00:00Z는 유지했다.

## 발견된 결함과 최소 수정

1. **P1 BTC integrity 무시**: 최신 Snapshot에 15m missing2/abnormal2, 1h missing1/abnormal1이 있지만 Health는 CURRENT.
   실제 누락 open UTC: 15m2026-10-07T15:00Z,16:45Z; 1h2026-10-07T14:00Z.
   4h/1d 오류0, 모든 TF duplicate0/OHLCV invalid0. 저장 Snapshot와 load_raw로 대조했다.
   수정안은 DEGRADED로 차단하며 raw는 보간/복구/변경하지 않는다.
2. **P1 재생성된 오래된 엔진 출력 허용**: snapshot generated_at만으로 Direction/Entry가 최신이 되지 않는다.
   기존 snapshot policy decision_max_age_ms1800000/publication_allowance_ms1200000와 alignment를 소비시점에 검사한다.
   snapshot45분, A/B120분 기준은 변경하지 않았다.
3. **P2 B 필드 오류**: 실제 manifest는 completeness/failed인데 Health는 complete/failure_count를 읽어
   COMPLETE도 false/null로 표시했다. partial도 usable=true였다. 실제 필드로 수정하고 partial은 DEGRADED 처리한다.
4. **P1/P2 캐시된 CURRENT 재사용**: 저장 health21:40:50 UTC는 BTC21:36/A17:58/B18:00을 참조해 최신 main과 다르다.
   valid_until_utc와 at_consumer_time을 추가했다. 구형 CURRENT에 만료가 없으면 DEGRADED.
   현재 원자료로 build하는 것이 우선이며 cached expiry는 이후 upstream 변경을 발견하지 못한다.
5. **P2 Health trigger**: GITHUB_TOKEN의 output push는 다른 push workflow를 실행하지 않는다.
   성공한 A/B/Snapshot/Entry workflow_run을 구독한다. cron/권한 확대 없음, PR 검증 job은 읽기 전용.
   BTC availability→direction→entry→snapshot→health는 workflow_run 깊이 제한에 걸릴 수 있어 Entry도 구독한다.
   Entry hook과 Snapshot 게시 사이 race 및 schedule 지연은 남는다. 즉시 최신 Health를 보장하지 않는다.
6. **P2 B pipeline 실패 은폐**: run [38007050622](https://github.com/nh1018/upbit-scanner/actions/runs/38007050622)는
   23:59:37 UTC에 시작해 collector의 `start grace exceeded`가 발생했다.
   tee가 종료코드를 가려 수집 step이 success, 다음 원격 검증에서 빈 로그의 IndexError가 발생했다.
   shell:bash와 pipefail만 명시했다. grace/전략/재시도/기록 조건은 유지한다.

## 실제 Actions 기록

| Workflow | 예약 슬롯 | schedule run | 성공/실패 | 기록 부족분 |
|---|---:|---:|---:|---:|
| Health |192|9|9/0|183|
| BTC Snapshot |192|10|10/0|182|
| B History |144|26|25/1|118|
| A Scanner |48|9|9/0|39|

API는 원래 예정 슬롯 ID를 제공하지 않아 지연과 영구 누락을 확정적으로 구분할 수 없다.
BTC Snapshot은 workflow_run으로 계속 갱신된다. 최근100개 혼합 run의 cancel/skip을 48시간 전체 통계라고 주장하지 않는다.
공식 근거: [schedule 및 workflow_run 제한](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows),
[GITHUB_TOKEN 이벤트 제한](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).

## 실제 Entry lifecycle 및 MASTER 권장 문구

SHORT/BREAKOUT setup_id `ee9324aae8c9eedfb7bf076fac4b5d42b50cac97092cdf5141f5ea89495efc22`.

- ARMED 2026-10-08T15:32:00.070Z, publication43cebd2a784d0122f64aa6deaee3f303525180266168fbd7cd3e20e3f847656e,
  commit [f18db2e6](https://github.com/nh1018/upbit-scanner/commit/f18db2e6b66e9643f01bf20b4d4b94ececcde4c8).
- EXPIRED 2026-10-08T16:31:25.696Z, publicationb48226e944a9c63fdab254427c392e43cd686bbac919f298d826537479f7fc70,
  commit [6b329b8e](https://github.com/nh1018/upbit-scanner/commit/6b329b8eb40f010a6a91d77da8d6663bf84bd80a).
- 원본/engine/input integrity를 검증했다. historical_reconstruction0, operational_status EVALUATED.
  같은 setup 보존 record224개에서 최초 lifecycle 변경 ARMED→EXPIRED 확인.
  반복 상태 보존은 신규 setup 중복 발행이 아니다. CONFIRMED/INVALIDATED/체결/수익성은 미검증.

MASTER 기존 B 설계예정 문구는 실제 개발보다 오래됐다. 권장:
“B Market Data/Feature/Trend-State/Compact History 구현 및 main 운영 확인.
별도 B Prospective V1.2는 최초 activation과 기존12건을 보존하며 2026-10-13T00:00Z 전환 대기.”
“BTC SHORT BREAKOUT의 실제 prospective ARMED→EXPIRED를 원본과 게시 commit으로 검증했다.
CONFIRMED/INVALIDATED 및 실제 체결 검증과는 구분한다.” MASTER는 수정하지 않았다.

## 테스트와 남은 위험

로컬 공통Python72 + B/C582 + BTC389 + Node70 = **1113 PASS / 0 FAIL**.
STALE/MISSING/INVALID/DEGRADED, alignment/continuity, 미래/naive시각, 과거 decision 재생성,
cached expiry, B COMPLETE/PARTIAL 및 기존 replay/duplicate 회귀 포함.
Windows MinGit에는 bash가 없어 로컬은 workflow 안전장치를 검사하며, 실제 pipeline 실패 fixture는 Linux PR CI에서 실행한다.
YAML 구문은 격리된 PyYAML6.0.3으로 검증하고 운영 의존성은 추가하지 않는다.
기존 A/B 전략, BTC 계산식/parameter, raw/history/output/Activation/Registry 변경0.
main 병합/운영 실행 없음. 배포 후 Health workflow_run 실제 동작은 아직 미검증이다.
schedule 보장 문제와 raw gap은 남아 있다. 전체 감사 판정은 **PARTIAL**이다.


## PR #38 추가 검토 — 전체 raw gap 차단 정책 정정

앞선 raw gap 자체를 Health 차단 사유로 본 결론은 수정한다. Feature V1은 누락 슬롯을
가격 상태 리셋 사유로 삼지 않는 기존 계약이다. 전체 과거 결손 수를 0으로 요구하면
현재 유효한 신호도 계속 DEGRADED가 된다. OI strict continuity는 별도이며 변경하지 않는다.

새 Health는 원본 Snapshot hash, Direction/Entry/입력 파일 SHA256과 내부 hash,
기존 validate_snapshot/Entry validate를 확인한다. 저장 Feature 값·quality 일치,
Direction 필수 component 가용성, Entry EVALUATED 및 실제 consumed 15m 경로를 검사한다.
NEUTRAL NO_ENTRY 및 명시적 authorization veto는 기존 엔진의 path 검사 이전 종료 계약을 유지한다.
부족한 증거·활성 경로 결손·검증 실패는 계속 차단한다. 엔진 재실행은 없다.
전체 과거 integrity, warning, provenance는 그대로 노출한다.

2026-10-10T23:44:04.114358Z 원격 main cbdb2cf878d92f05e0a535a029bd00d9b0f4d0f8 고정 조회:
Snapshot 23:33:22.264Z, Direction 23:31:03.667Z, Entry 23:31:47.384Z.
15m 과거 missing2/abnormal2, 1h missing1/abnormal1 유지.
Entry consumed 15m 10개 경로 gap0, 원본 검증 통과, NEUTRAL/NO_ENTRY.
같은 입력에 이전 PR 로직 DEGRADED → 수정안 CURRENT (생성시점 및 위 consumer 시점).
valid_until 2026-10-11T00:01:03.667Z이며 이후 CURRENT를 재사용할 수 없다.

all_timeframes_fresh_at_generation이 현재 deadline/전체 integrity 결과와 혼합됐던 점도 수정한다.
generation flags와 consumer-time freshness를 분리하며 cached 재검증도 generation 값을 보존한다.
실제 원본을 고정한 gzip fixture는 네 파일의 원본 UTF-8 바이트를 보존하고 미래 main 변경과
무관한 재현 테스트로 사용한다. active-invalid 분기는 별도 합성 테스트로 구분한다.
전체 테스트: 공통78 + B/C582 + BTC389 + Node70 = 1119 PASS / 0 FAIL.
main 미병합, raw/history/엔진/파라미터/운영 evidence 변경0.
