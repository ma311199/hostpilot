"""Evaluate reference resolution with synthetic context and no Skill execution."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "docs/evaluations/contextual-reference-resolution-v1.yaml"
DEFAULT_API_BASE = "http://127.0.0.1:8000/api/v1"


def _trace_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events = payload.get("events", [])
    return [event for event in events if isinstance(event, dict)]


async def _load_trace(client: httpx.AsyncClient, request_id: str) -> list[dict[str, Any]]:
    for attempt in range(5):
        response = await client.get(f"/observability/traces/{request_id}")
        if response.status_code == 200:
            return _trace_events(response.json())
        if attempt < 4:
            await asyncio.sleep(0.1)
    return []


async def _run(args: argparse.Namespace) -> int:
    dataset = yaml.safe_load(args.dataset.read_text(encoding="utf-8"))
    if not isinstance(dataset, dict) or not isinstance(dataset.get("cases"), list):
        raise ValueError("The contextual reference dataset must contain a cases list.")
    timeout = httpx.Timeout(args.timeout_seconds)
    async with httpx.AsyncClient(base_url=args.api_base.rstrip("/"), timeout=timeout) as client:
        model = (await client.get("/model/configuration")).raise_for_status().json()
        if not model.get("configured"):
            raise RuntimeError("The active HostPilot runtime has no configured primary LLM.")

        cases: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        trace_contract_errors: list[str] = []
        total_tokens = 0
        for case in dataset["cases"]:
            conversation = (
                (
                    await client.post(
                        "/conversations", json={"title": f"reference-eval-{case['case_id']}"}
                    )
                )
                .raise_for_status()
                .json()
            )
            session_id = conversation["session_id"]
            started = time.perf_counter()
            try:
                response = await client.post(
                    f"/conversations/{session_id}/routing-evaluation",
                    json={
                        "message": case["prompt"],
                        "evaluation_context": case.get("evaluation_context", {}),
                    },
                )
                response.raise_for_status()
                result = response.json()
                elapsed = time.perf_counter() - started
                events = await _load_trace(client, result["request_id"])
                resolutions = [
                    event for event in events if event.get("name") == "skill.reference_resolution"
                ]
                reference = resolutions[-1].get("attributes", {}) if resolutions else {}
                expected_candidate_id = case.get("expected_candidate_id")
                if expected_candidate_id is not None:
                    if not all(key in reference for key in ("candidate_id", "confidence")):
                        matched = None
                        trace_contract_errors.append(case["case_id"])
                    else:
                        matched = (
                            reference.get("status") == "matched"
                            and reference.get("candidate_id") == expected_candidate_id
                            and isinstance(reference.get("confidence"), (int, float))
                            and reference["confidence"] >= 0.8
                        )
                else:
                    if resolutions and not all(
                        key in reference for key in ("candidate_id", "confidence")
                    ):
                        trace_contract_errors.append(case["case_id"])
                    matched = result.get("skill_id") == "builtin/clarify" and result.get(
                        "reason_code"
                    ) in {"query_reference_not_resolved", "query_context_ambiguous"}
                usage = result.get("token_usage") or {}
                tokens = usage.get("total_tokens", 0)
                total_tokens += tokens if isinstance(tokens, int) else 0
                cases.append(
                    {
                        "case_id": case["case_id"],
                        "expected_resolution": case["expected_resolution"],
                        "expected_candidate_id": expected_candidate_id,
                        "actual_status": reference.get("status", "not_called"),
                        "actual_candidate_id": reference.get("candidate_id") or None,
                        "confidence": reference.get("confidence"),
                        "candidate_count": reference.get("candidate_count"),
                        "actual_skill_id": result.get("skill_id"),
                        "reason_code": result.get("reason_code"),
                        "passed": matched,
                        "token_total": tokens,
                        "latency_seconds": round(elapsed, 3),
                        "trace_available": bool(events),
                    }
                )
            except httpx.HTTPStatusError as error:
                failures.append(
                    {
                        "case_id": case["case_id"],
                        "http_status": str(error.response.status_code),
                        "reason": "request_rejected",
                    }
                )
                break
            finally:
                await client.delete(f"/conversations/{session_id}")

    matched = sum(case["passed"] is True for case in cases)
    measured = sum(case["passed"] is not None for case in cases)
    positive = [case for case in cases if case["expected_candidate_id"] is not None]
    negative = [case for case in cases if case["expected_candidate_id"] is None]
    confidence_values = [
        case["confidence"] for case in positive if isinstance(case["confidence"], (int, float))
    ]
    report = {
        "dataset_id": dataset["dataset_id"],
        "dataset_version": dataset["version"],
        "mode": "active_model_reference_resolution_only",
        "run_status": "complete"
        if not failures and not trace_contract_errors and len(cases) == len(dataset["cases"])
        else "incomplete",
        "side_effect_execution_enabled": False,
        "model": model.get("model_name"),
        "case_count": len(cases),
        "failed_cases": failures,
        "trace_contract_errors": trace_contract_errors,
        "reference_accuracy": {
            "matched": matched,
            "total": measured,
            "completed_cases": len(cases),
            "rate": round(matched / measured, 4) if measured else None,
        },
        "positive_match_accuracy": {
            "matched": sum(case["passed"] is True for case in positive),
            "total": sum(case["passed"] is not None for case in positive),
        },
        "negative_safe_clarifications": {
            "matched": sum(case["passed"] is True for case in negative),
            "total": len(negative),
        },
        "confidence": {
            "threshold": 0.8,
            "matched_case_values": confidence_values,
        },
        "token_total": total_tokens,
        "resolution_status_counts": dict(Counter(case["actual_status"] for case in cases)),
        "cases": cases,
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(f"Dataset: {report['dataset_id']} v{report['dataset_version']}")
    print(f"Model: {report['model']}; Skill/tool execution: disabled")
    print(
        f"Reference accuracy: {matched}/{measured} "
        f"({report['reference_accuracy']['rate']:.1%}; {len(cases)} calls completed)"
        if measured
        else "Reference accuracy: no completed cases"
    )
    for case in cases:
        status = "PASS" if case["passed"] else "REVIEW"
        print(
            f"{case['case_id']} | expected={case['expected_candidate_id'] or 'clarify'} "
            f"actual={case['actual_candidate_id'] or case['actual_status']} | {status}"
        )
    if args.output:
        print(f"Report written to: {args.output}")
    return 0 if report["run_status"] == "complete" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--timeout-seconds", type=float, default=90)
    parser.add_argument("--output", type=Path)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
