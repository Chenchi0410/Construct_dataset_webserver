"""Compatibility regression: gold Markdown must pass every generated ParseBench rule."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PARSEBENCH_ROOT = Path(r"C:\Users\sangzs1\ParseBench-main\ParseBench-main")
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PARSEBENCH_ROOT / "src"))

from app.generator import SourceDocument, generate_document  # noqa: E402
from parse_bench.evaluation.metrics.parse.test_rules import create_test_rule  # noqa: E402


def main() -> int:
    markdown_path = PROJECT_ROOT / "test.md"
    markdown = markdown_path.read_text(encoding="utf-8")
    document = generate_document(
        SourceDocument(
            stem="test",
            pdf_name="test.pdf",
            pdf_bytes=b"%PDF-1.4\ncompatibility-test",
            md_name="test.md",
            markdown=markdown,
        ),
        department="compatibility",
    )

    failures: list[str] = []
    scores: list[float] = []
    for raw_rule in document.sidecar["test_rules"]:
        try:
            result = create_test_rule(raw_rule).run(markdown)
            passed = bool(result[0])
            score = float(result[2]) if len(result) > 2 else (1.0 if passed else 0.0)
            scores.append(score)
            if not passed:
                failures.append(f"{raw_rule['type']} {raw_rule['id']}: {result[1]}")
        except Exception as exc:  # pragma: no cover - diagnostic path
            failures.append(f"{raw_rule.get('type')} {raw_rule.get('id')}: {exc!r}")

    average = sum(scores) / len(scores) if scores else 0.0
    print(
        f"ParseBench self-score: rules={len(document.sidecar['test_rules'])} "
        f"average={average:.6f} failures={len(failures)}"
    )
    for failure in failures:
        print(f"FAIL {failure}")
    if failures or average != 1.0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
