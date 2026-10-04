# BTC Anytime Feature Layer V1 설계

상태: 사용자 확정 V1 사양. 구현·검증은 이 계약을 따른다. 작성일: 2026-10-04 KST.
Production cutover 검증 snapshot: Git main 2158af18dad942b182086d46ab85d37906e7a329, 2026-10-04 10:04:24 KST. 이는 당시 snapshot이며 이후 데이터 상태를 영구 보증하지 않는다.

## 1. 역할과 경계

Market Data V1 Production은 TradingView BINANCE:BTCUSDT.P의 confirmed 15m 및 confirmed HTF 데이터를 Cloudflare Worker를 통해 GitHub에 저장한다. 15m/1h/4h/1d 실제 live append와 latest 검증 완료. 검증 snapshot의 총 행 수는 645/385/321/303, 모든 TF의 gap/duplicate/abnormal interval/값 오류 0.

Feature Layer는 immutable raw observation을 재현 가능한 숫자/상태/품질 metadata로 변환한다. LONG/SHORT 점수, 방향, 진입, 포지션, 주문, 성과 판단은 하지 않는다. 기존 collector, Worker, Pine, Alert, Upbit A V1.3, raw/history/latest 및 provenance를 수정하지 않는다. synthetic HTF aggregation은 하지 않는다.

raw candle -> 독립적인 per-timeframe feature -> synchronized feature snapshot -> 이후 Direction Engine 입력.

## 2. 공통 계산 계약

- TF는 15m/1h/4h/1d. 모든 아래 feature는 네 TF에서 각각 계산하며 TF 간 price/volume/OI를 섞지 않는다. bar 수는 해당 TF의 bar 수다.
- Natural key: (BINANCE_USDT_M_FUTURES, BTCUSDT perpetual, timeframe, time UTC open ms). BTCUSDT와 BTCUSDT.P 표기는 metadata로 보존하면서 이 한 market으로 정규화한다.
- t는 현재 완료봉 index, C/O/H/L/V는 해당 봉 값. duration D는 900000/3600000/14400000/86400000 ms. 완료 경계 E_t=time_t+D, exchange close_time_ms=E_t-1. close_time_utc와 수집/게시/취득 시각은 별도다.
- P 계약: OHLC가 유한하고 양수이며 high>=open,close,low 및 low<=open,close. time%D=0, E_t<=known availability/decision time, is_closed=false이면 제외. 원본 legacy의 is_closed 누락은 정확한 timestamp/경계/수신 증거로만 판정하며 추정 수신시각을 만들지 않는다.
- V 계약: volume 유한, 0 이상. OI 계약은 5절. 필요한 필드가 invalid/null이면 관련 feature=null 및 reason을 기록한다. raw는 고치지 않는다.
- 동일 key 중복/충돌이면 입력 fail-closed. 이미 승인된 official backfill과 live의 서로 다른 key를 합쳐 canonical ledger로 사용하되 manifest에 선택된 raw 행과 source를 고정한다. 새 source로 기존 key를 대체하지 않는다.
- 필요한 window에 gap/경계 오류/invalid input이 있으면 관련 feature=null. 재귀 EMA/RSI/ATR는 해당 feature 입력의 contiguous valid segment 시작점부터 다시 warm-up한다. volume/OI 오류로 정상 price feature까지 무조건 폐기하지는 않는다.
- OHLCV source 변화 자체는 가격/volume feature reset 사유가 아니다. window의 source 집합과 source_transition/mixed_sources flag를 보존한다. OI 변화는 별도 엄격 reset 규칙을 적용한다. Binance finalized와 TV raw가 다른 것을 조정/보간하지 않는다.
- Decimal context precision=50, ROUND_HALF_EVEN. 입력 숫자는 원본 십진 표현에서 읽는다. 재귀 상태를 중간 출력 자리수로 반올림하지 않는다. 최종 수치만 소수점 18자리로 반올림해 decimal string으로 저장한다. bool/state는 별도 타입. 라이브러리 기본 seed에 의존하지 않는다.
- denominator=0, 필요한 lookback 부족, 연산 비유한 -> null. null을 0/직전 값으로 채우지 않는다. warm-up 결과는 ready=false다. 아래 최소 bars에는 현재 완료봉을 포함한다.
- 모든 feature는 source manifest, raw row refs/hash, dataset version, feature algorithm version/parameter hash, dependency interval, quality/null reason을 보존한다. None/unavailable와 false/0을 구분한다.

