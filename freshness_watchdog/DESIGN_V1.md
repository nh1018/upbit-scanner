# Independent Freshness Watchdog V1 — 읽기 전용 설계·검증

## 범위와 판정

기준 main: `c0277e06cf8d95254fbc7ddee302f539f929f3a3`.
이번 구현은 **GET 전용 관측기와 복구 판단기**입니다. POST, dispatch 실행기,
예약 등록, 서비스 배포, 토큰 생성 기능은 없습니다. 로컬 판단 검증은 가능하지만
자동 복구 Production 준비 완료를 의미하지 않습니다.
BTC 45분, A/B 120분 Health 제한과 기존 전략·raw·prospective 기록은 변경하지 않습니다.
B 승인 전환 `2026-10-13T00:00:00Z`도 변경하지 않습니다. 이 watchdog는 B의
Production journal workflow를 감시하며, 별도 Prospective V1.2 연구 실행기를 실행하지 않습니다.

## 기존 실행·게시 경로 감사

| 대상 | Workflow ID | 기존 cron UTC | 게시 증거 | 경합 방어와 한계 |
|---|---:|---|---|---|
| A | 368188656 | 매시 :37 | Commit scan results 성공 + main 결과 바이트 검증 | concurrency `upbit-a-v13-scan`, cancel=false. 서로 겹친 collection/push 비용과 오래된 결과 게시 가능성을 완전히 제거하지 못함 |
| B | 375462203 | 매시 :07/:17/:27 | Verify published cycle on origin/main 성공 + 전체 cycle 검증 | concurrency `upbit-b-prospective-history-v1`, cancel=false. cycle write-once, 동일 원본 no-op, 상이한 원본 충돌 거부 |

B `history_contracts.py`는 현재 실행 시점의 boundary를 사용하고 start grace 59분,
workflow timeout 25분을 적용합니다. watchdog가 과거 boundary를 입력하는 경로는 없습니다.
`history.py:write_once`의 기존 불변성 검사를 재사용하며 journal 결손을 생성하지 않습니다.
NO_JOURNAL은 관측 공백이며 NO_SIGNAL로 해석하지 않습니다.

GitHub concurrency는 실행 직렬화를 제공하지만 기본 pending 교체 가능성이 있으며,
외부 판단 직후 정규 run이 생성되는 경합도 존재합니다. 로컬 잠금만으로 GitHub 전체의
exactly-once 실행을 보장한다고 주장하지 않습니다.
[GitHub concurrency](https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency)

## 최소 구성과 환경 비교

| 환경 | 예상 추가 비용 USD | 장점 | 한계·운영 부담 |
|---|---|---|---|
| 기존 상시 Linux 서버/NAS + systemd timer | 기존 장비라면 서비스료 0, 전기·통신 별도 | 기존 Python/B 검증기와 SQLite 그대로 재사용, 영속 잠금, PC 절전 독립 | 서버 패치·디스크·NTP·타이머 자체 감시 필요 |
| 소형 VPS + timer | 예시 월 $4부터, 세금·백업 별도 | 상시 실행과 영속 상태, 최소 코드 변경 | 가입·결제 승인과 서버 관리 필요; 실제 메모리/CPU 부하는 배포 전 측정 |
| Windows 작업 스케줄러 | 기존 PC라면 서비스료 0, 전기 별도 | 현재 Python으로 읽기 전용 시험이 쉬움 | 절전·종료·재부팅·인터넷 단절 중 감시 불가; IgnoreNew, 최대 실행시간, missed-run 정책 검토 필요 |
| Cloudflare Workers Cron + Durable Objects | 무료 한도 후보, 유료 Workers 기본 월 $5 + 초과분 | PC와 GitHub 타이머에서 독립, 서버 관리 감소 | 현재 Python/B 검증기를 그대로 배포할 수 있다고 검증하지 않음. 포팅·CPU/스토리지 한도 실측·강한 일관성 잠금 필요 |
| cron-job.org + 검증 receiver | 타이머 무료, receiver 비용 별도 | 외부 HTTP 타이머 | 응답 제한 30초/64KB. 조건 판단·영속 잠금·토큰 보관 receiver가 별도로 필요; GitHub dispatch 직접 호출은 권장하지 않음 |

**권고:** 상시 장비가 있다면 단일 Linux 실행기+SQLite를 우선 사용합니다. 없으면
Windows 읽기 전용 관찰로 먼저 운영 패턴을 확인하고 소형 VPS를 검토합니다.
무료 외부 서비스는 별도 포팅과 상태 관리가 필요하므로 이번 최소 구현의 즉시 배포 대상으로 삼지 않습니다.
외부 타이머도 장애 가능성이 있으며 GitHub API/Actions 자체 장애는 복구하지 못합니다.

