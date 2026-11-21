"""Run the synthetic Stage C routing dataset against a configured Chat model."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from hostpilot.budget import RequestBudget
from hostpilot.config import Settings
from hostpilot.contracts import BudgetContext, BudgetLimits, ContextSnapshot
from hostpilot.evaluation.fixtures import write_fixture_skills
from hostpilot.evaluation.model_config import model_configuration_from_environment
from hostpilot.models import create_model_runtime_components
from hostpilot.routing.skill_selector import ModelProviderSkillSelector
from hostpilot.skills.yaml_registry import YamlSkillRegistry

DEFAULT_DATASET_PATH = (
    Path(__file__).resolve().parents[1] / "docs/evaluations/stage-c-model-evaluation-v2.yaml"
)
CHAT_ROUTE_BY_SKILL = {
    "builtin/direct_answer": "direct",
    "builtin/clarify": "clarify",
    "builtin/orchestrator_plan": "plan_execute",
}


def _active_app_model() -> dict[str, Any]:
    """Read only non-secret model metadata from the running local API, if available."""
    request = urllib.request.Request(
        "http://127.0.0.1:8000/api/v1/model/configuration",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or not payload.get("configured"):
        return {}
    return payload


def _load_model_configuration() -> tuple[str, str | None, str, bool]:
    active = _active_app_model()
    return model_configuration_from_environment(active, interactive=sys.stdin.isatty())


def _chat_route(skill_id: str) -> str:
    if skill_id in CHAT_ROUTE_BY_SKILL:
        return CHAT_ROUTE_BY_SKILL[skill_id]
    if skill_id.startswith(("business/", "community/")):
        return "skill_execute"
    return "unknown"


async def _evaluate(model: Any, dataset: dict[str, Any]) -> dict[str, Any]:
    case_results: list[dict[str, Any]] = []
    latencies: list[float] = []
    token_total = 0
    with tempfile.TemporaryDirectory(prefix="hostpilot-eval-skills-") as temp_dir:
        root = Path(temp_dir)
        packaged_code_review_id = "business/code-review-check"
        allowed_ids = write_fixture_skills(
            root,
            dataset["fixture_skills"],
            skip_skill_ids=frozenset({packaged_code_review_id}),
        )
        package_root = Path(__file__).resolve().parents[1] / "src/hostpilot/skill_packages"
        registry = YamlSkillRegistry(root, additional_roots=(package_root,))
        catalog = registry.discover()
        if catalog.issues or {skill.skill_id for skill in catalog.skills} != {
            fixture["skill_id"] for fixture in dataset["fixture_skills"]
        }:
            raise RuntimeError("Synthetic Skill fixtures failed registry validation.")

        selector = ModelProviderSkillSelector(registry, model)
        for case in dataset["cases"]:
            budget = RequestBudget.start(
                BudgetContext(
                    limits=BudgetLimits(
                        total_tokens=5000,
                        tool_calls=0,
                        retrieval_calls=0,
                        timeout_seconds=90,
                    )
                )
            )
            started_at = time.monotonic()
            selection = await selector.select(
                case["prompt"],
                ContextSnapshot(request_id=str(uuid.uuid4())),
                budget=budget,
            )
            latency = time.monotonic() - started_at
            latencies.append(latency)
            actual_route = _chat_route(selection.skill_id)
            expected = case["expected"]
            expected_route = expected.get("route")
            expected_skill = expected.get("skill_id")
            route_match = expected_route is None or actual_route == expected_route
            skill_match = expected_skill is None or selection.skill_id == expected_skill
            allowed = selection.skill_id in allowed_ids
            if selection.token_usage is not None:
                token_total += selection.token_usage.total_tokens
            case_results.append(
                {
                    "case_id": case["case_id"],
                    "category": case["category"],
                    "expected_route": expected_route,
                    "actual_route": actual_route,
                    "expected_skill_id": expected_skill,
                    "actual_skill_id": selection.skill_id,
                    "route_match": route_match,
                    "skill_match": skill_match,
                    "allowed_skill_id": allowed,
                    "reason_code": selection.reason_code,
                    "tokens": (
                        selection.token_usage.total_tokens
                        if selection.token_usage is not None
                        else None
                    ),
                    "latency_seconds": round(latency, 3),
                }
            )

    counts = Counter(case["category"] for case in case_results)
    matched_routes = sum(case["route_match"] for case in case_results)
    business_cases = [case for case in case_results if case["category"] == "business_skill_routing"]
    business_matches = sum(case["skill_match"] for case in business_cases)
    sorted_latencies = sorted(latencies)
    p95_index = max(0, int(len(sorted_latencies) * 0.95 + 0.999) - 1)
    return {
        "dataset_id": dataset["dataset_id"],
        "dataset_version": dataset["version"],
        "case_count": len(case_results),
        "model_fault_cases_not_run": len(dataset.get("model_fault_cases", [])),
        "category_counts": dict(counts),
        "route_accuracy": {
            "matched": matched_routes,
            "total": len(case_results),
            "rate": round(matched_routes / len(case_results), 4) if case_results else None,
        },
        "business_skill_exact_match": {
            "matched": business_matches,
            "total": len(business_cases),
            "rate": round(business_matches / len(business_cases), 4) if business_cases else None,
        },
        "unknown_skill_guard": {
            "passed": sum(case["allowed_skill_id"] for case in case_results),
            "total": len(case_results),
        },
        "token_total_reported_by_provider": token_total,
        "latency_seconds": {
            "mean": round(sum(latencies) / len(latencies), 3) if latencies else None,
            "p95": round(sorted_latencies[p95_index], 3) if sorted_latencies else None,
        },
        "cases": case_results,
    }


async def _run(args: argparse.Namespace) -> int:
    dataset = yaml.safe_load(args.dataset.read_text(encoding="utf-8"))
    model_name, base_url, api_key, streaming = _load_model_configuration()
    settings = Settings(
        model_name=model_name,
        model_api_key=api_key,
        model_base_url=base_url,
        model_streaming=streaming,
    )
    model = create_model_runtime_components(settings).provider
    report = await _evaluate(model, dataset)
    report["model"] = model_name
    report["streaming"] = streaming
    report["tools_executed"] = 0
    report["mode"] = "selector_only; synthetic fixture Skills; no Runtime execution"
    report_json = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(report_json + "\n", encoding="utf-8")

    print(f"Dataset: {report['dataset_id']} v{report['dataset_version']}")
    print(f"Model: {model_name}; streaming={streaming}; tools executed=0")
    print(
        "Route accuracy: "
        f"{report['route_accuracy']['matched']}/{report['route_accuracy']['total']} "
        f"({report['route_accuracy']['rate']:.1%})"
    )
    business = report["business_skill_exact_match"]
    print(
        "Business Skill exact match: "
        f"{business['matched']}/{business['total']} ({business['rate']:.1%})"
    )
    print(f"Provider-reported tokens: {report['token_total_reported_by_provider']}")
    print("Cases: case_id | expected route/skill | actual route/skill | result")
    for case in report["cases"]:
        expected = f"{case['expected_route'] or '-'} / {case['expected_skill_id'] or '-'}"
        actual = f"{case['actual_route']} / {case['actual_skill_id']}"
        result = "PASS" if case["route_match"] and case["skill_match"] else "REVIEW"
        print(f"{case['case_id']} | {expected} | {actual} | {result}")
    if args.output:
        print(f"Report written to: {args.output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help="Evaluation dataset YAML (defaults to the latest v2 dataset).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for a JSON report. Do not store credentials in the report.",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
