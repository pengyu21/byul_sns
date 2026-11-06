"""별성형외과 Meta 광고 운영 대시보드.

화면 구성 (Madgicx 의 '할 일 피드' + Motion 의 '소재 타일' 패턴 참고)
  🏠 오늘 할 일   — 핵심 숫자(지난 기간 대비) + 우선순위 할 일 카드 + 원클릭 실행
  📋 캠페인       — 🟢🟡🔴 판정 표, 상세에서 소재 썸네일 타일
  ✨ 새 광고 만들기 — 단계별 마법사 (무엇을 → 누구에게 → 소재 → 확인 → 집행)
  ⚖️ 예산 재배분   — 총예산 안에서 일괄 재배분 (고급)

실행: run_dashboard.bat
"""
from __future__ import annotations

import hashlib
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "meta_ads_mcp"), str(ROOT / "dashboard")]

import server as meta  # noqa: E402  (MCP 서버의 검증된 Meta API 함수 재사용)
import logic  # noqa: E402
import copywriter  # noqa: E402

BLUE = "#2a78d6"
st.set_page_config(page_title="Meta 광고 운영", page_icon="📈", layout="wide")
st.markdown("""<style>
div[data-testid="stMetricValue"] {font-size: 1.9rem;}
.todo-title {font-weight: 700; font-size: 1.05rem; margin-bottom: .1rem;}
.todo-detail {color: #52514e; font-size: .92rem;}
.muted {color: #6b6a66; font-size: .85rem;}
</style>""", unsafe_allow_html=True)


# ═════════════════════════ 데이터 (5분 캐시) ═════════════════════════
PERIODS = {"최근 7일": 7, "최근 14일": 14, "최근 30일": 30}


def period_range(days: int, back: int = 0) -> tuple[str, str]:
    """오늘 제외 최근 days 일. back=1 이면 바로 앞 기간."""
    until = date.today() - timedelta(days=1 + back * days)
    since = until - timedelta(days=days - 1)
    return since.isoformat(), until.isoformat()


@st.cache_data(ttl=300, show_spinner=False)
def load_campaigns():
    return meta.list_campaigns(only_active=True, limit=200)


@st.cache_data(ttl=300, show_spinner=False)
def load_insights(since: str, until: str, level: str = "campaign"):
    return meta.get_insights(level, since=since, until=until)


@st.cache_data(ttl=300, show_spinner=False)
def load_daily(since: str, until: str):
    return meta.get_insights("account", daily=True, since=since, until=until)


@st.cache_data(ttl=300, show_spinner=False)
def load_ad_perf(campaign_id: str, preset: str):
    return meta.analyze_performance(preset, campaign_id).get("ads", [])


@st.cache_data(ttl=300, show_spinner=False)
def load_ad_creatives(campaign_id: str):
    return meta.list_ads_with_creatives(campaign_id)


@st.cache_data(ttl=300, show_spinner=False)
def load_adsets(campaign_id: str):
    return meta.list_children(campaign_id)["adsets"]


@st.cache_data(ttl=3600, show_spinner=False)
def load_lead_forms():
    return meta.list_lead_forms()


@st.cache_data(ttl=600, show_spinner=False)
def load_overview():
    return meta.account_overview()


def refresh():
    st.cache_data.clear()


def money(v: float, d: int = 0) -> str:
    return f"${v:,.{d}f}"


# ═════════════════════════ 공통 상태 ═════════════════════════
if "period" not in st.session_state:
    st.session_state.period = "최근 7일"
DAYS = PERIODS[st.session_state.period or "최근 7일"]
PRESET = {7: "last_7d", 14: "last_14d", 30: "last_30d"}[DAYS]
since, until = period_range(DAYS)
p_since, p_until = period_range(DAYS, back=1)

try:
    with st.spinner("Meta에서 데이터를 불러오는 중…"):
        OV = load_overview()
        CAMPS = load_campaigns()
        DF = logic.campaign_frame(CAMPS, load_insights(since, until))
        PREV = logic.campaign_frame(CAMPS, load_insights(p_since, p_until))
except Exception as e:  # noqa: BLE001
    st.error(f"Meta 연결 실패: {e}")
    st.stop()

GS = logic.group_summary(DF) if not DF.empty else pd.DataFrame()
GROUP_AVG = {g: GS.loc[g, "평균단가"] for g in GS.index if pd.notna(GS.loc[g, "평균단가"])}
NAME = DF.set_index("id")["캠페인"].to_dict() if not DF.empty else {}


def ad_rows(cid: str) -> list[dict]:
    """광고 성과 + 소재(썸네일/문구/광고세트) 합치기."""
    crs = {c["ad_id"]: c for c in load_ad_creatives(cid)}
    rows = []
    for a in load_ad_perf(cid, PRESET):
        c = crs.get(a["ad_id"], {})
        rows.append({**a, "thumbnail": c.get("thumbnail"), "body": c.get("body", ""),
                     "adset_id": c.get("adset_id"), "status": c.get("status")})
    return rows


def reference_ads(cids: list[str]) -> tuple[list[dict], list[dict]]:
    pool = []
    for cid in cids:
        for a in ad_rows(cid):
            if a["body"] and a["spend"] >= meta.MIN_SPEND:
                pool.append({"문구": a["body"][:150], "단가": a["cpa"], "클릭률": a["link_ctr"],
                             "결과": a["results"], "지출": a["spend"]})
    ranked = sorted([p for p in pool if p["단가"]], key=lambda p: p["단가"])
    losers = (ranked[-3:][::-1] if len(ranked) > 5 else []) + [p for p in pool if not p["단가"]][:2]
    return ranked[:5], losers


