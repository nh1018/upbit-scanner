# BTC Anytime Direction Engine V1 — 확정 구현 사양

이 문서는 기존 설계와 사용자의 Pullback / Possible Reversal / OI confidence 수정 승인을 반영한다. 모든 가중치와 threshold는 **V1 initial hypothesis parameter set**이며 수익성이 검증된 전략값이 아니다. Direction은 진입 지시가 아니다. 실제 진입 가격/trigger, SL/TP, leverage, position sizing, RR는 Entry/Risk Engine의 책임이다.

## 1. 경계 및 TF 구조

Feature V1 synchronized snapshot → 계약/availability 검증 → TF별 component → multi-TF score → direction/confidence/regime → Entry Engine.

| TF | 역할 | weight |
|---|---|---:|
| 1D | Macro Context | 0.10 |
| 4H | Primary Direction | 0.65 |
| 1H | Confirmation | 0.25 |
| 15m | Context only | 0 |

Volume은 Participation/Confidence, OI는 Derivatives Confirmation/Conflict이며 방향 score에 가산하지 않는다. 15m score는 설명용으로 계산하지만 방향/confidence/regime에는 영향이 없다.

## 2. 계약 및 parameter

`direction/parameters/initial_hypothesis_1.json`에 모든 수치와 정책을 별도 관리한다. canonical JSON SHA256 `parameter_hash`, schema/version을 검증한다. 후속 조정은 새 파일/version으로 추가하고 이전 parameter와 decision을 수정하지 않는다.

Feature의 schema/algorithm/parameter hash, market/instrument/TF, completed boundary, actual observation evidence, feature generation evidence, per-feature dependency clocks, snapshot hash를 검증한다. Feature freshness 정책 duration+960000ms를 그대로 상속하고 stale flag를 맹신하지 않고 decision time 기준 재검증한다.

## 3. 정확한 component 계산

clip(x)=min(1,max(-1,x)); sign은 정확한 0 기준. Decimal precision50/ROUND_HALF_EVEN, 출력18자리. percentage 입력은 기존 Feature의 퍼센트 단위를 그대로 사용한다.

Trend:
- spread=clip((EMA20-EMA50)/ATR14)
- slope20=clip((EMA20*slope20_pct_per_bar/100)/(0.10*ATR14))
- slope50=동일한 EMA50 식
- Trend=0.50*spread+0.25*slope20+0.25*slope50

기존 slope가 이미 per-bar이므로 다시 3으로 나누지 않는다. EMA/ATR 양수와 모든 trend 입력 필요.

Structure:
- high: HH=1/LH=-1/EQ=0; low: HL=1/LL=-1/EQ=0
- pivot_structure=(high+low)/2
- range_event: breakout20=1/breakdown20=-1/둘다false=0
- Structure=0.75*pivot_structure+0.25*range_event
- 두 pivot classification과 range flags 모두 ready여야 계산. 부족하면 null, EQ/neutral로 변환하지 않는다. 두 range flags가 동시에 true이면 invalid.

Momentum=clip(return6/(2*ATR%)); ATR%>0 필요.
Participation=min(1,volume_ratio20/2); ratio>=0 필요. 점수가 아닌 참여 지표다.
Derivatives는 validated signature/segment의 OI change/change%/price×OI raw state만 사용한다. absolute만으로 방향/동의를 계산하지 않는다.

S_tf=0.55*Trend+0.30*Structure+0.15*Momentum.
Structure null은 기여0으로 기록하되 missing이며 가중치를 재분배하지 않는다. Trend/Momentum 중 하나라도 null이면 TF score unavailable.

같은 정보를 중복 가산하지 않기 위해 RSI14, 다른 return lag, rolling50, price-to-EMA, breakout50은 별도 방향 투표로 추가하지 않는다. 데이터는 Feature Layer에 유지한다. 실제 support/resistance, failed breakout, 시간경로 기반 해석은 V1에서 제외한다.

## 4. Direction 및 gates

score=0.65*S4H+0.25*S1H+0.10*S1D. unavailable1D는 기여0; 분모/가중치 재분배 없음. 4H/1H score가 unavailable이면 score=null, NEUTRAL, INSUFFICIENT_EVIDENCE.

- LONG: score>0.35 and S4H>=0.25
- SHORT: score<-0.35 and S4H<=-0.25
- exact ±0.35는 NEUTRAL
- STRONG: 방향성 score>=0.65, 방향성 S4H>=0.60, 방향성 S1H>=0.35
- STRONG 추가: 4H/1H Structure가 각각 방향성>=0.50, fresh1D available and 방향성 S1D>-0.25, confidence>=0.75
- STRONG 미충족은 LONG/SHORT 유지
- |score| 0.30..0.40에 THRESHOLD_PROXIMITY 기록; stateful hysteresis 없음

