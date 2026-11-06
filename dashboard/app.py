"""Meta 광고 운영 대시보드 — 성과 / 예산 최적화 / 신규 캠페인 / 소재 교체.

실행: run_dashboard.bat  (또는 .venv\\Scripts\\python -m streamlit run dashboard/app.py)
"""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "meta_ads_mcp"), str(ROOT / "dashboard")]

import server as meta  # noqa: E402  (MCP 서버의 검증된 API 함수 재사용)
import logic  # noqa: E402
import copywriter  # noqa: E402

BLUE = "#2a78d6"
st.set_page_config(page_title="별성형외과 Meta 광고 운영", page_icon="📈", layout="wide")


# ───────────────────────── 데이터 (5분 캐시) ─────────────────────────
@st.cache_data(ttl=300, show_spinner="Meta에서 데이터를 불러오는 중…")
def load_campaigns():
    return meta.list_campaigns(only_active=True, limit=200)


@st.cache_data(ttl=300, show_spinner=False)
def load_insights(preset: str):
    return meta.get_insights("campaign", preset)


@st.cache_data(ttl=300, show_spinner=False)
def load_daily(preset: str):
    return meta.get_insights("account", preset, daily=True)


@st.cache_data(ttl=300, show_spinner=False)
def load_ad_perf(campaign_id: str, preset: str):
    return meta.analyze_performance(preset, campaign_id)


@st.cache_data(ttl=300, show_spinner=False)
def load_ad_creatives(campaign_id: str):
    return meta.list_ads_with_creatives(campaign_id)


@st.cache_data(ttl=300, show_spinner=False)
def load_adsets(campaign_id: str):
    return meta.list_children(campaign_id)["adsets"]


@st.cache_data(ttl=3600, show_spinner=False)
def load_lead_forms():
    return meta.list_lead_forms()


def refresh():
    st.cache_data.clear()


def usd(cents: int | float) -> str:
    return f"${cents / 100:,.2f}"


def ad_table(campaign_id: str, preset: str) -> pd.DataFrame:
    perf = load_ad_perf(campaign_id, preset)
    crs = {c["ad_id"]: c for c in load_ad_creatives(campaign_id)}
    rows = []
    for a in perf.get("ads", []):
        c = crs.get(a["ad_id"], {})
        rows.append({
            "썸네일": c.get("thumbnail"), "광고": a["ad"], "광고세트": a["adset"], "지출($)": a["spend"],
            "결과": a["results"], "단가($)": a["cpa"], "링크CTR(%)": a["link_ctr"], "빈도": a["freq"],
            "진단": " / ".join(a["flags"]), "문구": (c.get("body") or "")[:120], "ad_id": a["ad_id"],
            "adset_id": c.get("adset_id"),
        })
    return pd.DataFrame(rows)


def reference_ads(campaign_ids: list[str], preset: str) -> tuple[list[dict], list[dict]]:
    """AI 카피 생성용: 기존 광고 중 단가 좋은/나쁜 소재와 문구."""
    pool = []
    for cid in campaign_ids:
        t = ad_table(cid, preset)
        for _, r in t.iterrows():
            if r["문구"] and r["지출($)"] >= meta.MIN_SPEND:
                pool.append({"문구": r["문구"], "단가": r["단가($)"], "링크CTR": r["링크CTR(%)"],
                             "결과": r["결과"], "지출": r["지출($)"]})
    with_cost = sorted([p for p in pool if p["단가"]], key=lambda p: p["단가"])
    winners = with_cost[:5]
    losers = with_cost[-3:][::-1] if len(with_cost) > 5 else []
    losers += [p for p in pool if not p["단가"]][:2]
    return winners, losers