# ═════════════════════════ 확인 팝업 ═════════════════════════
@st.dialog("변경 확인")
def confirm_budget(items: list[dict]):
    st.write("아래 예산을 **Meta 광고 계정에 바로 적용**할게요.")
    for it in items:
        arrow = "🔺" if it["to"] > it["from"] else "🔻"
        st.markdown(f"- {arrow} **{it['name']}**: ${it['from'] / 100:.0f} → **${it['to'] / 100:.0f}** /일")
    c1, c2 = st.columns(2)
    if c1.button("적용하기", type="primary", width="stretch"):
        res = []  # 감액 먼저 → 증액 (전체 상한을 넘지 않도록)
        for it in sorted(items, key=lambda x: x["to"] - x["from"]):
            r = meta.update_daily_budget(it["id"], it["to"], confirm=True)
            res.append((it["name"], r.get("ok"), r.get("error", "")))
        st.session_state.flash = [f"{'✅' if ok else '❌'} {n} {err}" for n, ok, err in res]
        st.session_state.done = st.session_state.get("done", set()) | {it["key"] for it in items}
        refresh()
        st.rerun()
    if c2.button("취소", width="stretch"):
        st.rerun()


@st.dialog("끄기 확인")
def confirm_pause(ids: list[str], names: list[str], key: str, what: str):
    st.write(f"아래 {what}을(를) **일시중지**할게요. 나중에 다시 켤 수 있어요.")
    for n in names:
        st.markdown(f"- {n}")
    c1, c2 = st.columns(2)
    if c1.button("끄기", type="primary", width="stretch"):
        for i in ids:
            meta.set_status(i, "PAUSED")
        st.session_state.flash = [f"⏸️ {len(ids)}개 {what}을(를) 껐어요"]
        st.session_state.done = st.session_state.get("done", set()) | {key}
        refresh()
        st.rerun()
    if c2.button("취소", width="stretch"):
        st.rerun()


def flash():
    for m in st.session_state.pop("flash", []):
        st.toast(m)


def period_picker():
    st.segmented_control("기간", list(PERIODS), key="period", label_visibility="collapsed")


# ═════════════════════════ 🏠 오늘 할 일 ═════════════════════════
def page_home():
    flash()
    top = st.columns([3, 2])
    top[0].markdown(f"### 안녕하세요 👋  \n<span class='muted'>{OV['account']['name']} · "
                    f"{since[5:]} ~ {until[5:]} 기준 · 지난 기간({p_since[5:]} ~ {p_until[5:]})과 비교</span>",
                    unsafe_allow_html=True)
    with top[1]:
        period_picker()

    def tot(d, col, grp=None):
        x = d if grp is None else d[d["그룹"] == grp]
        return float(x[col].sum()) if not x.empty else 0.0

    def unit(d, grp):
        r = tot(d, "결과", grp)
        return tot(d, "지출($)", grp) / r if r else None

    k = st.columns(5)
    sp, psp = tot(DF, "지출($)"), tot(PREV, "지출($)")
    k[0].metric("광고비", money(sp), f"{(sp - psp) / psp * 100:+.0f}%" if psp else None, delta_color="off")
    for i, (grp, label) in enumerate([("잠재고객", "상담 신청"), ("트래픽", "홈페이지 방문")]):
        n, pn = tot(DF, "결과", grp), tot(PREV, "결과", grp)
        u, pu = unit(DF, grp), unit(PREV, grp)
        k[1 + i * 2].metric(label, f"{int(n):,}건", f"{(n - pn) / pn * 100:+.0f}%" if pn else None)
        k[2 + i * 2].metric(f"{label} 1{'건' if grp == '잠재고객' else '회'}당 비용", money(u, 2 if grp == "잠재고객" else 3) if u else "–",
                            f"{(u - pu) / pu * 100:+.0f}%" if u and pu else None, delta_color="inverse",
                            help="낮을수록 좋아요. 초록색 = 지난 기간보다 저렴해짐")

    st.divider()
    recs, _ = logic.optimize_budgets(DF, int(round(DF.loc[DF["상태"] == "ACTIVE", "일예산($)"].sum() * 100)),
                                     cap_cents=meta.MAX_DAILY_BUDGET, min_spend=meta.MIN_SPEND, learning_days=5)
    with st.spinner("소재 진단 중…"):
        flags = {cid: ad_rows(cid) for cid in DF.loc[DF["운영일수"] >= 5, "id"]}
    todos = [t for t in logic.build_todos(DF, recs, flags, GROUP_AVG)
             if t.key not in st.session_state.get("done", set())]
    actions = [t for t in todos if t.kind != "wait"]
    waits = [t for t in todos if t.kind == "wait"]

    head = st.columns([3, 1])
    head[0].subheader(f"오늘 할 일 {len(actions)}개" if actions else "오늘 할 일이 없어요 🎉")
    budget_items = [{"key": t.key, **t.payload} for t in actions if t.kind == "budget"]
    if len(budget_items) > 1 and head[1].button(f"예산 조정 {len(budget_items)}건 한 번에", width="stretch"):
        confirm_budget(budget_items)

    for t in actions:
        with st.container(border=True):
            c1, c2 = st.columns([4, 1.4], vertical_alignment="center")
            c1.markdown(f"<div class='todo-title'>{t.icon} {t.title}</div>"
                        f"<div class='todo-detail'>{t.detail}</div>", unsafe_allow_html=True)
            if t.kind == "budget":
                if c2.button(t.button, key=t.key, width="stretch",
                             type="primary" if "늘리기" in t.button else "secondary"):
                    confirm_budget([{"key": t.key, **t.payload}])
            elif t.kind == "pause_campaign":
                if c2.button(t.button, key=t.key, width="stretch"):
                    confirm_pause([t.payload["id"]], [t.payload["name"]], t.key, "캠페인")
            elif t.kind == "creative":
                with c2:
                    if st.button(t.button, key=t.key, width="stretch", type="primary"):
                        start_wizard_add(t.payload["campaign_id"], None)
                    if st.button("약한 소재 끄기", key=t.key + "_off", width="stretch"):
                        confirm_pause(t.payload["bad_ads"], t.payload["bad_names"], t.key + "_off", "광고")

    small = int(((recs["제안"] != recs["현재"]) & (recs["제안"] > 0)).sum()) if not recs.empty else 0
    small -= len(budget_items)
    if small > 0:
        st.caption(f"그 밖에 10% 미만의 작은 예산 조정 {small}건은 ⚖️ 예산 재배분 화면에 있어요.")
    if waits:
        with st.expander(f"⏳ 학습 중인 캠페인 {len(waits)}개 — 지켜보기만 하세요"):
            for t in waits:
                st.markdown(f"- **{t.title.split(' — ')[0]}** · {t.detail}")

    daily = logic.daily_frame(load_daily(since, until))
    if not daily.empty:
        st.divider()
        st.subheader("일별 추이")
        c = st.columns(2)
        with c[0]:
            st.caption("상담 신청 (건)")
            st.bar_chart(daily["리드"], color=BLUE, height=200)
        with c[1]:
            st.caption("광고비 ($)")
            st.bar_chart(daily["지출($)"], color=BLUE, height=200)


