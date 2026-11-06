"""Meta(Facebook/Instagram) 유료광고 자동화 MCP 서버.

안전 원칙
- 생성되는 모든 캠페인/광고세트/광고는 PAUSED 상태로 만들어진다.
- 일예산은 MAX_DAILY_BUDGET(.env)를 넘을 수 없다.
- ACTIVE 전환과 예산 변경은 confirm=True 가 있어야 실행된다.
"""
from __future__ import annotations

import json
import logging
import os
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

API_VERSION = os.getenv("META_API_VERSION", "v24.0")
GRAPH = f"https://graph.facebook.com/{API_VERSION}"
TOKEN = os.getenv("META_ACCESS_TOKEN", "").strip()
_acct = os.getenv("META_AD_ACCOUNT_ID", "").strip()
ACCOUNT = _acct if (not _acct or _acct.startswith("act_")) else f"act_{_acct}"
PAGE_ID = os.getenv("META_PAGE_ID", "").strip()
PIXEL_ID = os.getenv("META_PIXEL_ID", "").strip()
IG_USER_ID = os.getenv("META_INSTAGRAM_USER_ID", "").strip()
MAX_DAILY_BUDGET = int(os.getenv("MAX_DAILY_BUDGET", "50000"))
# 집행 중인 모든 캠페인 일예산 합계 상한 (0 = 검사 안 함)
MAX_TOTAL_DAILY_BUDGET = int(os.getenv("MAX_TOTAL_DAILY_BUDGET", "0"))
DEFAULT_COUNTRY = os.getenv("DEFAULT_COUNTRY", "KR")
MIN_SPEND = float(os.getenv("MIN_SPEND_FOR_JUDGEMENT", "10"))
# 통화별 최소단위 배수: 예산 API 값 = 금액 × offset (USD 3000 = $30, KRW/JPY 는 그대로)
ZERO_DECIMAL = {"KRW", "JPY", "TWD", "CLP", "COP", "HUF", "ISK", "IDR", "VND", "PYG"}

CREATIVE_DIR = ROOT / "creatives"
RUNS_DIR = ROOT / "runs"
FONT_BOLD = os.getenv("AD_FONT_BOLD", r"C:\Windows\Fonts\malgunbd.ttf")
FONT_REG = os.getenv("AD_FONT_REGULAR", r"C:\Windows\Fonts\malgun.ttf")

OBJECTIVES = {
    "OUTCOME_TRAFFIC", "OUTCOME_SALES", "OUTCOME_LEADS",
    "OUTCOME_AWARENESS", "OUTCOME_ENGAGEMENT",
}
CTAS = {
    "LEARN_MORE", "SHOP_NOW", "SIGN_UP", "BOOK_TRAVEL", "CONTACT_US", "DOWNLOAD",
    "GET_OFFER", "GET_QUOTE", "SUBSCRIBE", "APPLY_NOW", "ORDER_NOW", "BUY_NOW",
    "SEND_MESSAGE", "WATCH_MORE", "NO_BUTTON",
}

mcp = FastMCP("meta-ads")

# 요청 URL(페이지네이션 next 링크에는 토큰 포함)이 로그에 남지 않도록 차단
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)
AUTH = {"Authorization": f"Bearer {TOKEN}"}


# ───────────────────────── Graph API helper ─────────────────────────
class MetaError(RuntimeError):
    pass


def _enc(d: dict | None) -> dict:
    out = {}
    for k, v in (d or {}).items():
        if v is None:
            continue
        out[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list, bool)) else v
    return out


def _api(method: str, path: str, params: dict | None = None, data: dict | None = None,
         files: dict | None = None) -> dict:
    if not TOKEN:
        raise MetaError("META_ACCESS_TOKEN 이 .env 에 없습니다. SETUP.md 를 참고해 설정하세요.")
    url = path if path.startswith("http") else f"{GRAPH}/{path.lstrip('/')}"
    q = _enc(params)
    with httpx.Client(timeout=90, headers=AUTH) as c:
        r = c.request(method, url, params=q, data=_enc(data) or None, files=files)
    try:
        body = r.json()
    except ValueError:
        raise MetaError(f"HTTP {r.status_code}: {r.text[:500]}")
    if r.status_code >= 400 or (isinstance(body, dict) and "error" in body):
        e = body.get("error", {}) if isinstance(body, dict) else {}
        msg = e.get("error_user_msg") or e.get("message") or str(body)
        title = e.get("error_user_title", "")
        raise MetaError(
            f"Meta API 오류 [{e.get('code')}/{e.get('error_subcode')}] {title} {msg}".strip()
        )
    return body


def _get_all(path: str, params: dict, max_pages: int = 10) -> list:
    rows, body = [], _api("GET", path, params)
    rows += body.get("data", [])
    for _ in range(max_pages - 1):
        nxt = body.get("paging", {}).get("next")
        if not nxt:
            break
        with httpx.Client(timeout=90) as c:
            body = c.get(nxt).json()
        rows += body.get("data", [])
    return rows