**반대 1H만으로 NEUTRAL을 강제하는 gate는 제거한다.** 강한 4H와 1D 배경으로 합산 조건이 유지되면 LONG/PULLBACK 또는 SHORT/PULLBACK을 허용한다. Direction과 Regime은 독립 필드다.

## 5. Regime

primary=sign(S4H), 단 |S4H|>=0.25일 때만 primary 존재. stateless인 V1에서 '기존 방향'은 해당 snapshot의 이 primary를 뜻하며 이전 판단을 추정하지 않는다.

4H weakening evidence(확인 가능한 조건만):
- primary*Trend4H<0.50, OR
- Structure4H가 available and primary*Structure4H<=0, OR
- primary*Momentum4H<0.

POSSIBLE_REVERSAL에는 모두 필요:
1. primary*S1H<=-0.25
2. primary*pivot_structure1H<=-0.50
3. range_event1H=-primary
4. 위 weakening evidence 최소1개.

Structure null은 neutral 약화 증거로 간주하지 않는다. RSI/이전 score 등을 이용해 시간상 감소를 추정하지 않는다. 약화는 현재 support criterion 미충족이라는 정확한 의미다. POSSIBLE_REVERSAL은 확정 반전이 아니다.

PULLBACK_CANDIDATE: |S4H|>=0.50, primary*S1H<=-0.25, primary*Trend4H>0, primary*pivot_structure4H>0. Direction LONG/SHORT 여부와 독립이다.

ALIGNED_TREND: |S4H|,|S1H|>=0.25 and 부호 일치.
CHOP_CANDIDATE: 두 TF |score|<0.25, |Trend|<0.25, 유효 pivot_structure=0, range_event=0.
TRANSITION: 4H primary 부재에 1H 유의 방향, TF 반대, 또는 Trend/Structure 반대이며 위 context에 해당하지 않는 경우.
나머지 MIXED.
우선순위: INSUFFICIENT → POSSIBLE_REVERSAL → PULLBACK → ALIGNED → CHOP → TRANSITION → MIXED.

## 6. Confidence 및 optional OI

confidence=0.35*coverage+0.25*alignment+0.25*data_quality+0.15*component_agreement. 성공 확률이 아니다.

C_tf=0.45*I(Trend)+0.30*I(Structure)+0.15*I(Momentum)+0.05*I(Participation)+0.05*I(Derivatives). coverage=ΣTFweight*C_tf.

data_quality: fresh이며 price/volume/boundary validity를 통과하면1, 아니면0. TF weight 합산.

alignment: LONG/SHORT 방향과 TF score 부호 일치1, exactscore0은0.5, 반대/unavailable0. NEUTRAL은 유효 TF의 |score|<0.25이면1, 그 외0. 고정 TF weights.

Core agreement: TF/component weights로 합산. LONG/SHORT과 부호 일치1, exactcomponent0은0.5, 반대/missing0. NEUTRAL은 |component|<0.25이면1, 그 외0. Core denominator=1로 고정.

OI available 보조동의:
- 가격 방향이 final bias와 같고 OI UP:1
- 가격 방향이 같고 OI DOWN:0.5
- 가격 반대 또는 FLAT:0
- OI weight=TFweight*0.05

component_agreement=(core_agreement+Σavailable_OI_weight*OI_agreement)/(1+Σavailable_OI_weight).
**OI unavailable은 분자/분모에서 모두 제외. coverage optional0.05 미충족으로만 반영.** 동일 동의조건에서 OI absence 때문에 agreement가 낮아지지 않는다. OI는 Direction score를 변경하지 않는다. raw 상태에 청산/숏커버 등의 해석을 붙이지 않는다.

필수 4H/1H 부족이면 confidence 최대0.25. 각 confidence 항과 OI 동의/충돌을 출력한다.

## 7. Null, availability, leakage

snapshot decision T에서 실제 사용 가능했던 completed candle만 사용한다. source received, repository observed, feature generated, dependency available을 구분한다. 늦은 backfill을 과거 snapshot에 소급 사용하지 않고 seed의 과거 availability를 만들지 않는다. numeric OI만으로 validated segment를 만들지 않는다. stale/missing OI는 forward-fill하지 않는다.

