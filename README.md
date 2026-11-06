# byul_sns

## Meta(페이스북·인스타그램) 광고 자동화 — Claude Code + MCP

브리프 한 줄로 **타겟 설계 → 카피·이미지 소재 생성 → 캠페인/광고세트/광고 일괄 세팅**까지 자동화.

| 구성 | 설명 |
|---|---|
| `meta_ads_mcp/server.py` | Meta Marketing API MCP 서버 (13개 도구) |
| `.claude/skills/meta-ads-launch` | `/meta-ads-launch` 원클릭 런칭 스킬 |
| `.claude/skills/meta-ads-optimize` | `/meta-ads-optimize` 성과 분석·최적화 스킬 |
| `.mcp.json` | Claude Code MCP 등록 |
| `dashboard/` | 운영 대시보드 (Streamlit) — 성과 / 예산 최적화 / 신규 캠페인 / 소재 교체 |

### 대시보드
`run_dashboard.bat` 더블클릭 → http://localhost:8502

| 화면 | 내용 |
|---|---|
| 🏠 오늘 할 일 | 핵심 숫자(지난 기간 대비) + 우선순위 할 일 카드 — 문장 하나 + 버튼 하나로 바로 실행 |
| 📋 캠페인 | 🔴 개선 필요 / 🟡 보통 / 🟢 효율 좋음 / ⏳ 학습 중 판정, 클릭하면 소재 썸네일 타일 |
| ✨ 새 광고 만들기 | 단계별 마법사: 무엇을 → 누구에게 → 소재(AI) → 확인·생성(일시중지) → 집행(심의번호) |
| ⚖️ 예산 재배분 | 총 예산 안에서 같은 목표끼리 효율 비교 후 ±20% 이내 일괄 조정 |

- 소재 문구는 Claude 구독(Claude Code)으로 생성 — API 비용 없음, 의료광고 규정 반영
- 돈이 걸린 모든 작업은 확인 팝업, 집행 시작은 심의필 번호 필수, 전체 일예산 상한 강제

- 모든 광고는 **PAUSED**로 생성 → 승인 후에만 집행
- `MAX_DAILY_BUDGET` 일예산 상한

설치: `python -m venv .venv && .venv\Scripts\pip install -r requirements.txt` → `.env.example`을 `.env`로 복사 → [SETUP.md](SETUP.md)