def _human(minor: int, currency: str | None) -> str:
    if currency in ZERO_DECIMAL:
        return f"{minor:,} {currency}"
    return f"{minor / 100:,.2f} {currency or ''}".strip()


def _need_account():
    if not ACCOUNT:
        raise MetaError("META_AD_ACCOUNT_ID 가 .env 에 없습니다.")


# ───────────────────────── 계정 / 리서치 ─────────────────────────
@mcp.tool()
def account_overview() -> dict:
    """광고 계정 상태, 통화, 설정된 페이지/픽셀, 안전장치(예산 상한)를 확인한다. 작업 시작 시 가장 먼저 호출."""
    _need_account()
    acct = _api("GET", ACCOUNT, {
        "fields": "name,account_status,currency,timezone_name,amount_spent,spend_cap,balance,disable_reason"
    })
    status_map = {1: "ACTIVE", 2: "DISABLED", 3: "UNSETTLED", 7: "PENDING_RISK_REVIEW",
                  8: "PENDING_SETTLEMENT", 9: "IN_GRACE_PERIOD", 101: "CLOSED"}
    acct["account_status_text"] = status_map.get(acct.get("account_status"), "UNKNOWN")
    page = None
    if PAGE_ID:
        try:
            page = _api("GET", PAGE_ID, {"fields": "name,link"})
        except MetaError as e:
            page = {"error": str(e)}
    return {
        "account": acct,
        "page": page,
        "pixel_id": PIXEL_ID or None,
        "instagram_user_id": IG_USER_ID or None,
        "guardrails": {"max_daily_budget": MAX_DAILY_BUDGET, "created_status": "PAUSED"},
        "api_version": API_VERSION,
        "budget_unit": ("원 그대로 (30000 = ₩30,000)" if acct.get("currency") in ZERO_DECIMAL
                        else f"최소단위 ×100 (3000 = 30 {acct.get('currency')})"),
        "max_daily_budget_human": _human(MAX_DAILY_BUDGET, acct.get("currency")),
    }


@mcp.tool()
def search_interests(query: str, limit: int = 15) -> list:
    """관심사 타겟을 키워드로 검색한다(예: '요가', '캠핑'). 결과의 id/name 을 targeting.flexible_spec 에 사용."""
    body = _api("GET", "search", {"type": "adinterest", "q": query, "limit": limit, "locale": "ko_KR"})
    return [{
        "id": d["id"], "name": d["name"],
        "audience": f"{d.get('audience_size_lower_bound', '?')}~{d.get('audience_size_upper_bound', '?')}",
        "path": " > ".join(d.get("path", [])),
    } for d in body.get("data", [])]


@mcp.tool()
def search_locations(query: str, location_types: list[str] | None = None) -> list:
    """지역 타겟 검색(예: '서울', '부산'). 결과 key 를 geo_locations.regions/cities 에 사용."""
    body = _api("GET", "search", {
        "type": "adgeolocation", "q": query, "locale": "ko_KR",
        "location_types": location_types or ["region", "city"],
    })
    return [{k: d.get(k) for k in ("key", "name", "type", "region", "country_code")}
            for d in body.get("data", [])]


@mcp.tool()
def estimate_audience(targeting: dict, optimization_goal: str = "LINK_CLICKS") -> dict:
    """타겟 조합의 예상 도달 규모(MAU/DAU)를 조회한다. 너무 좁으면(<10만) 확장 권장."""
    _need_account()
    t = _normalize_targeting(targeting)
    body = _api("GET", f"{ACCOUNT}/delivery_estimate", {
        "targeting_spec": t, "optimization_goal": optimization_goal,
    })
    d = (body.get("data") or [{}])[0]
    return {
        "mau_lower": d.get("estimate_mau_lower_bound"),
        "mau_upper": d.get("estimate_mau_upper_bound"),
        "dau": d.get("estimate_dau"),
        "ready": d.get("estimate_ready"),
        "targeting_used": t,
    }


# ───────────────────────── 소재(이미지) ─────────────────────────
def _font(path: str, size: int):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            cand = f"{cur} {word}".strip()
            if draw.textlength(cand, font=font) <= max_w:
                cur = cand
                continue
            if cur:
                lines.append(cur)
            cur = ""
            for ch in word:  # 한글 등 긴 단어는 글자 단위로 끊기
                if draw.textlength(cur + ch, font=font) <= max_w:
                    cur += ch
                else:
                    lines.append(cur)
                    cur = ch
        lines.append(cur)
    return [l for l in lines if l != ""] or [""]


def _hex(c: str) -> tuple:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


