"""Evaluate active local Skill routing without reading credentials or executing tools."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import yaml

from hostpilot.evaluation.routing_metrics import (
    enabled_skill_coverage,
    percentile,
    reciprocal_rank,
    retrieval_metric_summary,
    split_skill_ids,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "docs/evaluations/stage-c-model-evaluation-v3.yaml"
IMPORTED_SKILLS_DATASET = ROOT / "docs/evaluations/imported-skills-smoke-v1.yaml"
DEFAULT_API_BASE = "http://127.0.0.1:8000/api/v1"
SAFE_CATEGORIES = {"direct_answer", "clarify", "business_skill_routing"}


def _trace_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events = payload.get("events", [])
    return [event for event in events if isinstance(event, dict)]


async def _run(args: argparse.Namespace) -> int:
    dataset_paths = args.dataset or [DEFAULT_DATASET, IMPORTED_SKILLS_DATASET]
    datasets = [yaml.safe_load(path.read_text(encoding="utf-8")) for path in dataset_paths]
    cases = [case for dataset in datasets for case in dataset["cases"]]
    case_ids = [case["case_id"] for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Selected evaluation datasets contain duplicate case IDs.")
    timeout = httpx.Timeout(args.timeout_seconds)
    async with httpx.AsyncClient(base_url=args.api_base.rstrip("/"), timeout=timeout) as client:
        model = (await client.get("/model/configuration")).raise_for_status().json()
        routing_models = (
            (await client.get("/routing-model/configuration")).raise_for_status().json()
        )
        skill_catalog = (await client.get("/skills")).raise_for_status().json()
        if not model.get("configured"):
            raise RuntimeError("The active HostPilot runtime has no configured primary LLM.")
        available_skill_ids = {
            item["skill_id"] for item in skill_catalog.get("skills", []) if item.get("enabled")
        }
        execution_modes = {
            item["skill_id"]: item.get("execution_mode")
            for item in skill_catalog.get("skills", [])
            if item.get("enabled")
        }

        requested_ids = set(args.case_id or ())
        selected: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for case in cases:
            if requested_ids and case["case_id"] not in requested_ids:
                continue
            if not requested_ids and case["category"] not in SAFE_CATEGORIES:
                continue
            expected = case["expected"]
            expected_skill_id = expected.get("skill_id")
            if expected_skill_id and expected_skill_id not in available_skill_ids:
                skipped.append({"case_id": case["case_id"], "reason": "expected_skill_not_enabled"})
                continue
            if case["category"] not in SAFE_CATEGORIES:
                skipped.append({"case_id": case["case_id"], "reason": "outside_safe_categories"})
                continue
            selected.append(case)
        if requested_ids:
            matched_ids = {case["case_id"] for case in selected} | {
                case["case_id"] for case in skipped
            }
            unknown_ids = requested_ids - matched_ids
            if unknown_ids:
                raise ValueError(f"Unknown evaluation case IDs: {', '.join(sorted(unknown_ids))}")

        results: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        latencies: list[float] = []
        total_tokens = 0
        all_events: list[dict[str, Any]] = []
        for case in selected:
            conversation = (
                (
                    await client.post(
                        "/conversations", json={"title": f"route-eval-{case['case_id']}"}
                    )
                )
                .raise_for_status()
                .json()
            )
            conversation_id = conversation["conversation_id"]
            started = time.perf_counter()
            try:
                response = (
                    (
                        await client.post(
                            f"/conversations/{conversation_id}/routing-evaluation",
                            json={"message": case["prompt"]},
                        )
                    )
                    .raise_for_status()
                    .json()
                )
                elapsed = time.perf_counter() - started
                latencies.append(elapsed)
                request_id = response["request_id"]
                trace_payload: dict[str, Any] = {}
                for attempt in range(5):
                    trace_response = await client.get(f"/observability/traces/{request_id}")
                    if trace_response.status_code == 200:
                        trace_payload = trace_response.json()
                        break
                    if attempt < 4:
                        await asyncio.sleep(0.1)
                events = _trace_events(trace_payload)
                all_events.extend(events)
                retrieval_events = [
                    event for event in events if event.get("name") == "skill.retrieval"
                ]
                gate_events = [
                    event for event in events if event.get("name") == "skill.business_gate"
                ]
                classification_events = [
                    event for event in events if event.get("name") == "skill.classification"
                ]
                skill_id = response.get("skill_id")
                retrieval = retrieval_events[-1].get("attributes", {}) if retrieval_events else {}
                classification = (
                    classification_events[-1].get("attributes", {}) if classification_events else {}
                )
                rrf_ids = split_skill_ids(retrieval.get("rrf_skill_ids"))
                reranker_raw_ids = split_skill_ids(retrieval.get("reranker_raw_skill_ids"))
                reranked_ids = split_skill_ids(retrieval.get("reranked_skill_ids"))
                expected = case["expected"]
                expected_route = expected.get("route")
                expected_selection_route = (
                    "skill_execute"
                    if expected.get("clarification_stage") == "skill_execution"
                    else expected_route
                )
                expected_skill_id = expected.get("skill_id")
                token_usage = response.get("token_usage") or {}
                case_tokens = token_usage.get("total_tokens", 0)
                total_tokens += case_tokens if isinstance(case_tokens, int) else 0
                actual_route = response.get("route")
                results.append(
                    {
                        "case_id": case["case_id"],
                        "category": case["category"],
                        "expected_route": expected_selection_route,
                        "expected_final_route": (
                            expected_route if expected_route != expected_selection_route else None
                        ),
                        "actual_route": actual_route,
                        "route_match": actual_route == expected_selection_route,
                        "expected_skill_id": expected_skill_id,
                        "actual_skill_id": skill_id,
                        "skill_match": skill_id == expected_skill_id if expected_skill_id else None,
                        "rrf_rank": reciprocal_rank(rrf_ids, expected_skill_id),
                        "reranker_raw_rank": reciprocal_rank(reranker_raw_ids, expected_skill_id),
                        "reranked_rank": reciprocal_rank(reranked_ids, expected_skill_id),
                        "retrieval_version": retrieval.get("retrieval_version"),
                        "embedding_status": retrieval.get("embedding_status"),
                        "embedding_reason": retrieval.get("embedding_reason"),
                        "embedding_duration_ms": retrieval.get("embedding_duration_ms"),
                        "reranker_status": retrieval.get("reranker_status"),
                        "reranker_reason": retrieval.get("reranker_reason"),
                        "reranker_duration_ms": retrieval.get("reranker_duration_ms"),
                        "reranker_fusion_applied": retrieval.get("reranker_fusion_applied"),
                        "classifier_decision": classification.get("classifier_decision"),
                        "classifier_selected_skill_id": classification.get("selected_skill_id"),
                        "classifier_decision_confidence": classification.get("decision_confidence"),
                        "top_candidate_id": classification.get("top_candidate_id"),
                        "top_candidate_confidence": classification.get("top_confidence"),
                        "candidate_confidence_margin": classification.get("confidence_margin"),
                        "business_gate": (
                            gate_events[-1].get("attributes", {}) if gate_events else None
                        ),
                        "token_total": case_tokens,
                        "latency_seconds": round(elapsed, 3),
                        "trace_available": bool(events),
                    }
                )
            except httpx.HTTPStatusError as error:
                failures.append(
                    {
                        "case_id": case["case_id"],
                        "http_status": error.response.status_code,
                        "reason": "request_rejected",
                    }
                )
                break
            finally:
                await client.delete(f"/conversations/{conversation_id}")

    routed = sum(case["route_match"] for case in results)
    business = [case for case in results if case.get("expected_skill_id")]
    skill_matched = sum(case["skill_match"] is True for case in business)
    gate_eligible = [
        case
        for case in business
        if execution_modes.get(case["expected_skill_id"]) in {"yaml", "both"}
    ]
    gates = [case["business_gate"] for case in business if case.get("business_gate")]
    passed_gates = [gate for gate in gates if gate.get("gate_passed") is True]
    correct_passes = sum(
        case.get("skill_match") is True
        for case in business
        if (case.get("business_gate") or {}).get("gate_passed") is True
    )
    role_stats = Counter(
        f"{event.get('attributes', {}).get('model_role')}:"
        f"{event.get('attributes', {}).get('status')}"
        for event in all_events
        if event.get("name") == "skill.routing_model"
    )
    classification_stats = Counter(
        str(event.get("attributes", {}).get("classifier_decision"))
        for event in all_events
        if event.get("name") == "skill.classification"
    )
    retrieval_versions = Counter(
        str(event.get("attributes", {}).get("retrieval_version"))
        for event in all_events
        if event.get("name") == "skill.retrieval"
    )
    covered_skill_ids, uncovered_skill_ids = enabled_skill_coverage(available_skill_ids, selected)
    report = {
        "datasets": [
            {"dataset_id": dataset["dataset_id"], "version": dataset["version"]}
            for dataset in datasets
        ],
        "mode": "active_local_selector_route_only",
        "run_status": "complete" if not failures else "incomplete",
        "side_effect_execution_enabled": False,
        "skill_coverage_complete": not uncovered_skill_ids,
        "primary_model": model.get("model_name"),
        "lightweight_model": routing_models.get("judge_model"),
        "embedding_model": routing_models.get("embedding_model"),
        "reranker_model": routing_models.get("reranker_model"),
        "primary_api_key_configured": model.get("configured", False),
        "lightweight_api_key_configured": routing_models.get("judge_api_key_configured", False),
        "embedding_api_key_configured": routing_models.get("embedding_api_key_configured", False),
        "reranker_api_key_configured": routing_models.get("reranker_api_key_configured", False),
        "case_count": len(results),
        "failed_cases": failures,
        "skipped_cases": skipped,
        "enabled_skill_coverage": {
            "enabled_skill_ids": sorted(available_skill_ids),
            "covered_skill_ids": sorted(covered_skill_ids),
            "uncovered_skill_ids": sorted(uncovered_skill_ids),
        },
        "route_accuracy": {
            "matched": routed,
            "total": len(results),
            "rate": round(routed / len(results), 4) if results else None,
        },
        "business_skill_exact_match": {
            "matched": skill_matched,
            "total": len(business),
            "rate": round(skill_matched / len(business), 4) if business else None,
        },
        "rrf_retrieval": retrieval_metric_summary(results, ranking_key="rrf_rank"),
        "reranker_raw_retrieval": retrieval_metric_summary(
            results, ranking_key="reranker_raw_rank"
        ),
        "reranked_retrieval": retrieval_metric_summary(results, ranking_key="reranked_rank"),
        "business_gate": {
            "applicable": len(gate_eligible),
            "attempted": len(gates),
            "passed": len(passed_gates),
            "coverage": (
                round(len(passed_gates) / len(gate_eligible), 4) if gate_eligible else None
            ),
            "correct_passes": correct_passes,
            "precision": round(correct_passes / len(passed_gates), 4) if passed_gates else None,
        },
        "routing_model_attempts": dict(role_stats),
        "classification_decisions": dict(classification_stats),
        "retrieval_versions": dict(retrieval_versions),
        "token_total_reported": total_tokens,
        "latency_seconds": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
        },
        "unexpected_tool_events": sum(
            1 for event in all_events if str(event.get("name", "")).startswith("tool.")
        ),
        "cases": results,
    }
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    if failures:
        print(
            "The evaluation stopped after an HTTP error; partial metrics are not a baseline.",
            file=sys.stderr,
        )
        return 3
    if not results:
        return 1
    supported_versions = {
        "fielded_bm25_rrf_v1",
        "fielded_bm25_rrf_reranker_fusion_v2",
        "fielded_bm25_rrf_reranker_guarded_fusion_v3",
    }
    if not any(retrieval_versions.get(version) for version in supported_versions):
        print(
            "The active API did not emit a supported hybrid retrieval trace version. "
            "Restart HostPilot with the current code, then rerun this evaluation.",
            file=sys.stderr,
        )
        return 2
    if uncovered_skill_ids:
        print(
            "The dataset does not cover every enabled Skill; "
            "results are not a complete routing baseline.",
            file=sys.stderr,
        )
        return 4
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument(
        "--dataset",
        type=Path,
        action="append",
        help=(
            "Dataset file; repeat to combine datasets. Defaults to Stage C v3 plus imported Skills."
        ),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=180)
    parser.add_argument(
        "--case-id",
        action="append",
        help="Run only these case IDs; repeat the option to select multiple cases.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    try:
        raise SystemExit(asyncio.run(_run(args)))
    except (httpx.HTTPError, OSError, ValueError, RuntimeError, KeyError) as error:
        raise SystemExit(
            f"Live routing evaluation failed: {type(error).__name__}: {error}"
        ) from error


if __name__ == "__main__":
    main()
