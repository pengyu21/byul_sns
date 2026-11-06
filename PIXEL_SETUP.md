## 왜 필요한가요?
홈페이지 방문 캠페인에 **하루 약 $100**(광고비의 35%)를 쓰고 있지만, 그 방문자가 **상담 신청을 했는지 Meta가 모릅니다.**
그래서 Meta는 "클릭만 잘하는 사람"을 찾도록 학습하고 있고, 우리도 어느 캠페인이 좋은지 판단할 수 없어요.

**현재 상태 (2026-11-06 점검)**
| 항목 | 상태 |
|---|---|
| 랜딩페이지(`byulstar.com/landing/*.php`)의 Meta 픽셀 | `1109437609099662` — **광고 계정과 다른 픽셀**(다른 비즈니스 소유, 접근 불가) |
| 광고 계정 픽셀 | `2093580901502680` (별성형외과 data) — GTM 을 통해 `PageView`, `form_submit`, `onlineConsult` 등만 수신 |
| `onlineConsult` 이벤트 | "상담 신청하기" **버튼 클릭** 시 발생 (신청 완료가 아님) |
| **상담 신청 완료 이벤트** | ❌ 없음 |

---

## 개발 담당자 작업 (약 1시간)

### 1단계 — 광고 계정 픽셀 추가
랜딩페이지 공통 헤더의 기존 `fbq('init', '1109437609099662')` 아래에 한 줄 추가 (두 픽셀 동시 사용 가능):
```html
<script>
  fbq('init', '2093580901502680');   // 광고 계정 픽셀 (별성형외과 data)
  fbq('track', 'PageView');
</script>
```
> GTM(`GTM-TCSXDBX`)으로 관리한다면 GTM 에 Meta 픽셀 태그를 추가해도 됩니다.

### 2단계 — "신청 완료" 시점에 Lead 이벤트
`counsel_proc*.php` 로 폼이 **정상 저장된 직후**(성공 응답/완료 페이지)에서 실행:
```javascript
// eventID 는 서버에서 만든 고유값(예: 상담 DB 의 insert id). 3단계와 같은 값을 써야 중복 집계가 안 됩니다.
fbq('track', 'Lead', { content_name: '코재수술' /* 시술명 */ }, { eventID: 'consult_12345' });
```
⚠️ 버튼 클릭(`onclick`)이 아니라 **저장 성공 후**에 실행해야 합니다. 유효성 검사 실패·중복 제출은 제외.

### 3단계 (권장) — 서버에서도 전송 (Conversions API)
iOS·광고 차단기로 브라우저 이벤트의 20~30%가 유실됩니다. `counsel_proc*.php` 저장 성공 직후 서버에서 같은 이벤트를 보냅니다.
1. 이벤트 관리자 → 픽셀 `2093580901502680` → 설정 → **Conversions API → 액세스 토큰 생성**
2. PHP 예시:
```php
<?php
function meta_capi_lead($consult_id, $phone, $treatment) {
    $pixel = '2093580901502680';
    $token = getenv('META_CAPI_TOKEN');               // 서버 환경변수에 보관 (코드에 직접 쓰지 말 것)
    $digits = preg_replace('/\D/', '', $phone);        // 010-1234-5678 → 821012345678
    if (strpos($digits, '0') === 0) $digits = '82' . substr($digits, 1);
    $event = [
        'event_name'       => 'Lead',
        'event_time'       => time(),
        'event_id'         => 'consult_' . $consult_id, // 2단계 eventID 와 동일
        'action_source'    => 'website',
        'event_source_url' => $_SERVER['HTTP_REFERER'] ?? '',
        'user_data' => [
            'ph'                => [hash('sha256', $digits)],
            'client_ip_address' => $_SERVER['REMOTE_ADDR'] ?? '',
            'client_user_agent' => $_SERVER['HTTP_USER_AGENT'] ?? '',
            'fbp'               => $_COOKIE['_fbp'] ?? null,
            'fbc'               => $_COOKIE['_fbc'] ?? null,
        ],
        'custom_data' => ['content_name' => $treatment],
    ];
    $ch = curl_init("https://graph.facebook.com/v24.0/{$pixel}/events?access_token={$token}");
    curl_setopt_array($ch, [CURLOPT_POST => true, CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 3,
        CURLOPT_POSTFIELDS => ['data' => json_encode([$event])]]);
    curl_exec($ch);                                    // 실패해도 상담 저장에는 영향 없게
    curl_close($ch);
}
```

### 4단계 — 확인
이벤트 관리자 → 픽셀 `2093580901502680` → **테스트 이벤트**에서 실제로 상담을 1건 넣어 보고 `Lead` 가 (브라우저 + 서버) 1건으로 합쳐져 보이는지 확인.

---

## 설치 후 자동으로 바뀌는 것
- 대시보드의 홈페이지 방문 캠페인이 **"측정 불가" → 상담 1건당 비용으로 판정**됩니다.
- 그 다음 단계로 방문 캠페인의 최적화 목표를 "랜딩페이지 조회" → **"리드(Lead)"** 로 바꾸면, Meta가 실제로 신청할 사람을 찾기 시작합니다.

> 개인정보: 전화번호는 반드시 SHA-256 해시 후 전송합니다(위 코드 그대로 사용). 개인정보처리방침에 "맞춤형 광고를 위한 정보 제공(Meta)" 항목이 있는지 확인하세요.