@mcp.tool()
def render_ad_image(headline: str, subtext: str = "", cta_text: str = "", badge: str = "",
                    size: str = "1080x1080", bg_color: str = "#111827", text_color: str = "#FFFFFF",
                    accent_color: str = "#F59E0B", bg_image_path: str = "", file_name: str = "") -> dict:
    """텍스트 중심 광고 이미지를 생성해 creatives/ 에 저장한다.
    size: 1080x1080(피드) | 1080x1350(4:5) | 1080x1920(스토리/릴스).
    bg_image_path 를 주면 사진 위에 어두운 오버레이 + 텍스트를 얹는다. badge 는 좌상단 라벨(예: '50% 할인')."""
    from PIL import Image, ImageDraw, ImageOps

    W, H = (int(x) for x in size.lower().split("x"))
    if bg_image_path:
        img = ImageOps.fit(Image.open(bg_image_path).convert("RGB"), (W, H))
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        for y in range(H):  # 아래로 갈수록 진해지는 그라데이션
            od.line([(0, y), (W, y)], fill=(0, 0, 0, int(60 + 150 * y / H)))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    else:
        img = Image.new("RGB", (W, H), _hex(bg_color))
    d = ImageDraw.Draw(img)
    pad = int(W * 0.08)
    max_w = W - pad * 2
    tc, ac = _hex(text_color), _hex(accent_color)

    if badge:
        bf = _font(FONT_BOLD, int(W * 0.04))
        bw = d.textlength(badge, font=bf) + 48
        bh = int(W * 0.04) + 36
        d.rounded_rectangle([pad, pad, pad + bw, pad + bh], radius=bh // 2, fill=ac)
        d.text((pad + 24, pad + 14), badge, font=bf, fill=(17, 24, 39))

    # 헤드라인 크기 자동 조절 (최대 4줄)
    hs = int(W * 0.095)
    while True:
        hf = _font(FONT_BOLD, hs)
        hl = _wrap(d, headline, hf, max_w)
        if len(hl) <= 4 or hs <= 40:
            break
        hs -= 6
    sf = _font(FONT_REG, int(W * 0.042))
    sl = _wrap(d, subtext, sf, max_w) if subtext else []
    cf = _font(FONT_BOLD, int(W * 0.045))

    h_lh, s_lh = int(hs * 1.25), int(W * 0.042 * 1.5)
    cta_h = int(W * 0.045) + 56 if cta_text else 0
    block = len(hl) * h_lh + (30 + len(sl) * s_lh if sl else 0) + (60 + cta_h if cta_text else 0)
    y = (H - block) // 2 if not bg_image_path else H - pad - block

    for line in hl:
        d.text((pad, y), line, font=hf, fill=tc)
        y += h_lh
    if sl:
        y += 30
        for line in sl:
            d.text((pad, y), line, font=sf, fill=tuple(int(c * 0.85) for c in tc))
            y += s_lh
    if cta_text:
        y += 60
        cw = d.textlength(cta_text, font=cf) + 96
        d.rounded_rectangle([pad, y, pad + cw, y + cta_h], radius=cta_h // 2, fill=ac)
        d.text((pad + 48, y + 22), cta_text, font=cf, fill=(17, 24, 39))

    CREATIVE_DIR.mkdir(exist_ok=True)
    name = file_name or f"ad_{datetime.now():%Y%m%d_%H%M%S}_{W}x{H}"
    out = CREATIVE_DIR / f"{Path(name).stem}.png"
    img.save(out, "PNG")
    return {"path": str(out), "size": f"{W}x{H}"}


@mcp.tool()
def upload_image(image_path: str) -> dict:
    """로컬 이미지를 광고 계정 이미지 라이브러리에 업로드하고 image_hash 를 반환한다."""
    _need_account()
    p = Path(image_path)
    with p.open("rb") as f:
        body = _api("POST", f"{ACCOUNT}/adimages", files={"filename": (p.name, f.read())})
    info = next(iter(body["images"].values()))
    return {"image_hash": info["hash"], "url": info.get("url"), "path": str(p)}


# ───────────────────────── 원클릭 세팅 ─────────────────────────
def _normalize_targeting(t: dict, advantage_audience: bool = False) -> dict:
    t = json.loads(json.dumps(t or {}))
    t.setdefault("geo_locations", {"countries": [DEFAULT_COUNTRY]})
    t.setdefault("age_min", 18)
    t.setdefault("age_max", 65)
    t.setdefault("targeting_automation", {"advantage_audience": 1 if advantage_audience else 0})
    return t


def _goal_for(objective: str) -> tuple[str, dict | None, str | None]:
    """objective → (optimization_goal, promoted_object, destination_type)"""
    if objective == "OUTCOME_SALES":
        return "OFFSITE_CONVERSIONS", {"pixel_id": PIXEL_ID, "custom_event_type": "PURCHASE"}, "WEBSITE"
    if objective == "OUTCOME_LEADS":
        return "OFFSITE_CONVERSIONS", {"pixel_id": PIXEL_ID, "custom_event_type": "LEAD"}, "WEBSITE"
    if objective == "OUTCOME_TRAFFIC":
        return ("LANDING_PAGE_VIEWS" if PIXEL_ID else "LINK_CLICKS"), None, "WEBSITE"
    if objective == "OUTCOME_AWARENESS":
        return "REACH", None, None
    return "POST_ENGAGEMENT", None, None


def _validate(plan: dict) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    if not PAGE_ID:
        errs.append("META_PAGE_ID 미설정")
    obj = plan.get("objective")
    if obj not in OBJECTIVES:
        errs.append(f"objective 는 {sorted(OBJECTIVES)} 중 하나여야 함 (입력: {obj})")
    if obj in ("OUTCOME_SALES", "OUTCOME_LEADS") and not PIXEL_ID and not plan.get("lead_form_id"):
        errs.append(f"{obj} 는 META_PIXEL_ID 가 필요함 (없으면 OUTCOME_TRAFFIC 사용)")
    b = plan.get("daily_budget")
    if not isinstance(b, int) or b <= 0:
        errs.append("daily_budget 은 양의 정수(계정 통화 최소단위: USD=센트, KRW=원)")
    elif b > MAX_DAILY_BUDGET:
        errs.append(f"daily_budget {b:,} 이 상한 MAX_DAILY_BUDGET {MAX_DAILY_BUDGET:,} 초과")
    link = plan.get("link", "")
    if not str(link).startswith("https://"):
        errs.append("link 는 https:// 로 시작하는 랜딩 URL 이어야 함")
    if not plan.get("adsets"):
        errs.append("adsets 가 비어 있음")
    crs = plan.get("creatives") or []
    if not crs:
        errs.append("creatives 가 비어 있음")
    for i, c in enumerate(crs):
        tag = f"creatives[{i}]"
        if not (c.get("image_path") or c.get("image_hash")):
            errs.append(f"{tag}: image_path 또는 image_hash 필요")
        if c.get("image_path") and not Path(c["image_path"]).exists():
            errs.append(f"{tag}: 파일 없음 {c['image_path']}")
        if not c.get("primary_text"):
            errs.append(f"{tag}: primary_text 필요")
        if len(c.get("primary_text", "")) > 125:
            warns.append(f"{tag}: 본문 {len(c['primary_text'])}자 — 125자 넘으면 '더 보기'로 잘림")
        if len(c.get("headline", "")) > 40:
            warns.append(f"{tag}: 헤드라인 40자 초과 — 잘릴 수 있음")
        if c.get("cta", "LEARN_MORE") not in CTAS:
            errs.append(f"{tag}: cta '{c.get('cta')}' 는 지원 목록 아님 {sorted(CTAS)}")
    if len(crs) * len(plan.get("adsets") or []) > 50:
        warns.append("광고 수가 50개 초과 — 학습이 분산됩니다")
    return errs, warns


@mcp.tool()
def launch_campaign(plan: dict, dry_run: bool = True) -> dict:
    """원클릭 광고 세팅: 캠페인(CBO) + 광고세트 N개 + 소재 M개 → 광고 N×M개를 모두 PAUSED 로 생성.

    plan 예시:
    {
      "name": "봄시즌_트래픽_2611",
      "objective": "OUTCOME_TRAFFIC",            # TRAFFIC|SALES|LEADS|AWARENESS|ENGAGEMENT
      "daily_budget": 30000,                     # 통화 최소단위(USD 센트: 3000=$30 / KRW 원), 캠페인 예산(CBO)
      "link": "https://example.com/landing",
      "special_ad_categories": [],               # 신용/고용/주거/정치 광고면 지정
      "adsets": [
        {"name": "브로드_25-44", "targeting": {"age_min": 25, "age_max": 44}, "advantage_audience": true},
        {"name": "관심사_요가", "targeting": {"age_min": 25, "age_max": 44, "genders": [2],
           "flexible_spec": [{"interests": [{"id": "6003...", "name": "요가"}]}]}}
      ],
      "creatives": [
        {"name": "A_혜택", "image_path": "C:/.../creatives/a.png", "primary_text": "...",
         "headline": "...", "description": "...", "cta": "LEARN_MORE"}
      ]
    }
    dry_run=True(기본)면 검증과 최종 페이로드만 반환하고 아무것도 만들지 않는다.
    """
    _need_account()
    errs, warns = _validate(plan)
    if errs:
        return {"ok": False, "errors": errs, "warnings": warns}

    obj = plan["objective"]
    goal, promoted, dest = _goal_for(obj)
    form_id = plan.get("lead_form_id") if obj == "OUTCOME_LEADS" else None
    if form_id:  # 인스턴트 양식(페이스북 내 상담신청서)
        goal, promoted, dest = "LEAD_GENERATION", {"page_id": PAGE_ID}, "ON_AD"
    camp_payload = {
        "name": plan["name"], "objective": obj, "status": "PAUSED",
        "special_ad_categories": plan.get("special_ad_categories", []),
        "daily_budget": plan["daily_budget"], "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
    }
    adset_payloads = []
    for a in plan["adsets"]:
        p = {
            "name": a["name"], "status": "PAUSED", "billing_event": "IMPRESSIONS",
            "optimization_goal": a.get("optimization_goal", goal),
            "targeting": _normalize_targeting(a.get("targeting", {}), a.get("advantage_audience", False)),
        }
        if promoted:
            p["promoted_object"] = promoted
        if dest:
            p["destination_type"] = dest
        if a.get("start_time"):
            p["start_time"] = a["start_time"]
        if a.get("end_time"):
            p["end_time"] = a["end_time"]
        adset_payloads.append(p)

    def creative_payload(c: dict, image_hash: str) -> dict:
        return _creative_payload(c, image_hash, plan["link"], form_id)

    summary = {
        "campaign": camp_payload,
        "adsets": adset_payloads,
        "creatives": [creative_payload(c, c.get("image_hash", "<업로드 후 생성>")) for c in plan["creatives"]],
        "ads_to_create": len(adset_payloads) * len(plan["creatives"]),
    }
    if dry_run:
        return {"ok": True, "dry_run": True, "warnings": warns, "preview": summary}

    created: dict[str, Any] = {"campaign_id": None, "adsets": [], "creatives": [], "ads": []}
    try:
        camp = _api("POST", f"{ACCOUNT}/campaigns", data=camp_payload)
        created["campaign_id"] = camp["id"]

        creative_ids = []
        for c in plan["creatives"]:
            h = c.get("image_hash") or upload_image(c["image_path"])["image_hash"]
            cr = _api("POST", f"{ACCOUNT}/adcreatives", data=creative_payload(c, h))
            creative_ids.append((c.get("name", "creative"), cr["id"]))
            created["creatives"].append({"name": c.get("name"), "id": cr["id"], "image_hash": h})

        for p in adset_payloads:
            ads = _api("POST", f"{ACCOUNT}/adsets", data={**p, "campaign_id": camp["id"]})
            created["adsets"].append({"name": p["name"], "id": ads["id"]})
            for cname, cid in creative_ids:
                ad = _api("POST", f"{ACCOUNT}/ads", data={
                    "name": f"{p['name']} | {cname}", "adset_id": ads["id"],
                    "creative": {"creative_id": cid}, "status": "PAUSED",
                })
                created["ads"].append({"name": f"{p['name']} | {cname}", "id": ad["id"]})
    except MetaError as e:
        rolled = False
        if created["campaign_id"]:
            try:
                _api("POST", created["campaign_id"], data={"status": "DELETED"})
                rolled = True
            except MetaError:
                pass
        _save_run(plan, created, error=str(e))
        return {"ok": False, "error": str(e), "partially_created": created, "rolled_back": rolled}

    run_file = _save_run(plan, created)
    return {
        "ok": True, "status": "PAUSED (아직 집행되지 않음)", "created": created, "warnings": warns,
        "run_log": run_file,
        "ads_manager": f"https://adsmanager.facebook.com/adsmanager/manage/campaigns?act={ACCOUNT[4:]}"
                       f"&selected_campaign_ids={created['campaign_id']}",
        "next": "검토 후 set_status(campaign_id, 'ACTIVE', cascade=True, confirm=True) 로 집행 시작",
    }


def _creative_payload(c: dict, image_hash: str, default_link: str, lead_form_id: str | None = None) -> dict:
    link_data = {
        "link": c.get("link") or default_link or (f"https://www.facebook.com/{PAGE_ID}" if lead_form_id else ""),
        "message": c["primary_text"],
        "image_hash": image_hash,
    }
    if c.get("headline"):
        link_data["name"] = c["headline"]
    if c.get("description"):
        link_data["description"] = c["description"]
    cta = c.get("cta", "LEARN_MORE")
    if lead_form_id:
        link_data["call_to_action"] = {"type": cta if cta != "NO_BUTTON" else "SIGN_UP",
                                       "value": {"lead_gen_form_id": lead_form_id}}
    elif cta != "NO_BUTTON":
        link_data["call_to_action"] = {"type": cta, "value": {"link": link_data["link"]}}
    spec = {"page_id": PAGE_ID, "link_data": link_data}
    if IG_USER_ID:
        spec["instagram_user_id"] = IG_USER_ID
    return {"name": c.get("name", "creative"), "object_story_spec": spec}


def _cta_value(spec: dict) -> dict:
    for k in ("link_data", "video_data"):
        v = ((spec.get(k) or {}).get("call_to_action") or {}).get("value") or {}
        if v:
            return v
    return {}


def _landing_of(adset_id: str) -> tuple[str, str]:
    """광고세트 기존 광고에서 (랜딩 URL, 인스턴트 양식 ID) 를 찾는다."""
    ads = _get_all(f"{adset_id}/ads", {"fields": "creative{object_story_spec}", "limit": 10}, 1)
    link, form = "", ""
    for a in ads:
        spec = (a.get("creative") or {}).get("object_story_spec") or {}
        v = _cta_value(spec)
        link = link or (spec.get("link_data") or {}).get("link") or v.get("link", "")
        form = form or v.get("lead_gen_form_id", "")
    return link, form


@mcp.tool()
def list_lead_forms() -> list:
    """계정의 잠재고객 광고에서 사용 중인 인스턴트 양식(상담신청서) 목록을 찾는다."""
    camps = _get_all(f"{ACCOUNT}/campaigns", {"fields": "id,name,objective", "limit": 200}, 3)
    forms: dict[str, dict] = {}
    for c in camps:
        if c.get("objective") != "OUTCOME_LEADS":
            continue
        for a in _get_all(f"{c['id']}/ads", {"fields": "creative{object_story_spec}", "limit": 25}, 1):
            fid = _cta_value((a.get("creative") or {}).get("object_story_spec") or {}).get("lead_gen_form_id")
            if fid and fid not in forms:
                forms[fid] = {"id": fid, "used_in": c["name"]}
    for fid, f in forms.items():
        try:
            f.update(_api("GET", fid, {"fields": "name,status,leads_count"}))
        except MetaError:
            pass
    return list(forms.values())


@mcp.tool()
def add_ads_to_adset(adset_id: str, creatives: list[dict], link: str = "", dry_run: bool = True) -> dict:
    """기존 광고세트에 새 소재(광고)를 PAUSED 로 추가한다. 승자 타겟에 신규 소재를 테스트할 때 사용.
    creatives 형식은 launch_campaign 과 동일. link 를 비우면 기존 광고의 랜딩 URL 을 그대로 쓴다."""
    if not PAGE_ID:
        return {"ok": False, "errors": ["META_PAGE_ID 미설정"]}
    found_link, form_id = _landing_of(adset_id)
    link = link or found_link
    errs = []
    if not form_id and not str(link).startswith("https://"):
        errs.append("랜딩 URL 을 찾지 못했습니다. link 를 직접 지정하세요.")
    for i, c in enumerate(creatives):
        if not (c.get("image_path") or c.get("image_hash")):
            errs.append(f"creatives[{i}]: image_path 또는 image_hash 필요")
        if not c.get("primary_text"):
            errs.append(f"creatives[{i}]: primary_text 필요")
        if c.get("cta", "LEARN_MORE") not in CTAS:
            errs.append(f"creatives[{i}]: cta '{c.get('cta')}' 지원 안 함")
    if errs:
        return {"ok": False, "errors": errs}
    if dry_run:
        return {"ok": True, "dry_run": True, "link": link,
                "lead_form_id": form_id or None,
                "preview": [_creative_payload(c, c.get("image_hash", "<업로드 후 생성>"), link, form_id or None)
                            for c in creatives]}
    created = []
    run = {"name": f"add_to_{adset_id}", "creatives": creatives}
    try:
        for c in creatives:
            h = c.get("image_hash") or upload_image(c["image_path"])["image_hash"]
            cr = _api("POST", f"{ACCOUNT}/adcreatives", data=_creative_payload(c, h, link, form_id or None))
            ad = _api("POST", f"{ACCOUNT}/ads", data={
                "name": c.get("name", "new_creative"), "adset_id": adset_id,
                "creative": {"creative_id": cr["id"]}, "status": "PAUSED",
            })
            created.append({"name": c.get("name"), "ad_id": ad["id"], "creative_id": cr["id"]})
    except MetaError as e:
        _save_run(run, {"ads": created}, error=str(e))
        return {"ok": False, "errors": [str(e)], "created": created}
    _save_run(run, {"ads": created})
    return {"ok": True, "status": "PAUSED", "created": created, "link": link}


@mcp.tool()
def list_ads_with_creatives(campaign_id: str) -> list:
    """캠페인의 광고별 소재 정보(썸네일, 본문, 헤드라인, 상태)를 조회한다. 성과 좋은 카피를 참고할 때 사용."""
    rows = _get_all(f"{campaign_id}/ads", {
        "fields": "id,name,effective_status,adset_id,adset{name},"
                  "creative{thumbnail_url,image_url,body,title,object_story_spec}",
        "thumbnail_width": 300, "thumbnail_height": 300, "limit": 100,
    }, 3)
    out = []
    for r in rows:
        cr = r.get("creative") or {}
        spec = cr.get("object_story_spec") or {}
        ld = spec.get("link_data") or spec.get("video_data") or {}
        out.append({
            "ad_id": r["id"], "ad": r.get("name"), "status": r.get("effective_status"),
            "adset_id": r.get("adset_id"), "adset": (r.get("adset") or {}).get("name"),
            "thumbnail": cr.get("thumbnail_url") or cr.get("image_url"),
            "body": cr.get("body") or ld.get("message", ""),
            "title": cr.get("title") or ld.get("name") or ld.get("title", ""),
        })
    return out


def _save_run(plan: dict, created: dict, error: str | None = None) -> str:
    RUNS_DIR.mkdir(exist_ok=True)
    f = RUNS_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{plan.get('name', 'run')}.json"
    f.write_text(json.dumps({"plan": plan, "created": created, "error": error},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    return str(f)


# ───────────────────────── 운영 / 최적화 ─────────────────────────
@mcp.tool()
def list_campaigns(only_active: bool = False, limit: int = 50) -> list:
    """캠페인 목록(상태, 목표, 예산)을 조회한다."""
    _need_account()
    params = {"fields": "id,name,objective,status,effective_status,daily_budget,lifetime_budget,created_time",
              "limit": limit}
    if only_active:
        params["effective_status"] = ["ACTIVE"]
    return _get_all(f"{ACCOUNT}/campaigns", params, max_pages=3)


@mcp.tool()
def list_children(parent_id: str) -> dict:
    """캠페인 하위 광고세트와 광고 목록을 조회한다."""
    adsets = _get_all(f"{parent_id}/adsets", {"fields": "id,name,status,effective_status,daily_budget,optimization_goal"})
    ads = _get_all(f"{parent_id}/ads", {"fields": "id,name,status,effective_status,adset_id"})
    return {"adsets": adsets, "ads": ads}


INSIGHT_FIELDS = ("campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,spend,impressions,reach,"
                  "frequency,clicks,inline_link_clicks,inline_link_click_ctr,cpc,cpm,actions,"
                  "cost_per_action_type,purchase_roas")


@mcp.tool()
def get_insights(level: str = "ad", date_preset: str = "last_7d", object_id: str = "",
                 breakdowns: str = "", daily: bool = False) -> list:
    """성과 데이터 조회. level: campaign|adset|ad. date_preset: today|yesterday|last_3d|last_7d|last_14d|last_30d|this_month.
    breakdowns 예: 'age,gender' 또는 'publisher_platform'. object_id 비우면 계정 전체."""
    _need_account()
    params = {"level": level, "date_preset": date_preset, "fields": INSIGHT_FIELDS, "limit": 200}
    if breakdowns:
        params["breakdowns"] = breakdowns
    if daily:
        params["time_increment"] = 1
    return _get_all(f"{object_id or ACCOUNT}/insights", params, max_pages=20)


RESULT_PRIORITY = [
    "offsite_conversion.fb_pixel_purchase", "purchase",
    "offsite_conversion.fb_pixel_lead", "lead",
    "landing_page_view", "link_click",
]


def _action(row: dict, key: str, field: str = "actions") -> float:
    for a in row.get(field) or []:
        if a.get("action_type") == key:
            return float(a.get("value", 0))
    return 0.0


@mcp.tool()
def analyze_performance(date_preset: str = "last_7d", campaign_id: str = "",
                        min_spend: float = 0, result_action: str = "") -> dict:
    """광고 단위 성과를 분석해 PAUSE/SCALE/소재교체 후보를 제안한다(실행은 하지 않음).
    min_spend: 판단에 필요한 최소 지출(계정 통화 기본단위, USD=달러; 0이면 .env MIN_SPEND_FOR_JUDGEMENT). result_action 비우면 구매>리드>LPV>링크클릭 순 자동 선택."""
    min_spend = min_spend or MIN_SPEND
    rows = get_insights("ad", date_preset, campaign_id)
    if not rows:
        return {"message": "해당 기간 데이터 없음"}
    if not result_action:
        result_action = next((k for k in RESULT_PRIORITY if any(_action(r, k) > 0 for r in rows)), "link_click")

    ads = []
    for r in rows:
        spend = float(r.get("spend", 0))
        res = _action(r, result_action)
        ads.append({
            "ad_id": r["ad_id"], "ad": r.get("ad_name"), "adset": r.get("adset_name"),
            "spend": round(spend, 2), "impr": int(r.get("impressions", 0)),
            "link_ctr": round(float(r.get("inline_link_click_ctr", 0) or 0), 2),
            "freq": round(float(r.get("frequency", 0) or 0), 2),
            "results": int(res), "cpa": round(spend / res, 2) if res else None,
            "roas": round(_action(r, "omni_purchase", "purchase_roas") or
                          _action(r, "offsite_conversion.fb_pixel_purchase", "purchase_roas"), 2) or None,
        })
    cpas = [a["cpa"] for a in ads if a["cpa"] and a["spend"] >= min_spend]
    med = statistics.median(cpas) if cpas else None

    for a in ads:
        flags = []
        enough = a["spend"] >= min_spend
        if enough and a["results"] == 0:
            flags.append("PAUSE: 지출 대비 결과 0")
        if med and a["cpa"] and enough:
            if a["cpa"] >= med * 1.5:
                flags.append(f"PAUSE: CPA 중앙값({med:,.2f})의 1.5배 이상")
            elif a["cpa"] <= med * 0.7:
                flags.append("SCALE: CPA 우수 — 해당 광고세트 예산 증액/유사 소재 추가")
        if a["impr"] >= 2000 and a["link_ctr"] < 0.7:
            flags.append("CREATIVE: 링크 CTR 0.7% 미만 — 후킹 약함, 소재 교체")
        if a["freq"] >= 3.5:
            flags.append("FATIGUE: 빈도 3.5+ — 신규 소재 투입")
        a["flags"] = flags or (["OK"] if enough else ["판단 보류: 지출 부족"])
    ads.sort(key=lambda a: (a["cpa"] is None, a["cpa"] or 0))
    return {
        "result_action": result_action, "median_cpa": med, "min_spend": min_spend,
        "total_spend": round(sum(a["spend"] for a in ads), 2), "total_results": sum(a["results"] for a in ads),
        "ads": ads,
    }


@mcp.tool()
def set_status(object_id: str, status: str, cascade: bool = False, confirm: bool = False) -> dict:
    """캠페인/광고세트/광고 상태 변경. status: ACTIVE|PAUSED.
    ACTIVE 는 실제 과금이 시작되므로 사용자 확인 후 confirm=True 로만 실행.
    cascade=True 면 하위 광고세트·광고까지 같은 상태로 변경(캠페인 집행 시작 시 사용)."""
    status = status.upper()
    if status not in ("ACTIVE", "PAUSED"):
        return {"ok": False, "error": "status 는 ACTIVE 또는 PAUSED"}
    if status == "ACTIVE" and not confirm:
        return {"ok": False, "error": "ACTIVE 전환은 과금이 시작됩니다. 사용자 승인 후 confirm=True 로 다시 호출하세요."}
    if status == "ACTIVE" and MAX_TOTAL_DAILY_BUDGET:
        obj = _api("GET", object_id, {"fields": "daily_budget,effective_status"})
        add = int(obj.get("daily_budget") or 0) if obj.get("effective_status") != "ACTIVE" else 0
        if add:
            total = active_daily_budget_total()
            if total + add > MAX_TOTAL_DAILY_BUDGET:
                return {"ok": False, "error": f"켜면 전체 일예산 {total + add:,} 이 상한 {MAX_TOTAL_DAILY_BUDGET:,} 초과 "
                                              f"— 다른 캠페인 예산을 먼저 줄이세요"}
    changed = []
    targets = [object_id]
    if cascade:
        kids = list_children(object_id)
        targets += [a["id"] for a in kids["adsets"]] + [a["id"] for a in kids["ads"]]
    for t in targets:
        _api("POST", t, data={"status": status})
        changed.append(t)
    return {"ok": True, "status": status, "changed": changed}


@mcp.tool()
def update_daily_budget(object_id: str, daily_budget: int, confirm: bool = False) -> dict:
    """캠페인(CBO) 또는 광고세트 일예산 변경. confirm=True 필요.
    증액은 캠페인당 MAX_DAILY_BUDGET, 계정 전체 MAX_TOTAL_DAILY_BUDGET 을 넘을 수 없다(감액은 항상 허용).
    증액은 한 번에 20% 이내 권장(학습 재시작 방지)."""
    cur = _api("GET", object_id, {"fields": "name,daily_budget"})
    old = int(cur.get("daily_budget") or 0)
    if daily_budget <= 0:
        return {"ok": False, "error": "예산은 양수여야 합니다. 끄려면 set_status 를 쓰세요."}
    if daily_budget > old:
        if daily_budget > MAX_DAILY_BUDGET:
            return {"ok": False, "error": f"캠페인 상한 {MAX_DAILY_BUDGET:,} 초과"}
        if MAX_TOTAL_DAILY_BUDGET:
            total = active_daily_budget_total()
            if total - old + daily_budget > MAX_TOTAL_DAILY_BUDGET:
                return {"ok": False, "error": f"증액 후 전체 일예산 {total - old + daily_budget:,} 이 "
                                              f"상한 {MAX_TOTAL_DAILY_BUDGET:,} 초과 (감액을 먼저 적용하세요)"}
    if not confirm:
        pct = f"{(daily_budget - old) / old * 100:+.0f}%" if old else "n/a"
        return {"ok": False, "preview": {"name": cur.get("name"), "from": old, "to": daily_budget, "change": pct},
                "error": "사용자 승인 후 confirm=True 로 다시 호출하세요."}
    _api("POST", object_id, data={"daily_budget": daily_budget})
    return {"ok": True, "name": cur.get("name"), "from": old, "to": daily_budget}


def active_daily_budget_total() -> int:
    """집행 중인 캠페인(CBO) 일예산 합계."""
    camps = _get_all(f"{ACCOUNT}/campaigns", {"fields": "daily_budget", "effective_status": ["ACTIVE"],
                                             "limit": 200}, 5)
    return sum(int(c.get("daily_budget") or 0) for c in camps)


if __name__ == "__main__":
    mcp.run()