## 3. V1 feature별 정확한 정의

공통 적용: 아래 모든 feature는 네 TF 각각, completed-only, 공통 계산 계약 적용. leakage 규칙 L0=현재 완료/available 입력까지만, L1=현재 이전 bars만 reference, L2=확인된 pivot만 사용. 각 source 계약 P/V/I는 공통 provenance와 5절을 포함한다.

| Feature / parameter | 계산식 | lookback / 최소 bars / warm-up | null/invalid 추가 조건 | source 계약 / leakage |
|---|---|---|---|---|
| return_n, n=1,3,6,12,24 | 100*(C_t/C_(t-n)-1) | n prior bars / n+1 / 없음 | 기준 close 0 또는 window gap/invalid | P / L0 |
| EMA20, EMA50 | alpha=2/(N+1); 최초 EMA_(N-1)=mean(C_0..C_(N-1)); 이후 alpha*C_t+(1-alpha)*EMA_(t-1) | contiguous segment 전체 재귀 / N / 첫 N봉 SMA seed | segment가 N 미만 | P / L0 |
| EMA20_slope_pct_per_bar, EMA50_slope_pct_per_bar | 100*(EMA_N(t)/EMA_N(t-3)-1)/3 | 재귀 상태 + 3 EMA lag / N+3 / lag 3개 필요 | lag EMA=0/미준비 | P / L0 |
| close_to_EMA20_pct, close_to_EMA50_pct | 100*(C_t-EMA_N,t)/EMA_N,t | EMA_N와 동일 / N | EMA=0/미준비 | P / L0 |
| RSI14 | d_i=C_i-C_(i-1), gain=max(d,0), loss=max(-d,0); 최초 G/L=mean(gain/loss_1..14); 이후 (13*prev+current)/14; RSI=100-100/(1+G/L) | segment 전체 재귀 / 15 / 14 close changes seed | G=L=0 -> 50; L=0,G>0 -> 100; G=0,L>0 -> 0; invalid window -> null | P / L0 |
| ATR14 | TR_i=max(H_i-L_i,abs(H_i-C_(i-1)),abs(L_i-C_(i-1))); i>=1; 최초 ATR=mean(TR_1..14), 이후 (13*ATR_prev+TR_t)/14 | segment 전체 재귀 / 15 / TR_0은 unavailable | TR/이전 close invalid | P / L0 |
| ATR14_pct | 100*ATR14_t/C_t | ATR14와 동일 / 15 | C_t=0/ATR 미준비 | P / L0 |
| volume_MA20 | sum(V_(t-19)..V_t)/20 | 현재 포함 20 / 20 / 재귀 없음 | volume invalid | V / L0 |
| volume_ratio20 | V_t/volume_MA20_t | 현재 포함 20 / 20 | MA20=0 -> null, 현재 V=0이고 MA>0이면 0 | V / L0 |
| OI_absolute | 원본 oi 숫자 그대로; 단위/known basis 표시 | 0 prior / 1 | OI invalid/null/negative -> null; 단위 확인 안 됨은 unit_status=unavailable, 숫자를 계약 수로 해석하지 않음 | I / L0 |
| OI_change_1bar | OI_t-OI_(t-1) | 1 prior / 같은 basis segment 2 | basis/unit/source/instrument/TF signature 다름, gap 또는 unavailable -> null | I / L0 |
| OI_change_pct_1bar | 100*(OI_t/OI_(t-1)-1) | 같은 basis segment 2 | 이전 OI=0 -> null; change도 segment 규칙 적용 | I / L0 |
| price_OI_state | price=return_1=100*(C_t/C_(t-1)-1), OI delta=OI_change_1bar; 정확한 부호의 9개 조합 PRICE_{UP/DOWN/FLAT}_OI_{UP/DOWN/FLAT}; threshold 없음 | prior 1 / 2 | OI_change가 null이면 state=null; price source가 transition이면 state=null(source_transition), 다음 동질 pair부터 재개 | P+I / L0; LONG/SHORT 의미 부여 금지 |
| rolling_high_20/50 | max(H_(t-N)..H_(t-1)) | N prior / N+1 | reference window gap/invalid | P / L1 |
| rolling_low_20/50 | min(L_(t-N)..L_(t-1)) | N prior / N+1 | reference window gap/invalid | P / L1 |
| breakout_20/50 | C_t > rolling_high_N (strict bool) | N prior / N+1 | reference null -> null; 같으면 false | P / L1 |
| breakdown_20/50 | C_t < rolling_low_N (strict bool) | N prior / N+1 | reference null -> null; 같으면 false | P / L1 |
| distance_to_rolling_low_20/50_pct | 100*(C_t-rolling_low_N)/rolling_low_N | N prior / N+1 | rolling low=0/null -> null; 하회 시 음수 | P / L1; 실제 지지/저항 해석 없음 |
| distance_to_rolling_high_20/50_pct | 100*(rolling_high_N-C_t)/rolling_high_N | N prior / N+1 | rolling high=0/null -> null; 상회 시 음수 | P / L1 |
| structure_high_state / structure_low_state (HH/HL/LH/LL) | 4절의 확정 pivot pair 비교 | strict L2/R2 / pivot 하나 최소 5봉 / 동종 confirmed pivot 2개 | 확정 pivot pair 부족/gap -> null | P / L2 |