# ═════════════════════════ 📋 캠페인 ═════════════════════════
def page_campaigns():
    flash()
    if "open_cid" in st.session_state:
        return campaign_detail(st.session_state.open_cid)
    top = st.columns([3, 2])
    top[0].markdown("### 캠페인")
    with top[1]:
        period_picker()

    for grp in [g for g in ["잠재고객", "트래픽"] if g in set(DF["그룹"])]:
        res_name, cost_name = logic.PLAIN[grp]
        sub = DF[DF["그룹"] == grp].copy()
        avg = GROUP_AVG.get(grp)
        v = sub.apply(lambda r: logic.verdict(r, avg, 5, meta.MIN_SPEND), axis=1)
        sub["판정"], sub["한줄 설명"] = [x[0] for x in v], [x[1] for x in v]
        order = {"🔴": 0, "🟡": 1, "🟢": 2, "⏳": 3, "⚪": 4}
        sub = sub.sort_values(by="판정", key=lambda s: s.str[0].map(order))
        st.markdown(f"#### {logic.GROUP_NAME.get(grp, grp)}  "
                    f"<span class='muted'>평균 {cost_name} "
                    f"{money(avg, 2 if grp == '잠재고객' else 3) if avg else '–'}</span>", unsafe_allow_html=True)
        view = sub.rename(columns={"결과": res_name, "단가($)": cost_name, "지출($)": "광고비",
                                   "일예산($)": "일예산", "링크CTR(%)": "클릭률"})
        ev = st.dataframe(
            view[["판정", "캠페인", "한줄 설명", "일예산", "광고비", res_name, cost_name, "클릭률"]],
            hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key=f"tbl_{grp}",
            column_config={
                "일예산": st.column_config.NumberColumn(format="$%.0f"),
                "광고비": st.column_config.NumberColumn(format="$%.0f"),
                cost_name: st.column_config.NumberColumn(format="$%.2f" if grp == "잠재고객" else "$%.3f"),
                "클릭률": st.column_config.NumberColumn(format="%.1f%%"),
                res_name: st.column_config.NumberColumn(format="%d"),
            })
        if ev.selection.rows:
            st.session_state.open_cid = view.iloc[ev.selection.rows[0]]["id"]
            st.rerun()
    st.caption("👆 캠페인 행을 클릭하면 소재별 성과를 볼 수 있어요.")