새 raw를 포함하는 read-only dry-run은 실제 조회 event를 메모리에서만 생성하고 결과에 첨부한다. 이를 영속 event라고 주장하지 않으며 어떠한 metadata/raw/feature output도 쓰지 않는다. 생성 완료 후 실제 generation time과 decision time을 기록한다. 저장된 과거 evidence가 없는 시점에 대해 ready를 추정하지 않는다.

## 8. 결과 / append-only

`btc-direction-v1`: decision time/snapshot ID, engine version, parameter version/hash/status, input manifest IDs, availability refs, feature generation times, OI registry hashes, direction score/class/bias, confidence 및 구성요소, 독립 regime, TF/component별 score 및 기여, actual feature evidence/quality/ref, supporting/conflicting TF/components, missing/stale inputs, primary/reason codes, strong gate 결과, decision ID.

동일 snapshot+parameter는 동일 decision_id. 계산 시 wall-clock을 읽지 않는다. 독립 serialized 기여값의 18자리 rounding residual도 기록하여 score를 재합산할 수 있다. unavailable score는 null이며 0인 중립 score와 구분한다.

`output_direction/btc_anytime/v1/decisions/{decision_id}.json`: exclusive create+fsync; 동일 기록 replay는 no-op, 다른 내용/손상은 FAIL. 원본/기존output namespace overwrite 금지. 이 단계에서는 production decision 파일이나 자동 Direction workflow를 생성하지 않는다.

Reason namespace: INPUT.*, OI.*, DIRECTION.*, CONFLICT.*, REGIME.*. TF별 reason과 evidence를 연결한다. OI FLAT을 confirmation이라고 표기하지 않는다.

## 9. 성능 평가와 향후 단계

Decision 기록과 evaluation labels는 별도 append-only다. +1H/+4H/+12H/+24H, MFE/MAE는 평가 source/anchor/horizon을 먼저 고정한다. 이미 종료된 candle close를 실제 체결가로 가정하지 않는다. decision 후 실제 관측된 가격에 대한 평가를 별도로 정의한다. NEUTRAL/confidence/regime/OI availability별 분석, 중첩표본 관리, 시간 순서 validation을 수행한다. 미래 labels는 Direction 입력에 제공하지 않는다.

TF/component weight, slope0.10ATR, momentum2ATR, 방향0.35/strong0.65, regime/weakening threshold와 confidence weights는 signal history의 민감도/ablation/분리 검증으로 평가한다. ML/자동튜닝/진입/SLTP/레버리지/Funding/order book은 제외한다.

## 10. 검증

부호대칭, threshold 정확한 경계, pullback 방향유지, reversal의 4H약화필수, OI missing 이중penalty방지, Structure null/neutral, daily unavailable, confidence 독립성, replay, future suffix judgment invariance, late backfill, append-only, 실제 기여/reason 일치, 원본 immutability, 기존202 regression 및 production read-only dry-run을 필수로 한다.

전체 dataset manifest/snapshot identity는 미래 suffix 포함 여부에 따라 다를 수 있다. 동일 decision-time에 이용 가능한 과거 feature와 방향/score/confidence/regime 판단이 불변이어야 하며, 변경된 전체 dataset identity를 같다고 주장하지 않는다.

## 11. Production read-only dry-run sample

실제 조회·feature 생성·Direction 생성 시각은 아래와 같이 별도로 기록한다. Pure decision에는 wall-clock을 삽입하지 않아 deterministic replay를 유지한다. 이 샘플은 설계 문서의 검증 기록이며 production signal ledger가 아니다. 새 raw 조회 event는 메모리에만 존재하며 persistent evidence라고 주장하지 않는다.

