"""광고 카피 + 이미지 문구 생성 (의료광고 규정 반영).

기본: 구독 로그인된 Claude Code(claude -p) — API 비용 없음.
COPY_BACKEND=api + ANTHROPIC_API_KEY 설정 시 Claude API 사용."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

MODEL = "claude-opus-5-5"

SYSTEM = """당신은 한국 성형외과의 Meta(페이스북·인스타그램) 퍼포먼스 광고 카피라이터입니다.
목표는 상담 신청 단가(CPL)를 낮추는 것이며, 아래 규정을 반드시 지킵니다. 규정 위반 소재는 심의에서 반려되고 계정이 제재될 수 있습니다.

[의료법 제56조 · 의료광고 심의 기준]
- 치료 경험담·환자 후기·비포/애프터(전후 비교) 표현 금지
- 다른 의료기관과의 비교·비방, "최고/유일/1위/최초/부작용 없음/통증 제로/100%" 등 단정·과장 표현 금지
- 효과 보장, 기간 단정("7일 만에 완성") 금지
- 수술·시술 소재의 primary_text 마지막 줄에 반드시 "※ 개인에 따라 부작용이 발생할 수 있습니다." 포함
- 할인·이벤트는 기간·조건을 명시할 수 있을 때만 언급하고, 무료 시술로 유인하는 표현 금지
[Meta 광고 정책]
- 외모 콤플렉스를 자극하거나 "당신의 ○○가 문제" 처럼 개인 특성을 지칭하는 표현 금지
- 부정적 자기인식을 유발하는 문구 금지

[작성 원칙]
- 변형마다 서로 다른 심리 앵글(예: 일상·회복 부담 해소 / 자연스러움·디자인 / 전문성·안전 시스템 / 상담 혜택)을 사용해 A/B 테스트가 의미 있게 할 것
- primary_text: 첫 줄이 스크롤을 멈추게 하는 후킹. 125자 안팎, 이모지 0~2개, 마지막 문장은 행동 유도
- headline 25자 이내, description 30자 이내
- image_headline 은 이미지 위에 크게 들어갈 15자 안팎 문구, image_subtext 는 25자 이내
- 성과 좋은 기존 소재의 공통점은 살리고, 성과 나쁜 소재의 패턴은 피할 것. 기존 문구를 그대로 복사하지 말 것
- 한국어로 작성"""


class AdVariant(BaseModel):
    angle: str = Field(description="심리 앵글 이름")
    primary_text: str
    headline: str
    description: str
    image_headline: str
    image_subtext: str
    badge: str = Field(description="이미지 좌상단 라벨, 없으면 빈 문자열")
    cta: Literal["SIGN_UP", "LEARN_MORE", "APPLY_NOW", "GET_QUOTE", "CONTACT_US", "BOOK_TRAVEL"]
    rationale: str = Field(description="이 변형이 효율이 좋을 것으로 보는 이유 (데이터 근거)")


class CopyPlan(BaseModel):
    insights: list[str] = Field(description="기존 성과 데이터에서 읽은 패턴 3~5개")
    variants: list[AdVariant]
    compliance_check: list[str] = Field(description="규정 점검 결과와 심의 시 주의점")


def _claude_cli() -> str | None:
    """구독 로그인된 Claude Code 실행 파일. npm 의 claude.cmd 대신 실제 exe 를 직접 호출(cmd 따옴표 문제 회피)."""
    if os.getenv("CLAUDE_CLI_PATH"):
        return os.getenv("CLAUDE_CLI_PATH")
    shim = shutil.which("claude")
    if not shim:
        return None
    exe = Path(shim).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    return str(exe) if exe.exists() else shim


def backend() -> str:
    """COPY_BACKEND=api 이고 키가 있으면 API, 아니면 구독(Claude Code)."""
    if os.getenv("COPY_BACKEND", "subscription") == "api" and os.getenv("ANTHROPIC_API_KEY"):
        return "api"
    return "subscription" if _claude_cli() else "none"


def available() -> bool:
    return backend() != "none"


def _user_prompt(brief: dict, winners: list[dict], losers: list[dict], n: int) -> str:
    return (
        f"## 광고 브리프\n{json.dumps(brief, ensure_ascii=False, indent=2)}\n\n"
        "(단가·지출 단위는 미국 달러 USD. 원화로 표기하지 말 것)\n\n"
        f"## 성과 좋은 기존 소재 (단가 낮은 순)\n{json.dumps(winners, ensure_ascii=False, indent=2)}\n\n"
        f"## 성과 나쁜 기존 소재\n{json.dumps(losers, ensure_ascii=False, indent=2)}\n\n"
        f"위 데이터를 분석해 서로 다른 앵글의 신규 소재 {n}개를 만들어 주세요."
    )


def _generate_subscription(user: str) -> CopyPlan:
    """Claude Code 헤드리스 모드(claude -p) — 구독 사용량으로 처리, API 과금 없음."""
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    cmd = [
        _claude_cli(), "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(CopyPlan.model_json_schema(), ensure_ascii=False),
        "--system-prompt", SYSTEM,
        "--tools", "",                 # 도구 없이 글만 쓰게
        "--strict-mcp-config",         # 프로젝트 MCP(광고 API) 로드 안 함
        "--setting-sources", "user",
        "--no-session-persistence",
        "--model", os.getenv("COPY_MODEL", "opus"),
    ]
    proc = subprocess.run(cmd, input=user, capture_output=True, text=True, encoding="utf-8",
                          env=env, cwd=tempfile.gettempdir(), timeout=600,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"Claude Code 실행 실패: {(proc.stderr or proc.stdout)[:400]}")
    if out.get("is_error"):
        raise RuntimeError(f"Claude Code 오류: {out.get('result') or out.get('subtype')} "
                           "(구독 한도 초과 시 잠시 후 다시 시도)")
    data = out.get("structured_output")
    if data is None:
        raise RuntimeError("구조화된 결과가 비어 있습니다. 다시 시도하세요.")
    return CopyPlan.model_validate(data)


def generate(brief: dict, winners: list[dict], losers: list[dict], n: int = 3) -> CopyPlan:
    """brief: 시술, 목표, 타겟, 랜딩, 혜택, 톤 등. winners/losers: 기존 광고 {문구, 단가, CTR}."""
    user = _user_prompt(brief, winners, losers, n)
    if backend() == "subscription":
        return _generate_subscription(user)
    client = anthropic.Anthropic()
    resp = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "high"},
        system=SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_format=CopyPlan,
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("모델이 요청을 거절했습니다. 브리프 표현을 바꿔 다시 시도하세요.")
    if resp.parsed_output is None:
        raise RuntimeError(f"응답을 해석하지 못했습니다 (stop_reason={resp.stop_reason}).")
    return resp.parsed_output