# ───────────────────────── 사이드바 ─────────────────────────
with st.sidebar:
    st.header("📈 Meta 광고 운영")
    try:
        ov = meta.account_overview()
        acct = ov["account"]
        st.caption(f"**{acct['name']}** · {acct['account_status_text']} · {acct['currency']}")
        st.caption(f"페이지: {ov['page'].get('name')} · 픽셀 {'✅' if ov['pixel_id'] else '❌'}")
    except Exception as e:  # noqa: BLE001
        st.error(f"Meta 연결 실패: {e}")
        st.stop()
    preset = st.selectbox("기간", ["last_7d", "last_3d", "last_14d", "last_30d", "yesterday", "this_month"],
                          format_func=lambda p: {"last_7d": "최근 7일", "last_3d": "최근 3일", "last_14d": "최근 14일",
                                                 "last_30d": "최근 30일", "yesterday": "어제",
                                                 "this_month": "이번 달"}[p])
    st.button("🔄 새로고침", on_click=refresh, width="stretch")
    st.divider()
    st.caption("🛡️ 안전장치")
    st.caption(f"· 새 광고는 모두 **일시중지**로 생성\n\n· 캠페인 상한 {usd(meta.MAX_DAILY_BUDGET)}/일"
               f"\n\n· 전체 상한 {usd(meta.MAX_TOTAL_DAILY_BUDGET)}/일\n\n· 집행 시작은 심의번호 입력 필수")

campaigns = load_campaigns()
df = logic.campaign_frame(campaigns, load_insights(preset))

tab_perf, tab_opt, tab_new, tab_swap = st.tabs(["📊 성과", "🎯 예산 최적화", "✨ 신규 캠페인", "🖼️ 소재 교체"])

# ═════════════════════════ 1. 성과 ═════════════════════════
with tab_perf:
    gs = logic.group_summary(df) if not df.empty else pd.DataFrame()
    k = st.columns(5)
    k[0].metric("총 지출", f"${df['지출($)'].sum():,.0f}")
    k[1].metric("집행 중 일예산", f"${df['일예산($)'].sum():,.0f}")
    if "잠재고객" in gs.index:
        k[2].metric("리드", f"{int(gs.loc['잠재고객', '결과']):,}건")
        k[3].metric("평균 CPL", f"${gs.loc['잠재고객', '평균단가']:.2f}")
    if "트래픽" in gs.index:
        k[4].metric("랜딩조회 (조회당)", f"{int(gs.loc['트래픽', '결과']):,}",
                    f"${gs.loc['트래픽', '평균단가']:.3f}/회", delta_color="off")

    def highlight(row, avg):
        if row["단가($)"] is None or pd.isna(row["단가($)"]) or not avg:
            return [""] * len(row)
        r = row["단가($)"] / avg
        color = "background-color: rgba(227,73,72,.15)" if r >= 1.5 else \
            "background-color: rgba(27,175,122,.15)" if r <= 0.7 else ""
        return [color if c == "단가($)" else "" for c in row.index]

    for grp in [g for g in ["잠재고객", "트래픽", "판매", "참여", "인지도"] if g in set(df.get("그룹", []))]:
        sub = df[df["그룹"] == grp].sort_values("단가($)", na_position="last")
        avg = gs.loc[grp, "평균단가"] if grp in gs.index else None
        st.subheader(f"{grp} 캠페인 — {sub['단가명'].iloc[0]} 기준")
        st.caption("🟥 그룹 평균의 1.5배 이상 · 🟩 0.7배 이하")
        cols = ["캠페인", "일예산($)", "운영일수", "지출($)", "결과", "단가($)", "링크CTR(%)", "빈도", "CPM($)"]
        st.dataframe(sub[cols].style.apply(highlight, avg=avg, axis=1).format(
            {"단가($)": "{:.3f}", "지출($)": "{:.2f}", "일예산($)": "{:.0f}"}, na_rep="–"),
            hide_index=True, width="stretch")

    daily = logic.daily_frame(load_daily(preset))
    if not daily.empty:
        st.subheader("일별 추이")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.caption("지출 ($)")
            st.bar_chart(daily["지출($)"], color=BLUE, height=220)
        with c2:
            st.caption("리드 (건)")
            st.bar_chart(daily["리드"], color=BLUE, height=220)
        with c3:
            st.caption("랜딩조회 (회)")
            st.bar_chart(daily["랜딩조회"], color=BLUE, height=220)

    st.subheader("캠페인 상세 — 광고(소재)별")
    pick = st.selectbox("캠페인", df["id"], format_func=lambda i: df.set_index("id").loc[i, "캠페인"],
                        key="perf_pick")
    if pick:
        t = ad_table(pick, preset)
        if t.empty:
            st.info("이 기간 광고 데이터가 없습니다.")
        else:
            st.dataframe(t.drop(columns=["ad_id", "adset_id"]), hide_index=True, width="stretch",
                         column_config={"썸네일": st.column_config.ImageColumn(width="small")})

