"""성과 집계와 총예산 재배분 로직 (Streamlit 과 분리된 순수 함수)."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone

import pandas as pd

# 목표별 '결과' 정의: (action_type, 결과 이름, 단가 이름)
RESULT_BY_OBJECTIVE = {
    "OUTCOME_LEADS": ("lead", "리드", "CPL"),
    "OUTCOME_TRAFFIC": ("landing_page_view", "랜딩조회", "조회당 비용"),
    "OUTCOME_SALES": ("offsite_conversion.fb_pixel_purchase", "구매", "CPA"),
    "OUTCOME_ENGAGEMENT": ("post_engagement", "참여", "참여당 비용"),
    "OUTCOME_AWARENESS": ("reach", "도달", "도달당 비용"),
}
GROUP_LABEL = {"OUTCOME_LEADS": "잠재고객", "OUTCOME_TRAFFIC": "트래픽", "OUTCOME_SALES": "판매",
               "OUTCOME_ENGAGEMENT": "참여", "OUTCOME_AWARENESS": "인지도"}


def action_value(row: dict, key: str) -> float:
    if key == "reach":
        return float(row.get("reach", 0) or 0)
    for a in row.get("actions") or []:
        if a.get("action_type") == key:
            return float(a.get("value", 0))
    return 0.0


def campaign_frame(campaigns: list[dict], insights: list[dict]) -> pd.DataFrame:
    """캠페인 목록 + 캠페인 레벨 인사이트 → 표."""
    ins = {r["campaign_id"]: r for r in insights}
    now = datetime.now(timezone.utc)
    rows = []
    for c in campaigns:
        r = ins.get(c["id"], {})
        obj = c.get("objective", "")
        key, res_name, cost_name = RESULT_BY_OBJECTIVE.get(obj, ("link_click", "클릭", "클릭당 비용"))
        spend = float(r.get("spend", 0) or 0)
        results = action_value(r, key) if r else 0.0
        created = datetime.fromisoformat(c["created_time"].replace("+0900", "+09:00")) if c.get("created_time") else now
        rows.append({
            "id": c["id"], "캠페인": c["name"], "objective": obj, "그룹": GROUP_LABEL.get(obj, obj),
            "상태": c.get("effective_status"),
            "일예산($)": int(c.get("daily_budget") or 0) / 100,
            "운영일수": (now - created).days,
            "지출($)": round(spend, 2),
            "노출": int(r.get("impressions", 0) or 0),
            "빈도": round(float(r.get("frequency", 0) or 0), 2),
            "링크CTR(%)": round(float(r.get("inline_link_click_ctr", 0) or 0), 2),
            "CPM($)": round(float(r.get("cpm", 0) or 0), 2),
            "결과": int(results), "결과명": res_name, "단가명": cost_name,
            "단가($)": round(spend / results, 3) if results else None,
            # 트래픽 캠페인은 홈페이지 전환(픽셀 상담신청 등)이 잡혀야 '실제 성과'를 판단할 수 있음
            "전환측정": obj != "OUTCOME_TRAFFIC" or has_site_conversion(r),
        })
    return pd.DataFrame(rows)


SITE_CONVERSION_PREFIXES = ("offsite_conversion.fb_pixel_lead", "offsite_conversion.fb_pixel_complete_registration",
                            "offsite_conversion.fb_pixel_contact", "offsite_conversion.fb_pixel_schedule",
                            "offsite_conversion.fb_pixel_submit_application", "offsite_conversion.custom.")


def has_site_conversion(row: dict) -> bool:
    return any(a.get("action_type", "").startswith(SITE_CONVERSION_PREFIXES) and float(a.get("value", 0)) > 0
               for a in row.get("actions") or [])


def group_summary(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("그룹").agg(지출=("지출($)", "sum"), 결과=("결과", "sum"), 일예산=("일예산($)", "sum"))
    g["평균단가"] = (g["지출"] / g["결과"]).where(g["결과"] > 0)
    return g


def daily_frame(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        out.append({
            "날짜": r["date_start"], "지출($)": float(r.get("spend", 0) or 0),
            "리드": action_value(r, "lead"), "랜딩조회": action_value(r, "landing_page_view"),
        })
    return pd.DataFrame(out).set_index("날짜") if out else pd.DataFrame()


# ───────────────────────── 예산 재배분 ─────────────────────────
@dataclass
class BudgetRec:
    id: str
    캠페인: str
    그룹: str
    현재: int          # cents
    제안: int          # cents (0 = 중지 제안)
    조치: str
    근거: str


def optimize_budgets(df: pd.DataFrame, total_cents: int, reserve_cents: int = 0, max_step: float = 0.2,
                     min_cents: int = 500, cap_cents: int = 10**9, min_spend: float = 10.0,
                     learning_days: int = 3, exponent: float = 0.7,
                     min_results: int = 0) -> tuple[pd.DataFrame, dict]:
    """목표 그룹(리드/트래픽…) 안에서만 효율을 비교해 총예산을 재배분한다.

    - 학습 중(운영일수 < learning_days)이거나 지출이 min_spend 미만이면 고정
    - 지출이 충분한데 결과 0 → 중지 제안
    - 나머지는 (그룹 평균단가 / 캠페인 단가)^exponent 비율로 조정하되 1회 변경폭은 ±max_step
    - 그룹 간 예산 비율은 현재 비율을 유지 (리드 단가와 트래픽 단가는 비교 불가)
    - 홈페이지 전환이 측정되지 않는 트래픽 캠페인은 '측정 불가'로 고정 (방문 단가는 허상 지표)
    - 결과가 min_results 미만이면 우연일 수 있어 고정
    """
    act = df[(df["상태"] == "ACTIVE") & (df["일예산($)"] > 0)].copy()
    if act.empty:
        return pd.DataFrame(), {}
    act["cur"] = (act["일예산($)"] * 100).round().astype(int)
    cur_total = int(act["cur"].sum())
    avail = max(total_cents - reserve_cents, 0)
    recs: list[BudgetRec] = []

    for grp, gdf in act.groupby("그룹"):
        g_target = avail * gdf["cur"].sum() / cur_total
        valid = gdf[(gdf["결과"] > 0) & gdf.get("전환측정", True)]
        g_avg = valid["지출($)"].sum() / valid["결과"].sum() if not valid.empty else None
        locked, flex = {}, {}
        for _, r in gdf.iterrows():
            cur = int(r["cur"])
            if not r.get("전환측정", True):
                locked[r["id"]] = (cur, "유지", "측정 불가 — 홈페이지 상담 신청이 집계되지 않아 효율을 판단할 수 없어요")
            elif r["운영일수"] < learning_days or r["지출($)"] < min_spend:
                locked[r["id"]] = (cur, "유지", f"아직 판단 일러요 (시작 {r['운영일수']}일째, 광고비 ${r['지출($)']:.0f})")
            elif r["결과"] == 0:
                locked[r["id"]] = (0, "중지 제안", f"광고비 ${r['지출($)']:.0f} 썼는데 {r['결과명']} 0건")
            elif r["결과"] < min_results:
                locked[r["id"]] = (cur, "유지", f"{r['결과명']} {r['결과']}건 — {min_results}건 이상 쌓여야 믿을 수 있어요")
            elif g_avg is None:
                locked[r["id"]] = (cur, "유지", "비교할 그룹 평균 없음")
            else:
                ratio = g_avg / r["단가($)"]
                flex[r["id"]] = (cur, ratio)

        remaining = g_target - sum(v[0] for v in locked.values())
        lo = {k: max(min_cents, c * (1 - max_step)) for k, (c, _) in flex.items()}
        # 이미 상한을 넘은 캠페인은 현재값까지만(강제 감액 안 함)
        hi = {k: c if c >= cap_cents else min(cap_cents, c * (1 + max_step)) for k, (c, _) in flex.items()}
        tgt = {k: c * (ratio ** exponent) for k, (c, ratio) in flex.items()}
        for _ in range(30):  # 비례 정규화 + 변경폭 클램프 반복
            tgt = {k: min(hi[k], max(lo[k], v)) for k, v in tgt.items()}
            s = sum(tgt.values())
            if not tgt or abs(s - remaining) < 50:
                break
            free = [k for k in tgt if (s < remaining and tgt[k] < hi[k]) or (s > remaining and tgt[k] > lo[k])]
            if not free:
                break
            f = (remaining - s) / sum(tgt[k] for k in free)
            for k in free:
                tgt[k] *= 1 + f

        for _, r in gdf.iterrows():
            cid = r["id"]
            if cid in locked:
                new, action, why = locked[cid]
            else:
                cur, ratio = flex[cid]
                new = int(round(tgt[cid] / 100) * 100)
                pct = (new - cur) / cur * 100
                action = "증액" if pct >= 2 else "감액" if pct <= -2 else "유지"
                unit = PLAIN.get(grp, ("결과", "1건당 비용"))[1]
                diff = abs(1 / ratio - 1) * 100
                why = (f"{unit} ${r['단가($)']:.2f} — 평균(${g_avg:.2f})보다 "
                       f"{diff:.0f}% {'저렴해요' if ratio > 1 else '비싸요'}")
                if ratio < 0.5:
                    why += " · 소재 교체도 권해요"
            recs.append(BudgetRec(cid, r["캠페인"], grp, int(r["cur"]), new, action, why))

    out = pd.DataFrame([asdict(r) for r in recs])
    proposed = int(out["제안"].sum())
    summary = {
        "현재합계": cur_total, "목표합계": total_cents, "신규테스트예약": reserve_cents,
        "제안합계": proposed, "미배정": total_cents - reserve_cents - proposed,
    }
    return out, summary


# ───────────────────────── 쉬운 말 / 판정 / 할 일 ─────────────────────────
PLAIN = {  # 그룹 → (결과 이름, 단가 이름)
    "잠재고객": ("상담 신청", "상담 1건당 비용"),
    "트래픽": ("홈페이지 방문", "방문 1회당 비용"),
    "판매": ("구매", "구매 1건당 비용"),
    "참여": ("참여", "참여 1회당 비용"),
    "인지도": ("도달", "도달 1명당 비용"),
}
GROUP_NAME = {"잠재고객": "상담 신청 캠페인", "트래픽": "홈페이지 방문 캠페인"}


def verdict(row, group_avg: float | None, learning_days: int = 5, min_spend: float = 10.0,
            min_results: int = 0) -> tuple[str, str]:
    """(아이콘+라벨, 한 줄 설명)."""
    if not row.get("전환측정", True):
        return "⚪ 측정 불가", "상담 신청으로 이어졌는지 집계 안 됨"
    if row["운영일수"] < learning_days:
        return "⏳ 학습 중", f"시작 {row['운영일수']}일째 — {learning_days}일까지는 지켜보기"
    if row["지출($)"] < min_spend:
        return "⚪ 데이터 부족", f"지출 ${row['지출($)']:.0f} — 판단하기엔 아직 적어요"
    if row["결과"] == 0:
        return "🔴 성과 없음", f"${row['지출($)']:.0f} 썼는데 {PLAIN.get(row['그룹'], ('결과',))[0]} 0건"
    if row["결과"] < min_results:
        return "⚪ 데이터 부족", f"{PLAIN.get(row['그룹'], ('결과',))[0]} {row['결과']}건 — {min_results}건 이상 필요"
    if not group_avg:
        return "⚪ 비교 불가", ""
    r = row["단가($)"] / group_avg
    if r <= 0.8:
        return "🟢 효율 좋음", f"평균보다 {int((1 - r) * 100)}% 저렴"
    if r >= 1.25:
        return "🔴 개선 필요", f"평균보다 {int((r - 1) * 100)}% 비쌈"
    return "🟡 보통", "평균 수준"


@dataclass
class Todo:
    key: str
    priority: int            # 낮을수록 먼저
    kind: str                # budget | pause_campaign | creative | pause_ads | wait
    icon: str
    title: str
    detail: str
    button: str
    payload: dict


def build_todos(df: pd.DataFrame, recs: pd.DataFrame, ad_flags: dict[str, list[dict]],
                group_avg: dict[str, float], min_change: float = 0.1) -> list[Todo]:
    """예산 재배분 제안 + 광고 진단 → 우선순위가 매겨진 할 일 카드."""
    todos: list[Todo] = []
    by_id = df.set_index("id")
    if not recs.empty:
        for _, r in recs.iterrows():
            c = by_id.loc[r["id"]]
            res_name, cost_name = PLAIN.get(c["그룹"], ("결과", "단가"))
            avg = group_avg.get(c["그룹"])
            if r["제안"] == 0:
                todos.append(Todo(f"pause_{r['id']}", 0, "pause_campaign", "⛔",
                                  f"{c['캠페인']} — 성과가 없어요",
                                  f"${c['지출($)']:.0f}를 썼는데 {res_name}이 0건이에요. 캠페인을 끄는 걸 권해요.",
                                  "캠페인 끄기", {"id": r["id"], "name": c["캠페인"]}))
            elif abs(r["제안"] - r["현재"]) / r["현재"] < min_change:
                continue  # 자잘한 조정은 할 일에 띄우지 않음 (예산 재배분 화면에서만)
            elif r["제안"] < r["현재"]:
                pct = (r["현재"] - r["제안"]) / r["현재"] * 100
                todos.append(Todo(f"down_{r['id']}", 1 if pct >= 15 else 3, "budget", "🔻",
                                  f"{c['캠페인']} — 비용이 비싸요",
                                  f"{cost_name} ${c['단가($)']:.2f} (평균 ${avg:.2f}). "
                                  f"예산을 줄여 효율 좋은 캠페인으로 옮기세요.",
                                  f"예산 ${r['현재'] / 100:.0f} → ${r['제안'] / 100:.0f} 줄이기",
                                  {"id": r["id"], "name": c["캠페인"], "from": int(r["현재"]), "to": int(r["제안"])}))
            elif r["제안"] > r["현재"]:
                pct = (r["제안"] - r["현재"]) / r["현재"] * 100
                todos.append(Todo(f"up_{r['id']}", 2 if pct >= 10 else 4, "budget", "🔺",
                                  f"{c['캠페인']} — 효율이 좋아요",
                                  f"{cost_name} ${c['단가($)']:.2f} (평균 ${avg:.2f}). 예산을 늘려 {res_name}을 더 받으세요.",
                                  f"예산 ${r['현재'] / 100:.0f} → ${r['제안'] / 100:.0f} 늘리기",
                                  {"id": r["id"], "name": c["캠페인"], "from": int(r["현재"]), "to": int(r["제안"])}))
    for cid, ads in ad_flags.items():
        if cid not in by_id.index:
            continue
        c = by_id.loc[cid]
        bad = [a for a in ads if any(f.startswith(("PAUSE", "CREATIVE", "FATIGUE")) for f in a["flags"])]
        if not bad:
            continue
        fatigue = any(any(f.startswith("FATIGUE") for f in a["flags"]) for a in bad)
        todos.append(Todo(f"creative_{cid}", 2, "creative", "🎨",
                          f"{c['캠페인']} — 소재 {len(bad)}개가 힘이 빠졌어요",
                          ("같은 사람에게 너무 자주 노출되고 있어요. " if fatigue else
                           "클릭률이 낮거나 비용이 높은 소재가 있어요. ")
                          + "약한 소재를 끄고 새 소재를 추가하세요.",
                          "새 소재 만들기",
                          {"campaign_id": cid, "name": c["캠페인"], "bad_ads": [a["ad_id"] for a in bad],
                           "bad_names": [a["ad"] for a in bad], "adset_id": bad[0].get("adset_id")}))
    blind = df[(df["상태"] == "ACTIVE") & (~df["전환측정"])] if "전환측정" in df else df.iloc[0:0]
    if not blind.empty:
        todos.append(Todo("measure", 0, "measure", "📏",
                          f"홈페이지 방문 캠페인 {len(blind)}개 — 성과를 측정할 수 없어요",
                          f"하루 ${blind['일예산($)'].sum():.0f}를 쓰는데, 홈페이지에서 상담 신청이 몇 건 나왔는지 "
                          "Meta가 모르고 있어요. 홈페이지에 '상담 신청 완료' 이벤트를 설치해야 해요.",
                          "설치 가이드 보기", {}))
    learning = df[(df["상태"] == "ACTIVE") & (df["운영일수"] < 5)]
    for _, c in learning.iterrows():
        todos.append(Todo(f"wait_{c['id']}", 9, "wait", "⏳", f"{c['캠페인']} — 학습 중",
                          f"시작 {c['운영일수']}일째예요. 5일 정도는 예산·소재를 바꾸지 말고 지켜보세요.",
                          "", {}))
    return sorted(todos, key=lambda t: t.priority)


# ───────────────────────── 상담 결과(내원·수술) ─────────────────────────
VISIT_STATES = ("내원", "수술확정", "완료")
BOOK_STATES = ("예약",) + VISIT_STATES
WORKED_STATES = ("상담중", "무효(부재·중복)") + BOOK_STATES  # '신규'가 아닌 것 = 결과 기록됨


def outcome_by_campaign(db_path: str, since: str = "", until: str = "") -> pd.DataFrame:
    """상담 통합 대시보드 DB 의 메타광고 리드 → 캠페인별 예약/내원/수술 수."""
    import json
    import sqlite3
    from pathlib import Path
    if not Path(db_path).exists():
        return pd.DataFrame()
    sql = "SELECT status, raw, received_at FROM consultations WHERE channel_key = 'meta_ads'"
    params = []
    if since:
        sql += " AND received_at >= ?"
        params.append(since)
    if until:
        sql += " AND received_at < ?"
        params.append(until + "T23:59:59")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()
    agg: dict = {}
    for status, raw, _ in rows:
        cid = (json.loads(raw or "{}") or {}).get("campaign_id") or "-"
        a = agg.setdefault(cid, {"리드DB": 0, "기록됨": 0, "예약": 0, "내원": 0, "수술": 0, "무효": 0})
        a["리드DB"] += 1
        a["기록됨"] += status in WORKED_STATES
        a["예약"] += status in BOOK_STATES
        a["내원"] += status in VISIT_STATES
        a["수술"] += status == "수술확정"
        a["무효"] += status == "무효(부재·중복)"
    return pd.DataFrame.from_dict(agg, orient="index")


# ───────────────────────── 세그먼트(위치·연령) 분석 ─────────────────────────
def segment_frame(rows: list[dict], keys: tuple[str, ...], result_key: str = "lead") -> pd.DataFrame:
    agg: dict = {}
    for r in rows:
        k = tuple(r.get(x, "") for x in keys)
        a = agg.setdefault(k, [0.0, 0.0])
        a[0] += float(r.get("spend", 0) or 0)
        a[1] += action_value(r, result_key)
    out = pd.DataFrame([(*k, sp, res) for k, (sp, res) in agg.items()], columns=[*keys, "광고비", "결과"])
    out["1건당 비용"] = (out["광고비"] / out["결과"]).where(out["결과"] > 0)
    return out.sort_values("광고비", ascending=False)


PLACEMENT_KO = {
    ("instagram", "instagram_reels"): "인스타 릴스", ("instagram", "feed"): "인스타 피드",
    ("instagram", "instagram_stories"): "인스타 스토리", ("instagram", "instagram_explore"): "인스타 탐색",
    ("instagram", "instagram_explore_grid_home"): "인스타 탐색 홈", ("instagram", "instagram_profile_feed"): "인스타 프로필",
    ("facebook", "facebook_reels"): "페북 릴스", ("facebook", "feed"): "페북 피드",
    ("facebook", "facebook_stories"): "페북 스토리", ("facebook", "facebook_reels_overlay"): "페북 릴스 오버레이",
    ("facebook", "marketplace"): "페북 마켓플레이스", ("facebook", "video_feeds"): "페북 동영상 피드",
    ("audience_network", "classic"): "Audience Network", ("audience_network", "rewarded_video"): "Audience Network 보상형",
    ("threads", "threads_feed"): "스레드",
}


def segment_flags(seg: pd.DataFrame, label_col: str, min_spend: float = 50.0, min_results: int = 0,
                  bad: float = 1.5, good: float = 0.8) -> list[dict]:
    """평균 대비 비싼/싼 구간. 광고비가 충분한 구간만 판단."""
    tot_sp, tot_res = seg["광고비"].sum(), seg["결과"].sum()
    if not tot_res:
        return []
    avg = tot_sp / tot_res
    out = []
    for _, r in seg.iterrows():
        if r["광고비"] < min_spend:
            continue
        if r["결과"] == 0:
            out.append({"구간": r[label_col], "판정": "🔴", "광고비": r["광고비"], "결과": 0, "단가": None,
                        "설명": f"${r['광고비']:.0f} 썼는데 결과 0건"})
        elif r["결과"] >= min_results:
            ratio = r["1건당 비용"] / avg
            if ratio >= bad:
                out.append({"구간": r[label_col], "판정": "🔴", "광고비": r["광고비"], "결과": int(r["결과"]),
                            "단가": r["1건당 비용"], "설명": f"평균(${avg:.2f})의 {ratio:.1f}배"})
            elif ratio <= good:
                out.append({"구간": r[label_col], "판정": "🟢", "광고비": r["광고비"], "결과": int(r["결과"]),
                            "단가": r["1건당 비용"], "설명": f"평균(${avg:.2f})보다 {int((1 - ratio) * 100)}% 저렴"})
    return out
