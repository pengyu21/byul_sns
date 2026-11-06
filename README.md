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

- **성과**: 목표별(리드 CPL·트래픽 조회당 비용) 캠페인 표, 평균 대비 색상 경고, 일별 추이, 광고(소재)별 썸네일·진단
- **예산 최적화**: 같은 목표끼리 효율 비교 → 총 일예산 안에서 ±20% 이내 재배분, 학습 중 캠페인 보호, 승인 후 일괄 적용
- **신규 캠페인**: 기존 성과 좋은/나쁜 소재를 근거로 Claude 구독(Claude Code)으로 카피 생성 — API 비용 없음, 의료광고 규정 반영 → 이미지 자동 생성 → 일시중지 상태로 세팅
- **소재 교체**: 진단된 저성과 광고 끄기 + 검증된 광고세트에 새 소재 추가
- 집행 시작은 **심의필 번호 입력 + 동의** 후에만 가능, 전체 일예산 상한(`MAX_TOTAL_DAILY_BUDGET`) 강제

- 모든 광고는 **PAUSED**로 생성 → 승인 후에만 집행
- `MAX_DAILY_BUDGET` 일예산 상한

설치: `python -m venv .venv && .venv\Scripts\pip install -r requirements.txt` → `.env.example`을 `.env`로 복사 → [SETUP.md](SETUP.md)