# ═════════════════════════ 2. 예산 최적화 ═════════════════════════
with tab_opt:
    st.markdown("목표가 같은 캠페인끼리만 효율을 비교해서, **총 일예산 안에서** 재배분합니다. "
                "리드 단가와 트래픽 단가는 비교할 수 없으므로 그룹 간 예산 비율은 유지해요.")
    cur_total = int(round(df.loc[df["상태"] == "ACTIVE", "일예산($)"].sum() * 100))
    c = st.columns(4)
    total = c[0].number_input("총 일예산 ($)", min_value=10.0,
                              max_value=meta.MAX_TOTAL_DAILY_BUDGET / 100 if meta.MAX_TOTAL_DAILY_BUDGET else 10000.0,
                              value=cur_total / 100, step=5.0)
    reserve = c[1].number_input("신규 테스트용 예약 ($)", min_value=0.0, value=0.0, step=5.0,
                                help="새 캠페인/소재 테스트에 쓸 돈을 미리 빼둡니다")
    step = c[2].slider("1회 최대 변경폭", 5, 30, 20, format="%d%%",
                       help="한 번에 크게 바꾸면 학습이 다시 시작돼요. 20% 이내 권장")
    learn = c[3].slider("학습 보호 기간(일)", 0, 14, 5, help="이 기간 미만인 캠페인은 건드리지 않아요")

    recs, summ = logic.optimize_budgets(df, int(total * 100), int(reserve * 100), step / 100,
                                        cap_cents=meta.MAX_DAILY_BUDGET, min_spend=meta.MIN_SPEND,
                                        learning_days=learn)
    if recs.empty:
        st.info("집행 중인 캠페인이 없습니다.")
    else:
        view = recs.assign(현재=recs["현재"] / 100, 제안=recs["제안"] / 100,
                           변화=(recs["제안"] - recs["현재"]) / 100)
        st.dataframe(view[["캠페인", "그룹", "현재", "제안", "변화", "조치", "근거"]], hide_index=True,
                     width="stretch",
                     column_config={k: st.column_config.NumberColumn(format="$%.0f") for k in ["현재", "제안", "변화"]})
        m = st.columns(4)
        m[0].metric("현재 합계", usd(summ["현재합계"]))
        m[1].metric("제안 합계", usd(summ["제안합계"]))
        m[2].metric("신규 테스트 예약", usd(summ["신규테스트예약"]))
        m[3].metric("미배정", usd(summ["미배정"]), help="변경폭 제한 때문에 다 못 쓴 금액 → 신규 테스트에 쓰는 걸 권장")

        changes = recs[recs["제안"] != recs["현재"]]
        st.markdown(f"**변경 대상 {len(changes)}개 캠페인**")
        ok = st.checkbox("위 예산 변경을 Meta 광고 계정에 실제로 적용하는 것에 동의합니다.", key="opt_ok")
        if st.button("✅ 예산 변경 적용", type="primary", disabled=not ok or changes.empty):
            log = []
            # 감액 → 중지 → 증액 순서 (전체 상한을 넘지 않도록)
            order = pd.concat([changes[(changes["제안"] > 0) & (changes["제안"] < changes["현재"])],
                               changes[changes["제안"] == 0],
                               changes[changes["제안"] > changes["현재"]]])
            for _, r in order.iterrows():
                try:
                    if r["제안"] == 0:
                        res = meta.set_status(r["id"], "PAUSED")
                    else:
                        res = meta.update_daily_budget(r["id"], int(r["제안"]), confirm=True)
                    log.append((r["캠페인"], "✅" if res.get("ok") else f"❌ {res.get('error')}"))
                except Exception as e:  # noqa: BLE001
                    log.append((r["캠페인"], f"❌ {e}"))
            st.dataframe(pd.DataFrame(log, columns=["캠페인", "결과"]), hide_index=True)
            refresh()