def campaign_detail(cid: str):
    row = DF.set_index("id").loc[cid]
    if st.button("← 캠페인 목록"):
        del st.session_state.open_cid
        st.rerun()
    res_name, cost_name = logic.PLAIN.get(row["그룹"], ("결과", "단가"))
    st.markdown(f"## {row['캠페인']}")
    v = logic.verdict(row, GROUP_AVG.get(row["그룹"]), 5, meta.MIN_SPEND)
    m = st.columns(5)
    m[0].metric("판정", v[0], help=v[1])
    m[1].metric("일예산", money(row["일예산($)"]))
    m[2].metric("광고비", money(row["지출($)"]))
    m[3].metric(res_name, f"{row['결과']:,}건")
    m[4].metric(cost_name, money(row["단가($)"], 2) if row["단가($)"] else "–")

    ads = sorted(ad_rows(cid), key=lambda a: (a["cpa"] is None, a["cpa"] or 0))
    head = st.columns([3, 1])
    head[0].subheader(f"소재 {len(ads)}개")
    if head[1].button("➕ 새 소재 추가", type="primary", width="stretch"):
        start_wizard_add(cid, None)
    if not ads:
        st.info("이 기간에 집행된 광고가 없어요.")
        return
    best = next((a["cpa"] for a in ads if a["cpa"]), None)
    cols = st.columns(4)
    for i, a in enumerate(ads):
        with cols[i % 4], st.container(border=True):
            if a["thumbnail"]:
                st.image(a["thumbnail"], width="stretch")
            bad = any(f.startswith(("PAUSE", "CREATIVE", "FATIGUE")) for f in a["flags"])
            badge = "🏆 최고 효율" if a["cpa"] and a["cpa"] == best else ("⚠️ 약함" if bad else "")
            st.markdown(f"**{a['ad'][:22]}** {badge}")
            st.markdown(f"<span class='muted'>1건당 <b>{money(a['cpa'], 2) if a['cpa'] else '–'}</b> · "
                        f"{res_name} {a['results']}건<br>광고비 {money(a['spend'])} · 클릭률 {a['link_ctr']:.1f}% · "
                        f"노출빈도 {a['freq']:.1f}</span>", unsafe_allow_html=True)
            if bad:
                st.caption(" / ".join(f.split(": ", 1)[-1] for f in a["flags"]))
                if st.button("끄기", key=f"off_{a['ad_id']}", width="stretch"):
                    confirm_pause([a["ad_id"]], [a["ad"]], f"off_{a['ad_id']}", "광고")


# ═════════════════════════ ✨ 새 광고 만들기 (마법사) ═════════════════════════
STEPS_NEW = ["무엇을", "누구에게", "소재", "확인·생성", "집행"]
STEPS_ADD = ["대상", "소재", "확인·추가", "집행"]


def wiz() -> dict:
    if "wiz" not in st.session_state:
        st.session_state.wiz = {"mode": "new", "step": 0}
    return st.session_state.wiz


def reset_wiz():
    st.session_state.wiz = {"mode": "new", "step": 0}
    st.session_state.pop("plan", None)


def start_wizard_add(cid: str, adset_id: str | None):
    st.session_state.wiz = {"mode": "add", "step": 0, "campaign_id": cid, "adset_id": adset_id}
    st.session_state.pop("plan", None)
    st.session_state.pop("open_cid", None)
    st.switch_page(PAGES["new"])


def stepper(steps: list[str], cur: int):
    cols = st.columns(len(steps))
    for i, (c, s) in enumerate(zip(cols, steps)):
        mark = "✅" if i < cur else ("🔵" if i == cur else "⚪")
        c.markdown(f"<div style='text-align:center'>{mark}<br>"
                   f"<span style='font-weight:{700 if i == cur else 400}'>{i + 1}. {s}</span></div>",
                   unsafe_allow_html=True)
    st.progress((cur + 1) / len(steps))


def nav(w: dict, next_label: str | None = "다음 →", next_ok: bool = True) -> bool:
    c1, _, c3 = st.columns([1, 3, 1.3])
    if w["step"] > 0 and c1.button("← 이전", width="stretch"):
        w["step"] -= 1
        st.rerun()
    return bool(next_label) and c3.button(next_label, type="primary", width="stretch", disabled=not next_ok)


def page_new():
    flash()
    w = wiz()
    steps = STEPS_NEW if w["mode"] == "new" else STEPS_ADD
    head = st.columns([4, 1])
    head[0].markdown("### ✨ 새 광고 만들기" if w["mode"] == "new"
                     else f"### 🎨 새 소재 추가 — {NAME.get(w['campaign_id'], '')}")
    if head[1].button("처음부터", width="stretch"):
        reset_wiz()
        st.rerun()
    stepper(steps, w["step"])
    st.write("")
    {"무엇을": step_what, "누구에게": step_who, "대상": step_target, "소재": step_creative,
     "확인·생성": step_confirm, "확인·추가": step_confirm, "집행": step_go}[steps[w["step"]]](w)


def best_form(forms: list[dict], proc: str) -> str:
    """시술명과 글자가 가장 많이 겹치는 신청서, 동점이면 누적 신청이 많은 것."""
    def score(f):
        name = f.get("name", "")
        overlap = sum(ch in name for ch in set(proc.replace(" ", "")))
        return (proc.strip() in name, overlap, int(f.get("leads_count") or 0))
    return max(forms, key=score)["id"]


def related_campaigns(proc: str, goal: str | None) -> list[str]:
    """시술명 앞글자(코/눈…)가 들어간 캠페인 + 부족하면 같은 목표 캠페인으로 채워 최소 3개."""
    kw = proc.strip()[:1]
    refs = [i for i, n in NAME.items() if kw and kw in n]
    grp = {"lead": "잠재고객", "traffic": "트래픽"}.get(goal or "")
    if len(refs) < 3 and grp:
        refs += [i for i in DF.loc[DF["그룹"] == grp, "id"] if i not in refs]
    return refs or list(NAME)


