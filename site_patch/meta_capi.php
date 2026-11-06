<?php
/**
 * Meta Conversions API — 상담 신청 완료(Lead)를 서버에서 Meta 로 전송
 *
 * 설치: /front/counsel/ 에 이 파일과 meta_capi_config.php 를 업로드
 * 사용: 각 counsel_proc*.php 에서 "상담 저장 성공 직후, 완료 알림/이동 전에" 두 줄 추가
 *
 *     require_once $_SERVER['DOCUMENT_ROOT'] . '/front/counsel/meta_capi.php';
 *     meta_capi_lead();
 *
 * - 폼 값($_POST)에서 전화번호를 알아서 찾아 SHA-256 해시 후 전송 (원문 전화번호는 보내지 않음)
 * - 전송 실패해도 상담 저장/화면에는 영향 없음 (3초 타임아웃, 오류는 서버 로그에만 기록)
 */

if (!function_exists('meta_capi_lead')) {

    function meta_capi_lead($phone = null, $content_name = null, $event_id = null)
    {
        $cfg_file = __DIR__ . '/meta_capi_config.php';
        $cfg = [];
        if (is_readable($cfg_file)) {
            $cfg = include $cfg_file;
        }
        $pixel = isset($cfg['pixel_id']) ? $cfg['pixel_id'] : '2093580901502680';
        $token = isset($cfg['token']) ? $cfg['token'] : '';
        if ($token === '') {
            error_log('[meta_capi] token 이 설정되지 않음 (meta_capi_config.php)');
            return false;
        }

        // ── 전화번호: 폼마다 이름이 달라서 흔한 이름을 차례로 확인 ──
        if ($phone === null) {
            $p = $_POST;
            if (isset($p['tel1'])) {
                $phone = $p['tel1'] . (isset($p['tel2']) ? $p['tel2'] : '') . (isset($p['tel3']) ? $p['tel3'] : '');
            } else {
                foreach (['qc_tel', 'tel', 'phone', 'hp', 'mobile', 'wr_hp'] as $k) {
                    if (!empty($p[$k])) { $phone = $p[$k]; break; }
                }
            }
        }
        $digits = preg_replace('/\D/', '', (string)$phone);
        if ($digits !== '' && $digits[0] === '0') {
            $digits = '82' . substr($digits, 1);          // 01012345678 → 821012345678
        }

        // ── 어느 랜딩/캠페인에서 왔는지 (리퍼러의 utm_term, fbclid) ──
        $referer = isset($_SERVER['HTTP_REFERER']) ? $_SERVER['HTTP_REFERER'] : '';
        $query = [];
        parse_str((string)parse_url($referer, PHP_URL_QUERY), $query);
        if ($content_name === null) {
            $content_name = !empty($query['utm_term']) ? $query['utm_term'] : basename((string)parse_url($referer, PHP_URL_PATH));
        }

        $fbc = isset($_COOKIE['_fbc']) ? $_COOKIE['_fbc'] : null;
        if (!$fbc && !empty($query['fbclid'])) {
            $fbc = 'fb.1.' . round(microtime(true) * 1000) . '.' . $query['fbclid'];
        }
        $ip = isset($_SERVER['HTTP_X_FORWARDED_FOR'])
            ? trim(explode(',', $_SERVER['HTTP_X_FORWARDED_FOR'])[0])
            : (isset($_SERVER['REMOTE_ADDR']) ? $_SERVER['REMOTE_ADDR'] : '');

        $user_data = array_filter([
            'ph'                => $digits !== '' ? [hash('sha256', $digits)] : null,
            'client_ip_address' => $ip,
            'client_user_agent' => isset($_SERVER['HTTP_USER_AGENT']) ? $_SERVER['HTTP_USER_AGENT'] : '',
            'fbp'               => isset($_COOKIE['_fbp']) ? $_COOKIE['_fbp'] : null,
            'fbc'               => $fbc,
        ]);

        $event = [
            'event_name'       => 'Lead',
            'event_time'       => time(),
            'event_id'         => $event_id ? $event_id : 'lead_' . (function_exists('random_bytes') ? bin2hex(random_bytes(8)) : uniqid('', true)),
            'action_source'    => 'website',
            'event_source_url' => $referer,
            'user_data'        => $user_data,
            'custom_data'      => ['content_name' => (string)$content_name],
        ];

        $post = ['data' => json_encode([$event], JSON_UNESCAPED_UNICODE), 'access_token' => $token];
        if (!empty($cfg['test_event_code'])) {
            $post['test_event_code'] = $cfg['test_event_code'];   // 테스트할 때만
        }
        $url = "https://graph.facebook.com/v24.0/{$pixel}/events";

        if (function_exists('curl_init')) {
            $ch = curl_init($url);
            curl_setopt_array($ch, [
                CURLOPT_POST => true, CURLOPT_POSTFIELDS => http_build_query($post),
                CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 3, CURLOPT_CONNECTTIMEOUT => 2,
            ]);
            $res = curl_exec($ch);
            $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
            curl_close($ch);
        } else {
            $ctx = stream_context_create(['http' => [
                'method' => 'POST', 'timeout' => 3, 'ignore_errors' => true,
                'header' => "Content-Type: application/x-www-form-urlencoded\r\n",
                'content' => http_build_query($post),
            ]]);
            $res = @file_get_contents($url, false, $ctx);
            $code = isset($http_response_header[0]) && preg_match('/\s(\d{3})\s/', $http_response_header[0], $m) ? (int)$m[1] : 0;
        }
        if ($code !== 200) {
            error_log('[meta_capi] 전송 실패 HTTP ' . $code . ' ' . substr((string)$res, 0, 300));
            return false;
        }
        return true;
    }
}