EMA slope 식의 t-3은 bar index lag이며 정규화 결과의 단위는 %/bar다. returns/거리/변화율은 percent, volume_ratio는 배수, EMA/ATR는 price 단위다. OI absolute/change 단위는 원천 metadata로만 표시한다.

## 4. Causal HH/HL/LH/LL

contiguous valid price segment에서 pivot center j의 좌우 2개 봉을 비교한다. High pivot은 H_j가 H_(j-2),H_(j-1),H_(j+1),H_(j+2) 각각보다 strict하게 클 때. Low pivot은 대응 low 4개보다 strict하게 작을 때. 동률은 pivot이 아니다. 200봉 readiness나 만료 window는 없다.

pivot j는 j+2 봉 완료 이후에만 계산된다. pivot_time은 중심봉 open UTC, confirmation_candle_time은 j+2 open UTC, confirmed_at은 다섯 dependency의 실제 availability 최대시각이다. confirmation_close_exclusive도 별도로 보존한다. availability 미확보 시 confirmed_at=null이며 strict snapshot 사용을 차단한다. 마지막 두 확정 high pivot: HH/LH/EQ, low pivot: HL/LL/EQ. 동종 2개 부족 시 null. 최신 pivot과 pair refs를 각 시점에 보존하고 과거 j feature/snapshot에 소급하지 않는다. price gap/invalid 시 pivot segment를 재시작한다.

## 5. OI basis 경계 정책

Historical Binance: source=binance_usdm_open_interest_hist, oi_timestamp_ms=candle open, alignment=source_timestamp_equals_candle_open.
TradingView live: source=tradingview_binance_usdm_htf, oi_time=period exclusive end, basis=confirmed_oi_bar_close_boundary, matching oi_period_start/end 필요. 이는 공급자의 실제 sampling millisecond를 증명하지 않는다.
Legacy 15m은 OI 숫자가 있어도 basis가 명시되지 않은 행은 legacy_unspecified다. 새로 metadata를 만들어 raw에 쓰지 않는다. seed OI 미기록/unavailable도 그대로다.

OI segment signature=(provider family, instrument, timeframe, observation basis, unit status/unit). URL/response hash 변화만으로 segment를 끊지는 않지만 provenance는 모두 유지한다. historical open과 TV confirmed close 사이, legacy_unspecified와 명시 basis 사이, 서로 다른 provider/단위 사이, timestamp gap 또는 unavailable 사이에서 OI change/change%/price_OI_state를 null로 하고 새 segment를 시작한다. 첫 행 absolute만 가능, 두 번째 동일한 검증 basis/단위 행부터 변화량 가능하다.

OI 단위가 ledger에 없는 경우 V1 metadata는 oi_unit=unavailable로 보존한다. absolute는 raw 수치로 표시 가능하지만 OI change/change%의 ready=false(unit_unverified)로 한다. provider 문서/사용자 검증으로 instrument별 단위가 확인된 경우에만 versioned external metadata registry에 근거를 추가하고 change 계산을 허용한다. 같은 숫자 크기만으로 단위를 추정하거나 historical/live를 동일 basis로 취급하지 않는다. 후속 알고리즘 버전이 과거 raw를 수정하지 않는다.

## 6. 결과 schema와 provenance