# ═════════════════════════ 소재 편집기 (탭 3·4 공용) ═════════════════════════
def creative_editor(key: str, plan_variants: list[dict]) -> list[dict]:
    """AI 가 만든 변형을 편집하고 이미지를 렌더링한다. 최종 creatives 리스트 반환."""
    out = []
    colors = {"네이비": ("#111827", "#F59E0B"), "화이트": ("#F8FAFC", "#2a78d6"),
              "로즈": ("#4C1D2F", "#F9A8D4"), "그린": ("#0F3D3E", "#5EEAD4")}
    for i, v in enumerate(plan_variants):
        with st.expander(f"소재 {i + 1} · {v.get('angle', '')}", expanded=True):
            left, right = st.columns([3, 2])
            with left:
                v["primary_text"] = st.text_area("본문", v["primary_text"], key=f"{key}_pt{i}", height=150)
                a, b = st.columns(2)
                v["headline"] = a.text_input("헤드라인", v["headline"], key=f"{key}_hl{i}")
                v["description"] = b.text_input("설명", v["description"], key=f"{key}_ds{i}")
                a, b, c = st.columns(3)
                v["image_headline"] = a.text_input("이미지 큰 글씨", v["image_headline"], key=f"{key}_ih{i}")
                v["image_subtext"] = b.text_input("이미지 작은 글씨", v["image_subtext"], key=f"{key}_is{i}")
                v["badge"] = c.text_input("라벨", v.get("badge", ""), key=f"{key}_bd{i}")
                a, b, c = st.columns(3)
                ctas = ["SIGN_UP", "LEARN_MORE", "APPLY_NOW", "GET_QUOTE", "CONTACT_US", "BOOK_TRAVEL"]
                v["cta"] = a.selectbox("버튼", ctas, index=ctas.index(v["cta"]) if v["cta"] in ctas else 0,
                                       key=f"{key}_cta{i}")
                theme = b.selectbox("색상", list(colors), key=f"{key}_th{i}")
                photo = c.file_uploader("배경 사진(선택)", type=["png", "jpg", "jpeg"], key=f"{key}_ph{i}")
                if v.get("rationale"):
                    st.caption(f"💡 {v['rationale']}")
            with right:
                bg_path = ""
                if photo:
                    data = photo.getvalue()
                    up = meta.CREATIVE_DIR / "uploads"
                    up.mkdir(parents=True, exist_ok=True)
                    bg_path = str(up / f"{hashlib.md5(data).hexdigest()[:10]}{Path(photo.name).suffix}")
                    if not Path(bg_path).exists():
                        Path(bg_path).write_bytes(data)
                bg, accent = colors[theme]
                cta_text = "상담 신청하기" if v["cta"] in ("SIGN_UP", "APPLY_NOW", "GET_QUOTE", "CONTACT_US") else "자세히 보기"
                params = [v["image_headline"], v["image_subtext"], cta_text, v.get("badge", ""), theme, bg_path]
                digest = hashlib.md5("|".join(params).encode("utf-8")).hexdigest()[:10]
                path = meta.CREATIVE_DIR / f"{key}_{digest}.png"
                if not path.exists():  # 입력이 바뀔 때만 다시 그림
                    meta.render_ad_image(
                        v["image_headline"], v["image_subtext"], cta_text=cta_text, badge=v.get("badge", ""),
                        bg_color=bg, accent_color=accent, text_color="#111827" if theme == "화이트" else "#FFFFFF",
                        bg_image_path=bg_path, file_name=path.stem)
                st.image(str(path), width="stretch")
            out.append({"name": f"{datetime.now():%y%m%d}_{v.get('angle', '')[:12]}_{i + 1}",
                        "image_path": str(path), "primary_text": v["primary_text"], "headline": v["headline"],
                        "description": v["description"], "cta": v["cta"]})
    return out


