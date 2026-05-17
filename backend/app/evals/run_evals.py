"""评测脚本。用法：python -m app.evals.run_evals"""

import asyncio
import time
from pathlib import Path

import yaml

from app.agent.judge_agent import JudgeAgent
from app.core.logging import get_logger, setup_logging

logger = get_logger(__name__)
GOLDEN_CASES_PATH = Path(__file__).parent / "golden_cases.yaml"


def load_golden_cases() -> list[dict]:
    with open(GOLDEN_CASES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


async def run_single_case(agent: JudgeAgent, case: dict) -> dict:
    case_id = case["id"]
    question = case["question"]
    logger.info("运行评测用例", case_id=case_id)
    start = time.monotonic()
    try:
        response = await agent.ask(question=question)
        elapsed = (time.monotonic() - start) * 1000
        confidence_match = response.confidence == case.get("expected_confidence", "high")
        response_cards = {c.name for c in response.cards}
        expected_cards = set(case.get("expected_cards", []))
        cards_covered = expected_cards.issubset(response_cards) if expected_cards else True
        response_rules = {r.section_id for r in response.rules}
        expected_rules = case.get("expected_rules", [])
        rules_covered = any(any(r_id.startswith(exp) for r_id in response_rules) for exp in expected_rules) if expected_rules else True
        human_judge_match = response.needs_human_judge == case.get("expected_needs_human_judge", False)
        score = sum([confidence_match, cards_covered, rules_covered, human_judge_match])
        return {"case_id": case_id, "question": question, "score": score, "max_score": 4, "latency_ms": round(elapsed, 1), "response_summary": response.summary}
    except Exception as e:
        logger.exception("评测用例执行失败", case_id=case_id)
        return {"case_id": case_id, "question": question, "score": 0, "max_score": 4, "error": str(e)}


async def run_evals() -> None:
    setup_logging()
    cases = load_golden_cases()
    agent = JudgeAgent()
    results: list[dict] = []
    total_score = 0
    total_max = 0
    for case in cases:
        result = await run_single_case(agent, case)
        results.append(result)
        total_score += result["score"]
        total_max += result["max_score"]
    accuracy = total_score / total_max * 100 if total_max > 0 else 0
    print(f"\n=== 评测报告 ===\n总计: {total_score}/{total_max} ({accuracy:.1f}%)")


def main() -> None:
    asyncio.run(run_evals())


if __name__ == "__main__":
    main()