per-timeframe record 실제 schema:
- schema_version/algorithm_version/parameter_hash/dataset_manifest_id/result_hash.
- market/instrument/timeframe/time/candle_time_utc/candle_close_exclusive_utc.
- raw_ref: input Git commit, file/line/time 및 원본 JSONL row SHA256. manifest에 file hashes와 time순 canonical row hashes.
- available_at_ms 및 availability_evidence(kind/ref/observed_at_ms); evidence 없으면 available_at_ms=null.
- input_validity: boundary/price/volume/OI 구분.
- features: named decimal string/bool/state/pivot object/null (총 36개).
- feature_quality: ready/null_reason/dependency_start_index/end_index/available_at_ms/sources/mixed_sources/source_transition. structure의 pivot_pair는 두 pivot value/time/confirmation refs.
- oi_metadata: signature/segment_id/null_reason/raw_basis/raw_observation_time/raw_unit/unit_status/registry_hash/registry_evidence.
- 실행의 observed_at_utc는 dry-run envelope에 기록하며 generated-at을 candle/availability로 대체하지 않는다.

수치는 예시값으로 채우지 않는다. 동일 input manifest+algorithm+parameters는 동일 수치와 deterministic metadata를 생성한다. 실행 시각은 계산 결과와 분리한 run envelope에 두며 semantic result hash에는 넣지 않는다. 원본 decimal/record hash와 버전이 없는 단일 latest는 재현 가능한 입력으로 충분하지 않다.

## 7. synchronized snapshot과 실제 availability

decision time T는 UTC로 명시된 Feature consumer의 결정 시각이다. 기준 15m event를 소비할 때 생성할 수 있지만 candle close와 T를 동일시하지 않는다. 각 TF에서 completed AND 실제로 알려져 사용 가능했던 candle 중 가장 최신 open key의 feature를 선택한다. 부족한 TF를 미래 candle이나 진행봉으로 채우지 않는다.

known_available_at=max(candle exclusive close, trustworthy original observation time, consumer의 first observed GitHub publication time). live received_at만으로 GitHub durable 저장 시각을 가정하지 않는다. Git committer timestamp는 clock evidence로 저장할 수 있지만 실제 접근 가능 시각의 정확한 증명으로 단독 채택하지 않는다. consumer가 최초 조회한 시각은 conservative known-by evidence이며 별도 append-only availability record에 저장한다. historical retrieval+최초 publication observation도 같은 규칙으로 다룬다.

현재 raw에 publication observation이 없는 경우 해당 필드를 unavailable로 두고 strict historical as-of replay에서 제외한다. 실시간 consumer 최초 조회 시점 이후부터 사용 가능하다. 기존 seed/backfill을 과거 candle close 때 이미 알고 있었다고 가정하지 않는다. nominal close만 사용하는 별도 finalized-reference 연구 모드는 live replay와 이름/manifest를 분리하고 실전 성과로 제시하지 않는다.

각 feature 자체의 available_at은 raw anchor와 모든 dependency/recursive state input의 known availability 최댓값이다. 동일 anchor가 있더라도 늦게 들어온 보충 행으로 계산한 새 feature version은 그 보충 행이 알려지기 전 snapshot에 사용하지 않는다. 과거 snapshot/feature를 overwrite하지 않고 새 manifest/run으로 기록한다.

snapshot schema 제안: snapshot_id, decision_time_utc, decision-event ref, input manifests, per-TF selected row/version/close/available time/age, features, freshness status, missing TF reasons, readiness. 조건: 모든 selected feature.available_at<=T, selected candle close<=T. latest candle에 feature warm-up이 안 됐다고 조용히 오래된 ready candle을 선택하지 않는다. 최신 eligible candle을 선택하고 해당 feature는 null로 남긴다.

Freshness는 T-candle exclusive close로 계산한다. V1 기준 max age=D+16분(15분 confirmed 전달 지연+1분 운영 allowance); 초과하면 stale=true/ready=false, 값은 감사용으로 보존한다. OHLCV/OI 값에 시간 보간은 하지 않는다. HTF의 15분 지연은 명시적으로 반영하며 이 기준은 versioned parameter다. Direction Engine은 향후 missing/stale/null 처리를 별도로 정의한다.

## 8. Future leakage 차단