```json
{
  "status": "PASS",
  "repository_commit": "474d30ef20e99c1071f8c2770c606017de9025a9",
  "repository_observed_at_utc": "2026-10-04T03:40:00.169Z",
  "feature_generated_at_utc": "2026-10-04T03:40:02.723Z",
  "snapshot_decision_time_utc": "2026-10-04T03:40:03.585Z",
  "direction_generated_at_utc": "2026-10-04T03:40:03.605Z",
  "raw_integrity": {
    "15m": {
      "rows": 655,
      "duplicates": 0,
      "missing_slots": 0,
      "boundary_violations": 0,
      "abnormal_intervals": 0,
      "invalid_rows": 0
    },
    "1h": {
      "rows": 388,
      "duplicates": 0,
      "missing_slots": 0,
      "boundary_violations": 0,
      "abnormal_intervals": 0,
      "invalid_rows": 0
    },
    "4h": {
      "rows": 321,
      "duplicates": 0,
      "missing_slots": 0,
      "boundary_violations": 0,
      "abnormal_intervals": 0,
      "invalid_rows": 0
    },
    "1d": {
      "rows": 303,
      "duplicates": 0,
      "missing_slots": 0,
      "boundary_violations": 0,
      "abnormal_intervals": 0,
      "invalid_rows": 0
    }
  },
  "written_files": [],
  "protected_files_unchanged": true,
  "feature_files_unchanged": true,
  "decision": {
    "decision_id": "b47791f0fe1260fc976dc414e509de678b10d6194474185428d8a64920f4935d",
    "snapshot_id": "176c5be86114c142b5720e15ba412f106793b25968067478be8e6d7a23561881",
    "parameter_hash": "825a53ad1c2276a1596abe29e3456fa0823fdd426488c79a999e3b7f6b52ca18",
    "direction_class": "LONG",
    "direction_score": "0.454379840530001423",
    "confidence": "0.989637559808612440",
    "confidence_components": {
      "coverage": "0.995000000000000000",
      "alignment": "1.000000000000000000",
      "data_quality": "1.000000000000000000",
      "component_agreement": "0.942583732057416268"
    },
    "regime": "ALIGNED_TREND",
    "reason_codes": [
      "15m:OI.BASIS_UNVERIFIED",
      "1d:OI.UNAVAILABLE",
      "1h:OI.AVAILABLE_NO_CONFIRMATION",
      "4h:OI.OPPOSING_PRICE_MOVE",
      "DIRECTION.PRIMARY_LONG",
      "REGIME.ALIGNED_TREND"
    ],
    "strong_gate": {
      "aggregate": false,
      "primary": false,
      "confirmation": false,
      "structure": true,
      "macro": true,
      "confidence": true
    },
    "aggregate_rounding_residual": "-0.000000000000000001",
    "tf_summary": {
      "15m": {
        "score": "0.246634840078245489",
        "components": {
          "trend": "0.059344071749150485",
          "structure": "0.750000000000000000",
          "momentum": "-0.073362662558581853",
          "participation": "0.222500339208217142",
          "derivatives": null
        },
        "candle_time_utc": "2026-10-04T03:15:00.000Z",
        "reason_codes": [
          "OI.BASIS_UNVERIFIED"
        ]
      },
      "1h": {
        "score": "0.269438641954039754",
        "components": {
          "trend": "0.034652859005271304",
          "structure": "0.750000000000000000",
          "momentum": "0.169197130007603582",
          "participation": "0.208645207968452546",
          "derivatives": {
            "state": "PRICE_FLAT_OI_UP",
            "change": "71.460000000000000000",
            "change_pct": "0.073653968317806340",
            "segment_id": "801b1469c03cb64d9e662628c93b8d76ebbe46dda3208131e40930535f977d1e",
            "signature": {
              "provider": "tradingview",
              "instrument": "BTCUSDT_PERPETUAL",
              "timeframe": "1h",
              "basis": "confirmed_oi_bar_close_boundary",
              "unit": "BTC"
            }
          }
        },
        "candle_time_utc": "2026-10-04T02:00:00.000Z",
        "reason_codes": [
          "OI.AVAILABLE_NO_CONFIRMATION"
        ]
      },
      "4h": {
        "score": "0.509360701106579234",
        "components": {
          "trend": "0.474984179364214793",
          "structure": "0.750000000000000000",
          "momentum": "0.154129349708407319",
          "participation": "0.205469585059135488",
          "derivatives": {
            "state": "PRICE_DOWN_OI_UP",
            "change": "1183.442000000000000000",
            "change_pct": "1.223354199909987316",
            "segment_id": "01c5efdd5c93cc375e42215a4e5cfccbccb112fe3ede798803bbf9ac16198442",
            "signature": {
              "provider": "tradingview",
              "instrument": "BTCUSDT_PERPETUAL",
              "timeframe": "4h",
              "basis": "confirmed_oi_bar_close_boundary",
              "unit": "BTC"
            }
          }
        },
        "candle_time_utc": "2026-10-03T20:00:00.000Z",
        "reason_codes": [
          "OI.OPPOSING_PRICE_MOVE"
        ]
      },
      "1d": {
        "score": "0.559357243222149826",
        "components": {
          "trend": "1.000000000000000000",
          "structure": "0.000000000000000000",
          "momentum": "0.062381621480998843",
          "participation": "0.143272517326070748",
          "derivatives": null
        },
        "candle_time_utc": "2026-10-03T00:00:00.000Z",
        "reason_codes": [
          "OI.UNAVAILABLE"
        ]
      }
    }
  }
}
```
