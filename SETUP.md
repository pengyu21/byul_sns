# Meta 광고 자동화 — 초기 세팅 (최초 1회, 약 15분)

## 1. Meta 앱 만들기
1. https://developers.facebook.com/apps 에 접속해 **앱 만들기**를 누른다. 유형은 **비즈니스**로 고르고, 본인 비즈니스 포트폴리오에 연결한다.
2. 앱 대시보드에서 **Marketing API** 제품을 추가한다.

## 2. 시스템 사용자 토큰 발급 (만료되지 않는 토큰)
1. https://business.facebook.com/settings 에서 **사용자 → 시스템 사용자 → 추가**를 누른다. 역할은 **관리자**로 지정한다.
2. 방금 만든 시스템 사용자에게 **자산 할당**을 한다.
   - 광고 계정: **전체 관리** 권한
   - 페이지: **전체 관리** 권한
   - (선택) 픽셀/데이터세트 권한
3. **새 토큰 생성**을 누르고 1번에서 만든 앱을 선택한다. 만료는 **사용 안 함**으로 설정한다.
   - 권한: `ads_management`, `ads_read`, `business_management`, `pages_show_list`, `pages_read_engagement`, `pages_manage_ads`
4. 발급된 토큰을 `.env`의 `META_ACCESS_TOKEN`에 붙여넣는다.

## 3. ID 채우기 (`.env`)
| 항목 | 찾는 곳 |
|---|---|
| `META_AD_ACCOUNT_ID` | 광고관리자 URL의 `act=` 뒤 숫자. 값은 `act_숫자` 형태로 입력 |
| `META_PAGE_ID` | 페이스북 페이지 → 정보 → 페이지 투명성, 또는 비즈니스 설정 → 페이지 |
| `META_PIXEL_ID` | 이벤트 관리자 → 데이터 소스 → 픽셀 ID (구매/리드 최적화에 필요) |
| `META_INSTAGRAM_USER_ID` | 비즈니스 설정 → 인스타그램 계정 → ID (선택) |
| `MAX_DAILY_BUDGET` | 캠페인 1개의 일예산 상한. 이 값을 넘는 세팅은 차단됨 |

## 4. 연결 확인
VS Code에서 Claude Code를 다시 열고(또는 `/mcp` 실행) **meta-ads** 서버가 connected 상태인지 확인한다.
그다음 이렇게 요청한다: `광고 계정 상태 확인해줘`

## 사용법
- 런칭: `/meta-ads-launch 상품: OO크림, 랜딩: https://..., 목표: 판매, 일예산 3만원`
- 최적화: `/meta-ads-optimize`

## 안전장치
- 모든 캠페인, 광고세트, 광고는 **PAUSED 상태로 생성**된다. 직접 승인하기 전에는 과금되지 않는다.
- ACTIVE 전환과 예산 변경은 Claude Code가 매번 허용 여부를 묻는다(`.claude/settings.json`의 `ask` 설정).
- 생성 내역은 `runs/`에 JSON으로, 만든 소재 이미지는 `creatives/`에 저장된다.
- `.env`에는 토큰이 들어 있으므로 공유하거나 깃에 올리지 않는다.