def step_what(w):
    with st.container(border=True):
        w["proc"] = st.text_input("어떤 시술을 광고할까요?", w.get("proc", ""), placeholder="예) 코재수술, 눈밑지방재배치")
        goal = st.radio("무엇을 받고 싶으세요?", ["📝 상담 신청 받기 (페이스북 안에서 바로 신청)", "🌐 홈페이지 방문 늘리기"],
                        index=0 if w.get("goal", "lead") == "lead" else 1)
        w["goal"] = "lead" if goal.startswith("📝") else "traffic"
        if "잠재고객" in GROUP_AVG:
            st.caption(f"💡 이 계정에서는 '상담 신청' 캠페인의 효율이 가장 좋아요 "
                       f"(최근 평균 상담 1건 {money(GROUP_AVG['잠재고객'], 2)})")
        c = st.columns(2)
        w["budget"] = c[0].number_input("하루 예산 ($)", 5.0, meta.MAX_DAILY_BUDGET / 100,
                                        float(w.get("budget", 20.0)), 1.0)
        if w["goal"] == "lead":
            forms = load_lead_forms()
            if forms:
                ids = [f["id"] for f in forms]
                label = {f["id"]: f"{f.get('name', f['id'])} (누적 {f.get('leads_count', '?')}건)" for f in forms}
                if w.get("form_id") not in ids or w.get("form_for") != w["proc"]:
                    w["form_id"], w["form_for"] = best_form(forms, w["proc"]), w["proc"]
                w["form_id"] = c[1].selectbox("상담 신청서", ids, index=ids.index(w["form_id"]),
                                              format_func=label.get,
                                              help="시술 이름과 비슷한 신청서를 자동으로 골랐어요")
            else:
                c[1].error("상담 신청서를 찾지 못했어요. 광고관리자에서 먼저 만들어 주세요.")
                w["form_id"] = None
            w["link"] = st.text_input("홈페이지 주소 (선택)", w.get("link", ""), placeholder="https://")
        else:
            w["form_id"] = None
            w["link"] = c[1].text_input("보낼 홈페이지 주소", w.get("link", ""), placeholder="https://")
    ok = bool(w["proc"].strip()) and bool(w.get("form_id") if w["goal"] == "lead"
                                          else w.get("link", "").startswith("https://"))
    if nav(w, next_ok=ok):
        w["step"] += 1
        st.rerun()


def step_who(w):
    with st.container(border=True):
        c = st.columns(2)
        w["age"] = c[0].slider("나이", 18, 65, tuple(w.get("age", (25, 44))))
        w["gender"] = c[1].radio("성별", ["전체", "여성", "남성"], horizontal=True,
                                 index=["전체", "여성", "남성"].index(w.get("gender", "전체")))
        w["adv"] = st.toggle("Meta가 반응 좋은 사람에게 알아서 넓혀서 보여주기 (추천)", w.get("adv", True))
        with st.expander("관심사 추가 (선택)"):
            kw = st.text_input("관심사 검색", placeholder="예) 성형, 뷰티")
            if kw:
                found = meta.search_interests(kw, 15)
                sel = st.multiselect("선택", found, format_func=lambda x: f"{x['name']} ({x['audience']})")
                if sel:
                    w["interests"] = [{"id": x["id"], "name": x["name"]} for x in sel]
            if w.get("interests"):
                st.caption("선택됨: " + ", ".join(i["name"] for i in w["interests"]) +
                           " → 관심사 광고세트가 하나 더 만들어져 비교 테스트돼요")
    t = {"age_min": w["age"][0], "age_max": w["age"][1]}
    if w["gender"] != "전체":
        t["genders"] = [2 if w["gender"] == "여성" else 1]
    try:
        est = meta.estimate_audience(t)
        st.info(f"👥 예상 도달 규모: 약 **{est['mau_lower'] / 10000:,.0f}만 ~ {est['mau_upper'] / 10000:,.0f}만 명**")
    except Exception:  # noqa: BLE001
        pass
    if nav(w):
        w["step"] += 1
        st.rerun()


def step_target(w):
    cid = w["campaign_id"]
    adsets = load_adsets(cid)
    with st.container(border=True):
        st.markdown(f"**{NAME.get(cid, cid)}** 캠페인에 새 소재를 추가해요.")
        ids = [a["id"] for a in adsets]
        names = {a["id"]: a["name"] for a in adsets}
        w["adset_id"] = st.selectbox("어느 광고세트에 넣을까요?", ids,
                                     index=ids.index(w["adset_id"]) if w.get("adset_id") in ids else 0,
                                     format_func=names.get)
        st.caption("💡 이미 효율이 검증된 타겟에 새 소재만 넣는 방식이라, 새 캠페인보다 학습이 빨라요.")
    if nav(w, next_ok=bool(w.get("adset_id"))):
        w["step"] += 1
        st.rerun()


