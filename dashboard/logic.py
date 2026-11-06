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
        })
    return pd.DataFrame(rows)


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
                     learning_days: int = 3, exponent: float = 0.7) -> tuple[pd.DataFrame, dict]:
    """목표 그룹(리드/트래픽…) 안에서만 효율을 비교해 총예산을 재배분한다.

    - 학습 중(운영일수 < learning_days)이거나 지출이 min_spend 미만이면 고정
    - 지출이 충분한데 결과 0 → 중지 제안
    - 나머지는 (그룹 평균단가 / 캠페인 단가)^exponent 비율로 조정하되 1회 변경폭은 ±max_step
    - 그룹 간 예산 비율은 현재 비율을 유지 (리드 단가와 트래픽 단가는 비교 불가)
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
        valid = gdf[(gdf["결과"] > 0)]
        g_avg = valid["지출($)"].sum() / valid["결과"].sum() if not valid.empty else None
        locked, flex = {}, {}
        for _, r in gdf.iterrows():
            cur = int(r["cur"])
            if r["운영일수"] < learning_days or r["지출($)"] < min_spend:
                locked[r["id"]] = (cur, "유지", f"학습/데이터 부족 (운영 {r['운영일수']}일, 지출 ${r['지출($)']:.0f})")
            elif r["결과"] == 0:
                locked[r["id"]] = (0, "중지 제안", f"지출 ${r['지출($)']:.0f} 동안 {r['결과명']} 0건")
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
                why = (f"{r['단가명']} ${r['단가($)']:.2f} vs 그룹평균 ${g_avg:.2f} "
                       f"({'효율 좋음' if ratio > 1 else '효율 낮음'} ×{ratio:.2f}) → {pct:+.0f}%")
                if ratio < 0.5:
                    why += " · 소재 교체 권장"
            recs.append(BudgetRec(cid, r["캠페인"], grp, int(r["cur"]), new, action, why))

    out = pd.DataFrame([asdict(r) for r in recs])
    proposed = int(out["제안"].sum())
    summary = {
        "현재합계": cur_total, "목표합계": total_cents, "신규테스트예약": reserve_cents,
        "제안합계": proposed, "미배정": total_cents - reserve_cents - proposed,
    }
    return out, summary
