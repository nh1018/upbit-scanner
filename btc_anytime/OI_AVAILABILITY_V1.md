# BTC Anytime OI Registry / Availability Evidence V1

이 계층은 Direction Engine이 아니다. production raw/provenance/collector/Worker/Pine/Alert/Upbit A V1.3 및 Feature 계산식은 변경하지 않는다. 단위·source scope 검증과 실제 availability metadata만 추가한다.

## 1. Registry

`features/registries/oi_registry_v1.json`은 content identity가 있는 immutable version이다. 후속 계약 변경은 새 registry version/file을 추가하고 원본을 교체하지 않는다. registry ID와 전체 계약/evidence는 Feature manifest에 고정된다. 코드 anchor bytes 또는 evidence hash가 달라지면 fail-closed로 재검증한다.

| 구분 | Binance historical | TradingView HTF live |
|---|---|---|
| provider / upstream | Binance | TradingView / Binance |
| instrument / market | BTCUSDT perpetual / USD-M Futures | 동일 underlying, provider별 독립 계약 |
| symbol / OI symbol | BTCUSDT / REST symbol parameter | BTCUSDT.P / BINANCE:BTCUSDT.P_OI |
| timeframe | 15m/1h/4h/1d, URL period 일치 필요 | 1h/4h/1d, requested context 및 기간 일치 |
| raw / source field | oi / sumOpenInterest (수치 변환 없음) | oi / OI context close[1] (Pine JSON 표현 외 단위 변환 없음) |
| unit | unavailable: field-specific declaration 미확보 | BTC: exact official symbolInfo currency/currency_code |
| observation basis | source_timestamp_equals_candle_open | confirmed_oi_bar_close_boundary |
| observation time | oi_timestamp_ms = attached kline open | oi_time = attached kline exclusive end |
| source period | API 문서는 timestamp를 period end로 설명. nominal start=t-D/end=t. attached candle t..t+D와 구분 | oi_period_start_ms=time, end_ms=time+D; underlying sampling millisecond는 unavailable |
| source schema | REST API version unavailable, endpoint와 field 명시 | btc-anytime-htf-v1 + tradingview_binance_usdm_htf |
| active status | UNRESOLVED_UNIT, Feature 변화량에 공급하지 않음 | VALIDATED |

공식 근거:
- TradingView exact symbol: https://www.tradingview.com/symbols/BTCUSDT.P_OI/?exchange=BINANCE 의 public `window.initData.symbolInfo`가 `resolved_symbol=BINANCE:BTCUSDT.P_OI`, `provider_id=binance`, `currency=currency_code=BTC`를 반환한다. 추출한 원본 symbolInfo, 취득시각, HTTP response SHA256을 별도 evidence JSON으로 보존한다. 전체 HTTP bytes와 과거 metadata effective-from는 unavailable이다.
- 일반 TradingView 문서는 비집계 OI의 단위가 exchange/derivative에 따라 다를 수 있다고 설명한다: https://www.tradingview.com/support/solutions/43000762388-understanding-crypto-open-interest/ . 따라서 단순 numeric 크기나 일반 설명으로 단위를 추정하지 않는다.
- Binance USD-M API: https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data . sumOpenInterest와 sumOpenInterestValue는 구분되며 timestamp는 period end다. field별 unit annotation은 확인되지 않았다.
- Binance의 BTCUSDT OI 예제는 BTC position quantity를 사용하지만, 이를 REST sumOpenInterest의 field-specific declaration으로 승격하지 않는다: https://www.binance.com/zh-CN/blog/%E5%90%88%E7%BA%A6/421499824684900398 . exchangeInfo baseAsset=BTC도 단독으로 OI field 단위를 증명하지 않는다.

TV 계약은 schema/source/symbol/market/TF/period 및 실제 received field가 있는 기존 HTF 행에만 적용한다. 공식 unit 검증을 이제 확보한 것이므로 OI 변화 feature의 availability도 registry validation time 이전으로 소급되지 않는다. 과거의 provider symbol unit-version 전체 동일성은 증명하지 않으며 current contract 검증 결과를 manifest에 기록한다.

Legacy flat 15m은 Worker legacySafeData가 source/basis/schema를 보존하지 않는다. 현재 Pine을 보고 과거 raw에 basis를 추가하거나 HTF 계약을 15m에 적용하지 않는다. historical unavailable/seed OI, legacy values는 그대로다. basis/provider/instrument/TF/unit 변경, gap/unavailable 시 기존 segment reset을 유지한다. 단위가 같아도 historical-to-live는 reset한다.

## 2. 실제 observation evidence