def ai_panel(key: str, brief: dict, ref_ids: list[str]):
    if not copywriter.available():
        st.warning("Claude Code(구독)를 찾지 못해 AI 생성을 쓸 수 없어요. 아래에 직접 입력하세요.")
        if st.button("빈 소재 3개 만들기", key=f"{key}_blank"):
            st.session_state[f"{key}_plan"] = {"insights": [], "compliance_check": [], "variants": [
                {"angle": f"앵글{i + 1}", "primary_text": "", "headline": "", "description": "",
                 "image_headline": "", "image_subtext": "", "badge": "", "cta": "SIGN_UP"} for i in range(3)]}
        return
    n = st.slider("만들 소재 수", 2, 5, 3, key=f"{key}_n")
    st.caption("엔진: " + ("Claude 구독 (API 비용 없음)" if copywriter.backend() == "subscription" else "Claude API (사용량 과금)"))
    if st.button("🤖 AI로 소재 생성", type="primary", key=f"{key}_gen"):
        with st.spinner("기존 성과를 분석하고 소재를 만드는 중… (1~3분)"):
            winners, losers = reference_ads(ref_ids, preset)
            try:
                plan = copywriter.generate(brief, winners, losers, n)
                st.session_state[f"{key}_plan"] = plan.model_dump()
            except Exception as e:  # noqa: BLE001
                st.error(f"생성 실패: {e}")


def show_plan_notes(plan: dict):
    if plan.get("insights"):
        st.info("**기존 성과에서 읽은 패턴**\n\n" + "\n".join(f"- {x}" for x in plan["insights"]))
    if plan.get("compliance_check"):
        st.warning("**규정 점검**\n\n" + "\n".join(f"- {x}" for x in plan["compliance_check"]))


def activation_gate(key: str, label: str) -> bool:
    st.markdown("##### 🚦 집행 시작")
    st.caption("의료광고는 심의를 통과한 소재만 집행할 수 있어요. 심의필 번호를 입력해야 버튼이 켜집니다.")
    a, b = st.columns([1, 2])
    num = a.text_input("심의필 번호", key=f"{key}_num", placeholder="예) 제2026-1234-5678호")
    ok = b.checkbox(f"{label} — 실제 과금이 시작되는 것에 동의합니다.", key=f"{key}_agree")
    return st.button("▶️ 집행 시작", key=f"{key}_go", disabled=not (num.strip() and ok))


