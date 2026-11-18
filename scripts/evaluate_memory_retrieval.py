"""Compare fixed-order, lexical-only, and hybrid bounded memory recall."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hostpilot.evaluation.memory_retrieval import (
    evaluate_memory_retrieval,
    load_memory_evaluation_dataset,
)

DEFAULT_DATASET = (
    Path(__file__).resolve().parents[1] / "docs/evaluations/memory-retrieval-v1.yaml"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--char-budget", type=int, default=220)
    arguments = parser.parse_args()
    try:
        entries, cases = load_memory_evaluation_dataset(arguments.dataset)
        report = evaluate_memory_retrieval(entries, cases, char_budget=arguments.char_budget)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
