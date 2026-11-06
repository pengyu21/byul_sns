# byul_sns

## Meta(페이스북·인스타그램) 광고 자동화 — Claude Code + MCP

브리프 한 줄로 **타겟 설계 → 카피·이미지 소재 생성 → 캠페인/광고세트/광고 일괄 세팅**까지 자동화.

| 구성 | 설명 |
|---|---|
| `meta_ads_mcp/server.py` | Meta Marketing API MCP 서버 (13개 도구) |
| `.claude/skills/meta-ads-launch` | `/meta-ads-launch` 원클릭 런칭 스킬 |
| `.claude/skills/meta-ads-optimize` | `/meta-ads-optimize` 성과 분석·최적화 스킬 |
| `.mcp.json` | Claude Code MCP 등록 |

- 모든 광고는 **PAUSED**로 생성 → 승인 후에만 집행
- `MAX_DAILY_BUDGET` 일예산 상한

설치: `python -m venv .venv && .venv\Scripts\pip install -r requirements.txt` → `.env.example`을 `.env`로 복사 → [SETUP.md](SETUP.md)