# ═════════════════════════ 3. 신규 캠페인 ═════════════════════════
with tab_new:
    st.markdown("기존 캠페인 성과를 참고해 AI가 소재를 만들고, **일시중지 상태로** 캠페인을 세팅합니다.")
    c = st.columns(3)
    proc = c[0].text_input("시술", placeholder="예) 코재수술")
    objective = c[1].selectbox("목표", ["잠재고객(인스턴트 양식)", "트래픽"])
    budget = c[2].number_input("일예산 ($)", 5.0, meta.MAX_DAILY_BUDGET / 100, 20.0, 1.0)

    form_id = None
    if objective.startswith("잠재"):
        forms = load_lead_forms()
        if forms:
            fi = st.selectbox("상담신청 양식", range(len(forms)),
                              format_func=lambda i: f"{forms[i].get('name', forms[i]['id'])} "
                                                    f"(누적 {forms[i].get('leads_count', '?')}건)")
            form_id = forms[fi]["id"]
        else:
            st.error("사용 중인 인스턴트 양식을 찾지 못했어요. 광고관리자에서 양식을 먼저 만들어 주세요.")
    link = st.text_input("랜딩 URL", placeholder="https://… (트래픽은 필수, 잠재고객은 선택)")

    st.markdown("**타겟**")
    c = st.columns(4)
    age = c[0].slider("나이", 18, 65, (25, 44))
    gender = c[1].radio("성별", ["전체", "여성", "남성"], horizontal=True)
    adv = c[2].checkbox("Advantage+ 타겟 확장", value=True, help="Meta가 성과 좋은 사람에게 타겟을 넓혀요")
    kw = c[3].text_input("관심사 검색", placeholder="예) 성형")
    interests = []
    if kw:
        found = meta.search_interests(kw, 15)
        sel = st.multiselect("관심사 선택 (선택 시 관심사 광고세트가 추가돼요)", found,
                             format_func=lambda x: f"{x['name']} ({x['audience']})")
        interests = [{"id": x["id"], "name": x["name"]} for x in sel]

    st.markdown("**AI 소재**")
    ref_default = [i for i in df["id"] if proc and proc[:2] in df.set_index("id").loc[i, "캠페인"]] or list(df["id"])
    refs = st.multiselect("참고할 기존 캠페인", df["id"], default=ref_default,
                          format_func=lambda i: df.set_index("id").loc[i, "캠페인"])
    extra = st.text_area("추가 요청 (혜택, 톤, 강조점 등)", placeholder="예) 11월 상담 이벤트 · 3D CT 정밀 진단 강조")
    brief = {"시술": proc, "목표": objective, "타겟": f"{age[0]}-{age[1]}세 {gender}", "랜딩": link, "추가요청": extra}
    ai_panel("new", brief, refs)

    plan = st.session_state.get("new_plan")
    if plan:
        show_plan_notes(plan)
        creatives = creative_editor("new", plan["variants"])
        targeting = {"age_min": age[0], "age_max": age[1]}
        if gender != "전체":
            targeting["genders"] = [2 if gender == "여성" else 1]
        adsets = [{"name": f"{proc}_브로드_{age[0]}-{age[1]}", "targeting": targeting, "advantage_audience": adv}]
        if interests:
            adsets.append({"name": f"{proc}_관심사", "targeting": {**targeting, "flexible_spec": [{"interests": interests}]}})
        name = f"{datetime.now():%y%m%d}_{proc}_{'잠재고객' if form_id else '트래픽'}"
        camp_plan = {"name": name, "objective": "OUTCOME_LEADS" if form_id else "OUTCOME_TRAFFIC",
                     "daily_budget": int(budget * 100), "link": link or "", "lead_form_id": form_id,
                     "adsets": adsets, "creatives": creatives}
        if form_id and not link:
            camp_plan["link"] = f"https://www.facebook.com/{meta.PAGE_ID}"

        a, b = st.columns(2)
        if a.button("🔍 세팅 검증", key="new_dry"):
            r = meta.launch_campaign(camp_plan, dry_run=True)
            (st.success if r["ok"] else st.error)("검증 통과" if r["ok"] else "\n".join(r["errors"]))
            for w in r.get("warnings", []):
                st.warning(w)
        if b.button("🚀 캠페인 생성 (일시중지 상태)", type="primary", key="new_launch"):
            with st.spinner("이미지 업로드 및 캠페인 생성 중…"):
                r = meta.launch_campaign(camp_plan, dry_run=False)
            if r["ok"]:
                st.session_state["new_created"] = r
                refresh()
            else:
                st.error(r.get("error") or r.get("errors"))
    created = st.session_state.get("new_created")
    if created:
        cid = created["created"]["campaign_id"]
        st.success(f"생성 완료 (일시중지): 광고 {len(created['created']['ads'])}개 · [광고관리자에서 보기]({created['ads_manager']})")
        if activation_gate("new_act", "새 캠페인 집행"):
            total_now = meta.active_daily_budget_total()
            nb = int(budget * 100)
            if meta.MAX_TOTAL_DAILY_BUDGET and total_now + nb > meta.MAX_TOTAL_DAILY_BUDGET:
                st.error(f"집행 시 전체 일예산 {usd(total_now + nb)} 이 상한 {usd(meta.MAX_TOTAL_DAILY_BUDGET)} 을 넘어요. "
                         "예산 최적화 탭에서 다른 캠페인을 먼저 줄여 주세요.")
            else:
                r = meta.set_status(cid, "ACTIVE", cascade=True, confirm=True)
                st.success(f"집행 시작 ✅ ({len(r['changed'])}개 항목)") if r["ok"] else st.error(r["error"])
                st.session_state.pop("new_created")
                refresh()