1. per-TF 계산은 해당 raw manifest의 t 이하 완료·available 입력만 읽는다.
2. reference rolling high/low는 t를 제외한다.
3. swing은 우측 2봉 확인 이후만 사용하고 확인시각 이전 feature를 만들지 않는다.
4. snapshot은 nominal close뿐 아니라 각 dependency의 known availability를 확인한다.
5. 원래 없던 backfill을 과거 snapshot에 삽입하지 않는다. source audit에 finalized reference를 사용해도 그것을 당시 실전 관측으로 바꾸지 않는다.
6. 현재 다른 TF의 진행 중 봉이나 미래 HTF final 값을 security/reindex/forward-fill로 가져오지 않는다.
7. centered window, negative shift, future return/label, 전체기간 scaler/normalization fit, bfill은 V1에 없다.
8. 출력 버전/manifest/hash를 고정하고 suffix 데이터 추가가 이전 시점 feature를 바꾸지 않는지 검사한다. gap 보충으로 달라지는 경우 새 availability와 새 버전을 명시한다.

## 9. 독립 구현/출력 구조

- btc_anytime/features/engine.py: input/Decimal/quality 계약, 순수 indicators, causal pivots, OI segments/registry 근거.
- btc_anytime/features/snapshot.py: causal as-of synchronization.
- btc_anytime/features/build.py: read-only raw loader, manifest builder, exclusive output writer, dry-run CLI.
- btc_anytime/tests/test_features.py: formula/causality/provenance/immutability regression tests.

선택적 출력 위치: data_features/btc_anytime/v1/{manifest_id}/manifest.json, {tf}/features.jsonl, snapshot.json. write_derived()는 유효한 manifest/result hash를 검사하고 새 namespace를 exclusive 생성한다. 같은 namespace를 덮어쓰지 않는다. CLI는 쓰기 option 없이 stdout dry-run만 제공한다. production raw/latest 경로는 사용하지 않는다. availability evidence는 해당 immutable manifest에 consumer_first_observed fact로 고정된다. 지속 운용의 availability event registry/스케줄링은 별도 승인 전까지 활성화하지 않는다.

실행: 저장소 root에서 python -B -m btc_anytime.features.build --repo D:/repos/upbit-scanner. Python 표준 라이브러리만 사용한다. Feature 자동화는 만들지 않는다. record의 result_hash, snapshot_id 및 input Git commit/file/row hashes로 재현 근거를 보존한다.

## 10. 테스트 계획

- 수작업 exact fixture: return 각 lag, EMA seed/recursion/slope, RSI 상승/하락/flat, ATR gap range, volume zero/all-zero, 각 distance 부호.
- n-1/n/n+1 warm-up; reset 후 재시작; null/NaN/Infinity/negative/OHLC invalid; Decimal representation 차이와 round-half-even.
- 4 TF 독립성: 다른 TF 변경이 per-TF 결과를 바꾸지 않음. 동일 manifest 재실행의 semantic hash 동일.
- OI historical open -> TV close / legacy unknown / provider·unit 변경 / null / zero / timestamp gap 모두 경계 변화량 null; 정확한 homogeneous pair만 계산.
- pivot strict ties, j+2 확인 전후, suffix append 불변, gap restart, 두 pivot 미존재.
- rolling reference가 현재 봉을 제외하고 breakout equality=false인지 검증.
- as-of T 직전/동일/직후 completion·availability, midnight/동시 HTF 종료, 15분 전달 지연, late backfill, reader observation unavailable, stale, latest anchor warm-up 미준비.
- 과거 snapshot에 미래 suffix와 늦은 backfill이 유입되지 않는 metamorphic test; 연구 mode와 strict replay 혼용 거부.
- raw/production 파일 bytes 및 원천 row hashes 보존; worker/Pine/15m production regression. 단위 검증 증거 없으면 OI change readiness 차단.

## 11. 의도적 제외

LONG/SHORT score, Direction/Entry rules, leverage/position sizing, 주문·자동매매, 미래 return labels/성과 평가는 다음 계층이다. MACD/Bollinger/ADX/Stochastic/Ichimoku는 V1의 기본 trend/momentum/volatility와 중복되어 초기 범위에서 제외한다. VWAP/session anchor는 24/7 시장의 anchor와 quote-volume 계약이 미정이므로 제외. Funding/liquidation/order-book/on-chain/sentiment는 전 TF의 확정 raw 계약이 없어 제외. ZigZag/unconfirmed pivot, OI interpolation, synthetic HTF, ML/학습 normalization도 제외한다.