def step_creative(w):
    plan = st.session_state.get("plan")
    with st.container(border=True):
        w["extra"] = st.text_area("강조하고 싶은 점 (선택)", w.get("extra", ""),
                                  placeholder="예) 11월 상담 이벤트, 3D CT 정밀 진단, 회복 기간 부담 줄이기")
        if w["mode"] == "new":
            refs = related_campaigns(w.get("proc") or "", w.get("goal"))
        else:
            refs = [w["campaign_id"]]
        with st.expander(f"AI가 참고할 기존 캠페인 {len(refs)}개 (자동 선택됨)"):
            refs = st.multiselect("참고 캠페인", list(NAME), default=refs, format_func=lambda i: NAME[i],
                                  label_visibility="collapsed")
        c = st.columns([1, 1.3, 2], vertical_alignment="bottom")
        n = c[0].number_input("소재 개수", 2, 5, 3)
        engine = copywriter.backend()
        c[2].caption("엔진: " + {"subscription": "Claude 구독 (API 비용 없음)", "api": "Claude API (사용량 과금)",
                                 "none": "없음 — 직접 입력"}[engine])
        if engine != "none" and c[1].button("🤖 AI로 소재 만들기" if not plan else "🔄 다시 만들기",
                                            type="primary", width="stretch"):
            brief = ({"시술": w.get("proc"), "목표": "상담 신청" if w.get("goal") == "lead" else "홈페이지 방문",
                      "타겟": f"{w.get('age', (25, 44))[0]}-{w.get('age', (25, 44))[1]}세 {w.get('gender', '전체')}",
                      "강조점": w["extra"]} if w["mode"] == "new" else
                     {"캠페인": NAME.get(w["campaign_id"]), "강조점": w["extra"],
                      "지시": "이 캠페인에서 성과 좋은 소재의 패턴을 살린 신규 변형"})
            with st.status("AI가 소재를 만드는 중… (1~3분)", expanded=True) as s:
                st.write("① 기존 소재 성과를 모으는 중")
                winners, losers = reference_ads(refs)
                st.write(f"② 좋은 소재 {len(winners)}개 · 약한 소재 {len(losers)}개 분석 → 새 소재 작성 중")
                try:
                    st.session_state.plan = copywriter.generate(brief, winners, losers, int(n)).model_dump()
                    s.update(label="완료!", state="complete")
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    s.update(label=f"실패: {e}", state="error")
        if engine == "none" and not plan and c[1].button("직접 입력하기", width="stretch"):
            st.session_state.plan = {"insights": [], "compliance_check": [], "variants": [
                {"angle": f"소재{i + 1}", "primary_text": "", "headline": "", "description": "",
                 "image_headline": "", "image_subtext": "", "badge": "", "cta": "SIGN_UP"} for i in range(int(n))]}
            st.rerun()

    if plan:
        if plan.get("insights"):
            with st.expander("📈 AI가 기존 성과에서 찾은 패턴", expanded=True):
                for x in plan["insights"]:
                    st.markdown(f"- {x}")
        w["creatives"] = creative_cards(plan["variants"])
        if plan.get("compliance_check"):
            with st.expander("⚖️ 의료광고 규정 점검"):
                for x in plan["compliance_check"]:
                    st.markdown(f"- {x}")
    ok = bool(plan) and all(c["primary_text"].strip() for c in w.get("creatives", []))
    if nav(w, next_ok=ok):
        w["step"] += 1
        st.rerun()


THEMES = {"네이비": ("#111827", "#F59E0B", "#FFFFFF"), "화이트": ("#F8FAFC", "#2a78d6", "#111827"),
          "로즈": ("#4C1D2F", "#F9A8D4", "#FFFFFF"), "그린": ("#0F3D3E", "#5EEAD4", "#FFFFFF")}
CTA_KO = {"SIGN_UP": "가입하기", "LEARN_MORE": "더 알아보기", "APPLY_NOW": "지금 신청하기",
          "GET_QUOTE": "견적 받기", "CONTACT_US": "문의하기", "BOOK_TRAVEL": "예약하기"}


def creative_cards(variants: list[dict]) -> list[dict]:
    out = []
    cols = st.columns(len(variants))
    for i, (col, v) in enumerate(zip(cols, variants)):
        with col, st.container(border=True):
            st.markdown(f"**소재 {i + 1}** · {v.get('angle', '')}")
            theme = st.session_state.get(f"th{i}") or "네이비"
            photo = st.session_state.get(f"ph{i}")
            bg_path = ""
            if photo:
                data = photo.getvalue()
                up = meta.CREATIVE_DIR / "uploads"
                up.mkdir(parents=True, exist_ok=True)
                bg_path = str(up / f"{hashlib.md5(data).hexdigest()[:10]}{Path(photo.name).suffix}")
                if not Path(bg_path).exists():
                    Path(bg_path).write_bytes(data)
            cta_text = "상담 신청하기" if v["cta"] in ("SIGN_UP", "APPLY_NOW", "GET_QUOTE", "CONTACT_US") else "자세히 보기"
            bg, accent, fg = THEMES[theme]
            params = [v["image_headline"], v["image_subtext"], cta_text, v.get("badge", ""), theme, bg_path]
            path = meta.CREATIVE_DIR / f"ad_{hashlib.md5('|'.join(params).encode()).hexdigest()[:10]}.png"
            if not path.exists():  # 입력이 바뀔 때만 다시 그림
                meta.render_ad_image(v["image_headline"], v["image_subtext"], cta_text=cta_text,
                                     badge=v.get("badge", ""), bg_color=bg, accent_color=accent, text_color=fg,
                                     bg_image_path=bg_path, file_name=path.stem)
            st.image(str(path), width="stretch")
            v["primary_text"] = st.text_area("본문", v["primary_text"], key=f"pt{i}", height=170)
            v["headline"] = st.text_input("제목", v["headline"], key=f"hl{i}")
            with st.expander("✏️ 이미지·버튼 수정"):
                v["image_headline"] = st.text_input("이미지 큰 글씨", v["image_headline"], key=f"ih{i}")
                v["image_subtext"] = st.text_input("이미지 작은 글씨", v["image_subtext"], key=f"is{i}")
                v["badge"] = st.text_input("라벨", v.get("badge", ""), key=f"bd{i}")
                v["description"] = st.text_input("설명", v["description"], key=f"ds{i}")
                v["cta"] = st.selectbox("버튼", list(CTA_KO),
                                        index=list(CTA_KO).index(v["cta"]) if v["cta"] in CTA_KO else 0,
                                        format_func=CTA_KO.get, key=f"cta{i}")
                st.selectbox("색상", list(THEMES), key=f"th{i}")
                st.file_uploader("배경 사진", type=["png", "jpg", "jpeg"], key=f"ph{i}")
            if v.get("rationale"):
                st.caption(f"💡 {v['rationale']}")
            out.append({"name": f"{datetime.now():%y%m%d}_{v.get('angle', '')[:12]}_{i + 1}",
                        "image_path": str(path), "primary_text": v["primary_text"], "headline": v["headline"],
                        "description": v["description"], "cta": v["cta"]})
    return out