# ═════════════════════════ 4. 소재 교체 ═════════════════════════
with tab_swap:
    st.markdown("성과가 나쁜 소재를 끄고, **이미 효율이 검증된 광고세트에** 새 소재를 추가합니다.")
    cid = st.selectbox("캠페인", df["id"], format_func=lambda i: df.set_index("id").loc[i, "캠페인"], key="swap_c")
    if cid:
        t = ad_table(cid, preset)
        if not t.empty:
            st.dataframe(t.drop(columns=["ad_id", "adset_id"]), hide_index=True, width="stretch",
                         column_config={"썸네일": st.column_config.ImageColumn(width="small")})
            bad = t[t["진단"].str.contains("PAUSE|CREATIVE", na=False)]
            if not bad.empty:
                kill = st.multiselect("끌 광고 (진단: PAUSE/CREATIVE)", bad["ad_id"], default=list(bad["ad_id"]),
                                      format_func=lambda a: t.set_index("ad_id").loc[a, "광고"])
                if st.button("⏸️ 선택한 광고 끄기", disabled=not kill):
                    for a in kill:
                        meta.set_status(a, "PAUSED")
                    st.success(f"{len(kill)}개 광고를 껐어요.")
                    refresh()

        adsets = load_adsets(cid)
        asid = st.selectbox("새 소재를 넣을 광고세트", [a["id"] for a in adsets],
                            format_func=lambda i: next(a["name"] for a in adsets if a["id"] == i), key="swap_as")
        cname = df.set_index("id").loc[cid, "캠페인"]
        extra2 = st.text_area("추가 요청", key="swap_extra", placeholder="예) 회복 기간 부담을 덜어주는 앵글 위주로")
        brief2 = {"캠페인": cname, "목표": df.set_index("id").loc[cid, "그룹"], "추가요청": extra2,
                  "지시": "이 캠페인의 성과 좋은 소재 패턴을 살린 신규 변형"}
        ai_panel("swap", brief2, [cid])
        plan2 = st.session_state.get("swap_plan")
        if plan2 and asid:
            show_plan_notes(plan2)
            creatives2 = creative_editor("swap", plan2["variants"])
            a, b = st.columns(2)
            if a.button("🔍 검증", key="swap_dry"):
                r = meta.add_ads_to_adset(asid, creatives2, dry_run=True)
                (st.success if r["ok"] else st.error)("검증 통과" if r["ok"] else "\n".join(r["errors"]))
            if b.button("➕ 광고세트에 추가 (일시중지)", type="primary", key="swap_add"):
                with st.spinner("업로드 중…"):
                    r = meta.add_ads_to_adset(asid, creatives2, dry_run=False)
                if r["ok"]:
                    st.session_state["swap_created"] = r["created"]
                    refresh()
                else:
                    st.error("\n".join(r["errors"]))
        added = st.session_state.get("swap_created")
        if added:
            st.success(f"새 광고 {len(added)}개 추가 완료 (일시중지)")
            if activation_gate("swap_act", "새 소재 집행"):
                for a in added:
                    meta.set_status(a["ad_id"], "ACTIVE", confirm=True)
                st.success("새 소재 집행 시작 ✅")
                st.session_state.pop("swap_created")
                refresh()