## 12. 향후 Direction/Entry 인터페이스

FeatureSnapshotV1은 immutable snapshot_id, decision_time, 선택된 4 TF anchor/close/availability, feature values, per-feature null/ready reasons, freshness, source window/OI signatures, algorithm/manifest hashes를 Direction Engine에 전달한다. 여기에는 direction/score/order 필드가 없다. Direction Engine은 snapshot_id를 자신의 출력에 참조하고 missing/stale/unknown-unit 처리 및 전략 기준을 별도 버전으로 정의한다. Feature Layer가 그 기준을 대신 결정하지 않는다.

Entry Timing Engine은 별도 Direction Engine 결과 reference와 그 결정시각 이후 사용 가능한 최신 FeatureSnapshotV1을 소비한다. 다른 decision time의 결과를 혼합하면 reference/age를 반드시 표시한다. 방향 판단과 timing은 feature 산출과 분리한다. Signal History는 입력 snapshot/engine version을 참조하여 재현성을 보존하고, Performance Evaluation의 미래 수익 label은 Feature 입력으로 역류하지 않는다. 주문 실행 계약은 이번 설계 범위가 아니다.

## 13. 구현 순서

1. 사용자 설계 검토, feature 이름/parameters/Decimal/availability/OI unit 계약 확정.
2. immutable input manifest와 quality gate/availability registry.
3. returns/EMA/volume 및 deterministic seeds 테스트.
4. RSI/ATR와 null/warm-up 테스트.
5. OI 단위 근거 확보 및 segment/state. 단위 미검증 시 OI change는 계속 null.
6. rolling bounds/breakout/distance와 causal structure.
7. per-TF versioned feature serialization.
8. synchronized snapshot 및 strict as-of/leakage 테스트.
9. read-only production dataset dry-run과 독립 output 검증.
10. Feature Layer 승인 후 Direction Engine -> Entry Timing Engine -> Signal History -> Performance Evaluation.

이 사양의 구현은 production 자동화/설정을 변경하거나 Feature 실행을 자동 활성화하지 않는다.

## 14. 구현 고정 계약

실제 코드: features/engine.py (계약·indicators·pivot·OI), features/snapshot.py (as-of), features/build.py (loader/manifest/read-only CLI/exclusive writer). schema/algorithm/manifest 버전은 각각 btc-feature-v1/1.0.0/1.0.0. feature key는 return_1/3/6/12/24, ema_20/50, ema_20/50_slope_3_pct_per_bar, price_to_ema_20/50_pct, rsi_14, atr_14, atr_pct, volume_ma_20, volume_ratio_20, oi_absolute/change/change_pct, price_oi_state, pivot_high/low, structure_high/low_state, rolling_high/low_20/50, breakout/breakdown_20/50, distance_to_rolling_high/low_20/50_pct다.

OI registry entries는 provider/instrument/timeframe/basis/unit 및 evidence를 모두 포함해야 한다. raw에 unit이 없으면 registry 없이 변화량을 계산하지 않는다. OHLCV provenance/raw refs는 input manifest와 dependency index range로 추적한다. 모든 재귀 feature의 dependency는 segment 시작부터 현재까지다. 각 feature quality는 dependency refs를 manifest에 연결하고 available_at을 따로 기록한다. future suffix 불변성은 값·품질·pivot/OI state에 적용하며 전체 dataset manifest ID는 suffix 추가 시 새 버전이 된다.

현재 조회로 생성되는 availability는 consumer_first_observed_at이라는 conservative known-by 증거다. 과거 received/retrieved timestamp만으로 historical replay를 허용하지 않는다. snapshot은 anchor 자체가 T에 unavailable이면 이전 eligible anchor를 선택하고, 선택된 anchor의 feature dependency가 unavailable이면 해당 feature만 null 처리한다. 단위 미검증 OI 및 warm-up null은 정상 계약 결과이며 malformed input/duplicate/protected-byte 변경은 dry-run FAIL이다.

Snapshot에 같은 candle의 여러 manifest 버전이 공급되면 T에 available한 버전 중 (open time, availability, manifest ID) 순서로 deterministic하게 선택한다. snapshot의 input_feature_result_hash는 원본 feature record를 참조하며, availability 때문에 마스킹한 snapshot 값은 별도의 snapshot_id로 보호한다.