가격·제한 확인: 2026-10-11. 신규 가입이나 결제는 하지 않았습니다.
[VPS 가격](https://www.digitalocean.com/pricing/droplets),
[Cloudflare 가격](https://developers.cloudflare.com/workers/platform/pricing/),
[Durable Objects 가격](https://developers.cloudflare.com/durable-objects/platform/pricing/),
[cron-job FAQ](https://cron-job.org/en/faq/),
[Windows IgnoreNew](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-multipleinstancespolicy-settingstype-element).

## 구현과 판단 순서

`reader.py`는 main revision을 고정하고 다음을 읽습니다.

1. repository/default branch와 두 workflow state·ID·파일 SHA256.
2. 최신 main run의 conclusion 및 실제 게시 검증 step 성공 여부.
3. queued/in_progress/waiting/requested/pending 전체 페이지. 다른 branch의 A/B 실행도 차단.
4. A 게시 JSON의 scanner version·기본 카운트·생성시각·바이트 SHA256.
5. B 현재 시간 journal 존재 여부와 최근 이틀의 최신 journal 전체 바이트.
   기존 `validate_cycle` 및 Compact unpack으로 Manifest/record/evidence 계약을 검증.
6. GET 완료 후 main이 동일한지, 관측이 시간 경계를 넘거나 시계가 변하지 않았는지 재검증.

Git contents의 Git blob SHA1도 다운로드 바이트와 비교합니다. 응답 원본 SHA256·수신시각은
메모리 receipts에 유지합니다. 토큰·Authorization 헤더는 기록하지 않습니다.
HTTP 캐시 방지 헤더/nonce를 사용하지만 HTTP Date를 인증된 시각 증거로 취급하지 않습니다.
운영 전 OS UTC 동기화/시계 역행 검사를 따로 확인해야 합니다.

`core.py`는 관측 완료 후 90초 이내, 전체 관측 120초 이내만 인정합니다.
API 오류/불완전한 페이지/config 변경/비활성 workflow/main 변경은 BLOCKED입니다.
최신 run 실패 또는 게시 step 미확인, source 결손·무효·partial은 해당 전략의 복구 제안을 차단합니다.
복구가 필요해도 실패 원인을 우회하여 다시 실행하지 않습니다.

다음은 **승인 전 제안 정책**이며 Health 제한 변경이 아닙니다.

- 외부 점검 5분 간격.
- A: 마지막 결과 생성시각 age 90분 이상이면 복구 필요 제안. Health STALE은 여전히 120분.
- B: 현재 시간 journal이 :35 이후에도 없으면 현재 시점 기록을 위한 복구 필요 제안.
  :27 정규 backup 이후 8분 대기이며 실행시간 분포로 재검토할 수 있습니다.
- A/B 실행이 하나라도 있으면 둘 다 차단. 동시에 필요하면 B를 우선하며 tick당 1건만 제안.
- 현재 hour만 대상으로 key를 생성하고 과거 미기록 hour는 그대로 남깁니다.
- 기존 run의 게시 성공과 현재 source의 유효성은 확인하지만 **특정 run과 특정 blob의 완전한
  인과 결속을 모든 경우 입증하는 기능은 아닙니다**. 실제 dispatch 후 완료 판정에는 run ID,
  게시 commit, 출력 바이트를 추가로 결속해야 합니다. 이 때문에 현재 코드는 판단 전용입니다.

## 중복·장애·인증 설계

`simulation.py`는 임시 SQLite에서 BEGIN IMMEDIATE와 고유 key로 동시 예약을 검증합니다.
전략별 1시간 cooldown, A/B 공통 unresolved 잠금을 적용합니다. RESERVED/UNKNOWN/
SIMULATED_ACCEPTED는 자동으로 해제하지 않습니다. 성공 응답만으로 게시 완료라고 보지 않습니다.
현재 simulation은 운영 저장소가 아니며 실제 발행/완료 승계 기능도 없습니다.

향후 승인된 sender는 전송 전에 예약을 영속화하고 timeout/연결 단절은 UNKNOWN으로 남겨
GitHub 상태를 재조회해야 합니다. 불확실한 POST를 자동 재전송하지 않습니다. 응답 run ID가
있으면 사용하고, ID 없는 응답이면 조회와 수동 판정으로 연결합니다. 게시 검증 후에만 완료 처리합니다.
DB 손실·손상 시 자동 신규 DB로 계속 실행하지 않도록 하는 운영 시작 조건도 별도 구현·검증해야 합니다.
현재 읽기 전용 CLI는 DB를 만들지 않습니다.

현재 GET 관찰에는 single-repository Contents read / Actions read 토큰이면 충분합니다.
향후 dispatch에는 해당 repo Actions write가 필요하고 Contents write는 필요 없습니다.
GitHub App 단기 installation token을 우선 검토하고, 차선은 만료일 있는 fine-grained PAT입니다.
Actions write는 특정 endpoint만 허용하는 권한이 아니므로 취소 등 다른 Actions 쓰기도 가능한
권한이라는 한계가 있습니다. 실행기 workflow allowlist·secret 보관·만료·폐기 절차가 필요합니다.
환경변수 이름 `FRESHNESS_WATCHDOG_READ_TOKEN`; 값을 명령행·로그·PR에 넣지 않습니다.
Windows credential store/서버 secret file/서비스 secret을 사용하며 외부 타이머에 PAT를 직접 전달하지 않습니다.
[dispatch 권한](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)

GET timeout 15초/요청, 최대 40회/tick, 응답당 4MB, 페이지당 100개/최대10페이지.
한도 초과·403·429·timeout은 즉시 중단하고 같은 tick에서 재시도하지 않습니다.
5분 점검 가정 최대 480 GET/시간, 11,520/일. 일반 약20~25회/tick이면 240~300/시간이며
실제 run/job 수에 따라 달라집니다. 비인증 60/시간으로는 부족하고 일반 인증 5,000/시간도
동일 계정 사용량과 공유됨을 고려해야 합니다. secondary limit/Retry-After도 운영 sender에 반영해야 합니다.
이번 구현의 거래소 API 호출은 0건입니다. 복구 dispatch마다 기존 전체 시장 수집 비용이 추가되므로
실제 복구 횟수·collector API 요청량을 계측한 후 상한을 승인해야 합니다.
[GitHub rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)

## 로컬 명령과 검증 증거

```powershell
python -B -m freshness_watchdog
# 보관한 관측 JSON으로만 재현; --now는 offline에서만 허용
python -B -m freshness_watchdog --evidence evidence.json --now 2026-10-11T00:40:00Z
python -B -m unittest discover -s tests -p test_freshness_watchdog.py -v
```

CLI는 콘솔에만 출력하며 BLOCKED exit=2, 정상 판단 exit=0입니다.
WOULD_DISPATCH도 호출하지 않습니다. `actual_dispatch_count`는 항상 0입니다.
offline JSON은 합성/보관 증거 입력이며 API 원본에 대한 독립 신뢰 증명을 만들지 않습니다.

신규 테스트 20개: 중복·경합 예약, UNKNOWN/accepted 영속 차단, active run, 최신 게시 미확인,
실패 run, partial/invalid, 시간·revision·config 경계, GET-only/token 비노출, API403/429,
pagination/budget, blob/main 변조 차단, offline CLI 파일 변경 0건.

최초 버전 전체 로컬 회귀: 공통 Python 108(신규20 포함), B/C Python 582,
BTC Python 389, Worker Node 70 = **1,149 PASS / 0 FAIL**.
최초 버전은 Linux CI 미실행이었습니다. 후속 읽기 전용 시험에서 신규 테스트5개와
전용 PR CI를 추가했습니다. 최신 검증 내용은 `READONLY_VALIDATION_V1.md`를 참조하십시오.

최종 원격 재확인 main `2c38a344ed1e537811488aade1b310413659388e`는 기준 이후
16개 Production 데이터 게시 commit이 추가됐습니다. GitHub compare로 코드/workflow 변경이
없음을 확인했습니다. 최신 commit은 2026-10-11T01:16:51Z A 결과 게시입니다.
이는 개발 기준의 STALE replay와 구분하며 watchdog 복구 결과로 주장하지 않습니다.

실제 기준 checkout의 A/B 출력도 읽기 전용 검증했습니다. 미래 고정 시각으로 Production 판정을
주장하지 않고, 아래 freshness는 **2026-10-11T01:10:00Z 기준 replay**입니다.

| 원본 | 바이트 SHA256 | 결과 |
|---|---|---|
| output/latest_scan.json | 02d8f19b2d327ff134cbfa3f1ab881485b21f129783d9b4c44d679d4f474d5ca | 기본 게시 계약 정상, STALE |
| output_upbit_b/v1/history/2026-10-10/23.jsonl | a06f12e05ce3e78d28d6fb526ffffac26f3084266b8a2eedcbbd64752b8cca4b | 기존 전체 cycle 검증 정상, COMPLETE, STALE |

최초 버전의 비인증 실제 GET 관측은 첫 repository 요청에서 HTTP403으로 중단됐습니다. 새 토큰을 만들지 않았으며
이를 정상 관측이나 복구 성공으로 보고하지 않습니다. API 통합 판단 경로는 mock 기반 로컬 테스트입니다.
후속 인증 GET 관측에서는 repository root 끝의 slash로 인한404 결함을 수정하고
완전한 실제 관측을 확보했습니다. dispatch·복구 게시 완료 검증은 계속 미실행입니다.

## Production 적용 전 승인·조건

1. 실행 환경/비용 및 단일 실행 호스트 선택.
2. 먼저 읽기 전용 관찰을 충분히 수행하고 실제 지연·시간대·run/blob 결속과 API 사용량 확인.
3. 제안된 A90분/B:35 정책, cooldown·복구 상한·알림·장애 대응 책임자 승인.
4. 정규 schedule와의 경합/A 오래된 결과 게시 위험을 수용할지, 최소 게시 방어가 추가로 필요한지 결정.
5. 별도 승인 후 sender·완료 조정·영속 상태 손실 대응 구현과 격리 테스트, 실제 제한된 1회 복구 검증.
6. 최소 토큰 발급과 secret 보관, Linux/Windows 배포 및 timer 등록은 각각 승인 후 수행.

main 병합·실제 dispatch·자동 복구 활성화는 이번 작업에서 하지 않습니다.