독립 observer `python -B -m btc_anytime.features.observe --repo .`는 read-only 계획/검증이며 `--record`에서만 metadata를 append한다. Git HEAD를 pin하고 raw의 HEAD diff/row hash를 확인한다. 기존 row 수정이 관측되면 증거를 덮어쓰지 않고 FAIL한다.

파일 namespace:
- metadata_features/btc_anytime/v1/observation_events/{event_id}.json
- metadata_features/btc_anytime/v1/generation_events/{event_id}.json

각 event는 schema version, content hash/ID로 보호하고 exclusive create한다. 기존 event 내용/filename hash가 맞지 않으면 FAIL한다. 관측 완료 후 feature generation 실패한 경우 별도 현재시각 generation event로 재시도하며 과거 시각을 만들지 않는다.

Observation은 candle open/close exclusive, raw ref/canonical row hash, source_received_at, backfill retrieval/OI retrieval, repository commit, 실제 repository_observed_at을 구분한다. Git committed-at은 clock 참고값일 뿐 접근 가능 시각으로 사용하지 않는다. 정확한 GitHub publication instant는 unavailable이다.

초기 inventory는 **이번 observer가 지금 실제 읽었다**는 known-by 사실만 기록한다. 모든 과거 candle의 historical availability는 unavailable이다. seed에 candle close 기준의 가짜 availability를 만들지 않는다. 초기 관측 이전 as-of snapshot에는 이 evidence를 사용할 수 없다. 이후 신규 행은 최초 실제 관측만 추가하고, late backfill도 실제 새 관측시각 이후만 사용할 수 있다. 이미 관측한 행은 새 조회시각으로 덮어쓰지 않는다.

## 3. Feature generation / snapshot

Raw availability=max(completed boundary, original received/retrieved clock, actual consumer observation). 외부 registry unit validation은 OI change/state dependency에 별도 시간 gate로 적용한다. 모든 재귀/rolling/pivot dependency의 실제 available time을 확인한다.

Feature 값을 산출한 뒤 실제 feature_generated_at을 기록하며, feature available=max(dependency availability, registry validation where needed, generation time)이다. snapshot_decision_time은 계산 완료 후 실제 시각이다. 미래 feature generation 또는 late backfill을 과거 T에 넣지 않는다. evidence가 명시적으로 누락된 input은 현재시각 fallback 없이 strict snapshot에서 차단한다.

Generation event는 algorithm/schema version, registry ID, input Git commit, compact manifest reference/file hashes, generation time, snapshot ID/decision time/선택 anchor와 dependency clock/result hashes를 보존한다. raw는 고정 Git revision에서 재현할 수 있다. 전체 feature history를 metadata commit에 중복 저장하지 않는다.

## 4. 자동 실행과 한계

새 독립 `.github/workflows/btc-feature-evidence.yml`만 추가한다. main의 BTC JSONL push 및 매시간 UTC 7/22/37/52분의 보조 schedule, workflow_dispatch를 지원한다. source collector job/Worker/Alert는 변경하지 않는다. metadata-only commit은 path filter를 재호출하지 않는다. GitHub Actions token push가 workflow를 유발하지 않을 수 있으므로 schedule은 보조다: https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow . 실제 관측시각이 상한 증거이며 schedule 지연도 숨기지 않는다.

workflow는 raw read-only observer만 실행하며 새 metadata 파일(A)만 commit한다. 원격 경합은 metadata commit만 rebase/일반 push 재시도하고 force push하지 않는다. 기존 production commit은 재작성하지 않는다. 자동 job 지연/권한 실패 시 원래 raw 수집에는 영향이 없지만 availability event는 나중에 관측한 시각으로만 기록된다.

이 변경의 테스트와 local production dry-run은 자동 Actions runner의 실제 첫 실행을 증명하지 않는다. push 이후 신규 자동 event/commit과 Actions 상태는 별도로 확인해야 한다. Direction Engine은 unit/basis 미확정 OI와 unavailable/stale snapshot 처리 정책을 먼저 정의해야 한다.

## Cross-platform text evidence identity

Registry code anchors and extracted JSON evidence declare `hash_basis=utf8_text_lf`; SHA256 is calculated after CRLF-to-LF conversion only. Official HTTP response SHA256 remains the original byte hash. Feature implementation hashes use the same explicit text basis. The initial local byte-hash registry observation is retained under `registries/observations/27e786c053ca7ebe13debc59d877c99d47d0cecf1c763933105ec4f1dc348934.json`; its original generation event is preserved. A changed registry or implementation generates a new event at the actual new generation time, without altering earlier events.
