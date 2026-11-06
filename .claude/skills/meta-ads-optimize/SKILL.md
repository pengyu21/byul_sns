---
name: meta-ads-optimize
description: 집행 중인 Meta 광고 성과를 분석해서 저성과 소재 중지, 고성과 세트 증액, 피로도가 쌓인 소재의 신규 소재 교체를 제안하고 승인 후 실행한다. "광고 성과 봐줘", "최적화해줘", "어떤 소재 끌까" 같은 요청에 사용.
---

# Meta 광고 최적화 루틴

## 1. 데이터 수집
- `list_campaigns(only_active=true)`로 대상 캠페인을 확인한다(사용자가 지정한 캠페인이 있으면 그것만)
- `analyze_performance(date_preset="last_7d", campaign_id=...)`
  - 집행한 지 3일 미만이면 `last_3d`를 쓰고, "학습 중이라 판단은 이르다"고 먼저 알린다
- 필요하면 `get_insights(level="adset", breakdowns="age,gender")`로 연령·성별 효율을 확인한다

## 2. 판단 기준 (도구가 붙이는 flags 기반)
| flag | 조치 |
|---|---|
| PAUSE | 광고 중지. 단, 해당 광고세트의 마지막 활성 광고는 끄지 않는다 |
| SCALE | 광고세트(또는 CBO 캠페인) 예산을 **+20% 이내**로 증액, 3일 간격 |
| CREATIVE | 같은 앵글로 후킹 문장만 바꾼 변형 소재 2개 생성 |
| FATIGUE | 새로운 앵글의 소재 투입 |
| 판단 보류 | 아무것도 하지 않는다 |

추가로 볼 것: 연령·성별 분석에서 CPA가 2배 이상 나쁜 구간이 있으면 타겟 제외를 제안한다.

## 3. 보고
표로 정리한다: 광고 / 지출 / 결과 / CPA / 링크 CTR / 빈도 / 제안 조치.
그 아래에 **실행할 조치 목록**을 번호로 제시하고 사용자 승인을 받는다.

## 4. 실행 (승인한 항목만)
- 중지: `set_status(ad_id, "PAUSED")`
- 증액: `update_daily_budget(id, 새예산)`으로 미리보기 → `confirm=true`로 실행
- 신규 소재: 기존 캠페인에 추가하는 대신, 새 plan에 `adsets`로 승자 타겟과 새 `creatives`를 넣어 `/meta-ads-launch`의 6단계 방식으로 생성한다(PAUSED 상태로 생성 → 승인 후 ACTIVE 전환)

반복 점검이 필요하면 `/loop 1d /meta-ads-optimize` 또는 `/schedule`을 안내한다(실행은 매번 승인을 거침).