def build_plan(w) -> dict:
    t = {"age_min": w["age"][0], "age_max": w["age"][1]}
    if w["gender"] != "전체":
        t["genders"] = [2 if w["gender"] == "여성" else 1]
    proc = w["proc"].strip()
    adsets = [{"name": f"{proc}_브로드_{t['age_min']}-{t['age_max']}", "targeting": t, "advantage_audience": w["adv"]}]
    if w.get("interests"):
        adsets.append({"name": f"{proc}_관심사", "targeting": {**t, "flexible_spec": [{"interests": w["interests"]}]}})
    lead = w["goal"] == "lead"
    return {"name": f"{datetime.now():%y%m%d}_{proc}_{'잠재고객' if lead else '트래픽'}",
            "objective": "OUTCOME_LEADS" if lead else "OUTCOME_TRAFFIC", "daily_budget": int(w["budget"] * 100),
            "link": w.get("link") or (f"https://www.facebook.com/{meta.PAGE_ID}" if lead else ""),
            "lead_form_id": w.get("form_id") if lead else None, "adsets": adsets, "creatives": w["creatives"]}


def step_confirm(w):
    with st.container(border=True):
        if w["mode"] == "new":
            plan = build_plan(w)
            st.markdown(f"#### {plan['name']}")
            c = st.columns(4)
            c[0].metric("목표", "상담 신청" if w["goal"] == "lead" else "홈페이지 방문")
            c[1].metric("하루 예산", money(w["budget"]))
            c[2].metric("타겟", f"{w['age'][0]}~{w['age'][1]}세 {w['gender']}")
            c[3].metric("만들어질 광고", f"{len(plan['adsets']) * len(plan['creatives'])}개")
            check = meta.launch_campaign(plan, dry_run=True)
        else:
            st.markdown(f"#### {NAME.get(w['campaign_id'])} 에 소재 {len(w['creatives'])}개 추가")
            check = meta.add_ads_to_adset(w["adset_id"], w["creatives"], dry_run=True)
        cols = st.columns(len(w["creatives"]))
        for col, c in zip(cols, w["creatives"]):
            col.image(c["image_path"], width="stretch")
            col.caption(c["primary_text"][:80] + "…")
        if check["ok"]:
            st.success("✅ 세팅 검증 통과 — **일시중지 상태로** 만들어져서, 아직 돈이 나가지 않아요.")
        else:
            st.error("고쳐야 할 점: " + " / ".join(check.get("errors", [])))
    if nav(w, next_label="🚀 만들기 (일시중지)", next_ok=check["ok"]):
        with st.spinner("이미지 업로드 및 광고 생성 중…"):
            r = meta.launch_campaign(build_plan(w), dry_run=False) if w["mode"] == "new" \
                else meta.add_ads_to_adset(w["adset_id"], w["creatives"], dry_run=False)
        if r["ok"]:
            w["result"] = r
            w["step"] += 1
            refresh()
            st.rerun()
        else:
            st.error(r.get("error") or " / ".join(r.get("errors", [])))


def step_go(w):
    r = w["result"]
    with st.container(border=True):
        if w["mode"] == "new":
            st.success(f"🎉 캠페인이 만들어졌어요 (일시중지 상태 · 광고 {len(r['created']['ads'])}개)")
            st.link_button("광고관리자에서 미리보기", r["ads_manager"])
        else:
            st.success(f"🎉 새 소재 {len(r['created'])}개가 추가됐어요 (일시중지 상태)")
        st.markdown("#### 🚦 집행 시작")
        st.caption("의료광고는 심의를 통과한 소재만 집행할 수 있어요. 심의필 번호를 받은 뒤 입력하세요. "
                   "지금 안 켜도 돼요 — 나중에 광고관리자에서 켜도 됩니다.")
        c = st.columns([2, 1], vertical_alignment="bottom")
        num = c[0].text_input("심의필 번호", placeholder="예) 제2026-1234-5678호")
        if c[1].button("▶️ 집행 시작", type="primary", width="stretch", disabled=not num.strip()):
            go_live(w, num)
    if st.button("새로 만들기"):
        reset_wiz()
        st.rerun()


@st.dialog("집행 시작 확인")
def go_live(w, num):
    budget_txt = f"하루 {money(w['budget'])}" if w["mode"] == "new" else "기존 광고세트 예산 안에서"
    st.write(f"심의필 번호 **{num}** 로 집행을 시작해요. **{budget_txt}** 과금이 시작됩니다.")
    if st.button("집행 시작", type="primary", width="stretch"):
        r = w["result"]
        if w["mode"] == "new":
            res = meta.set_status(r["created"]["campaign_id"], "ACTIVE", cascade=True, confirm=True)
            msg = "✅ 집행 시작!" if res["ok"] else f"❌ {res['error']}"
        else:
            for a in r["created"]:
                meta.set_status(a["ad_id"], "ACTIVE", confirm=True)
            msg = "✅ 새 소재 집행 시작!"
        st.session_state.flash = [msg]
        reset_wiz()
        refresh()
        st.rerun()


# ═════════════════════════ ⚖️ 예산 재배분 ═════════════════════════
def page_budget():
    flash()
    st.markdown("### ⚖️ 예산 재배분")
    st.caption("총 예산은 그대로 두고, 같은 목표의 캠페인끼리 비교해서 **효율 좋은 곳으로 예산을 옮겨요.** "
               "한 번에 크게 바꾸면 학습이 다시 시작되니 최대 ±20%씩만 조정해요.")
    cur_total = round(DF.loc[DF["상태"] == "ACTIVE", "일예산($)"].sum())
    c = st.columns(3, vertical_alignment="bottom")
    total = c[0].number_input("하루 총 예산 ($)", 10.0,
                              meta.MAX_TOTAL_DAILY_BUDGET / 100 if meta.MAX_TOTAL_DAILY_BUDGET else 10000.0,
                              float(cur_total), 5.0)
    reserve = c[1].number_input("새 광고 테스트용으로 남겨둘 금액 ($)", 0.0, float(total), 0.0, 5.0)
    with c[2].popover("⚙️ 고급 설정", width="stretch"):
        step = st.slider("1회 최대 변경폭", 5, 30, 20, format="%d%%")
        learn = st.slider("학습 보호 기간 (일)", 0, 14, 5)
    recs, summ = logic.optimize_budgets(DF, int(total * 100), int(reserve * 100), step / 100,
                                        cap_cents=meta.MAX_DAILY_BUDGET, min_spend=meta.MIN_SPEND,
                                        learning_days=learn)
    if recs.empty:
        st.info("집행 중인 캠페인이 없어요.")
        return
    recs = recs.assign(diff=recs["제안"] - recs["현재"])
    for grp in recs["그룹"].unique():
        with st.container(border=True):
            st.markdown(f"#### {logic.GROUP_NAME.get(grp, grp)}")
            for _, r in recs[recs["그룹"] == grp].sort_values("diff").iterrows():
                if r["제안"] == 0:
                    arrow, color = "⛔", "#e34948"
                else:
                    arrow = "🔺" if r["diff"] > 0 else ("🔻" if r["diff"] < 0 else "➖")
                    color = "#1baf7a" if r["diff"] > 0 else ("#e34948" if r["diff"] < 0 else "#6b6a66")
                a, b, d = st.columns([3, 1.4, 3])
                a.markdown(f"{arrow} **{r['캠페인']}**")
                b.markdown(f"${r['현재'] / 100:.0f} → <b style='color:{color}'>${r['제안'] / 100:.0f}</b>",
                           unsafe_allow_html=True)
                d.markdown(f"<span class='muted'>{r['근거']}</span>", unsafe_allow_html=True)
    m = st.columns(4)
    m[0].metric("지금", money(summ["현재합계"] / 100))
    m[1].metric("조정 후", money(summ["제안합계"] / 100))
    m[2].metric("테스트용", money(summ["신규테스트예약"] / 100))
    m[3].metric("남는 금액", money(summ["미배정"] / 100), help="변경폭 제한 때문에 다 못 쓴 금액이에요")
    items = [{"key": f"bulk_{r['id']}", "id": r["id"], "name": r["캠페인"], "from": int(r["현재"]),
              "to": int(r["제안"])} for _, r in recs.iterrows() if r["diff"] != 0 and r["제안"] > 0]
    if st.button(f"✅ 예산 {len(items)}건 적용하기", type="primary", disabled=not items):
        confirm_budget(items)


# ═════════════════════════ 네비게이션 ═════════════════════════
PAGES = {
    "home": st.Page(page_home, title="오늘 할 일", icon="🏠", default=True),
    "camps": st.Page(page_campaigns, title="캠페인", icon="📋", url_path="campaigns"),
    "new": st.Page(page_new, title="새 광고 만들기", icon="✨", url_path="new"),
    "budget": st.Page(page_budget, title="예산 재배분", icon="⚖️", url_path="budget"),
}
with st.sidebar:
    st.markdown(f"**{OV['account']['name']}**  \n<span class='muted'>{OV['page'].get('name')} · "
                f"{OV['account']['account_status_text']}</span>", unsafe_allow_html=True)
    st.button("🔄 최신 데이터로", on_click=refresh, width="stretch")
    st.caption(f"🛡️ 새 광고는 모두 일시중지로 생성 · 캠페인당 최대 {money(meta.MAX_DAILY_BUDGET / 100)}/일 · "
               f"전체 최대 {money(meta.MAX_TOTAL_DAILY_BUDGET / 100)}/일 · 집행은 심의번호 필수")
st.navigation(list(PAGES.values()), position="top").run()
