from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_PARSEBENCH_ROOT = Path(
    os.environ.get(
        "PARSEBENCH_ROOT",
        r"C:\Users\sangzs1\ParseBench-main\ParseBench-main",
    )
)


class ParseBenchCompatibilityError(RuntimeError):
    pass


def _parsebench_python(root: Path) -> Path:
    configured = os.environ.get("PARSEBENCH_PYTHON")
    if configured:
        return Path(configured)
    return root / ".venv" / "Scripts" / "python.exe"


def build_compatibility_profiles(markdowns: list[str]) -> list[dict[str, Any]]:
    """Extract rule bags with the exact ParseBench runtime used for evaluation."""
    root = DEFAULT_PARSEBENCH_ROOT.resolve()
    python = _parsebench_python(root)
    if not (root / "src" / "parse_bench").is_dir():
        raise ParseBenchCompatibilityError(
            f"ParseBench 源码目录不存在: {root}. 请设置 PARSEBENCH_ROOT。"
        )
    if not python.is_file():
        raise ParseBenchCompatibilityError(
            f"ParseBench Python 不存在: {python}. 请创建其 .venv 或设置 PARSEBENCH_PYTHON。"
        )

    request = json.dumps({"markdowns": markdowns}, ensure_ascii=False)
    try:
        completed = subprocess.run(
            [str(python), str(Path(__file__).resolve()), "--worker", str(root)],
            input=request,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=max(60, min(600, len(markdowns) * 15)),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ParseBenchCompatibilityError(f"无法运行 ParseBench 兼容提取器: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        raise ParseBenchCompatibilityError(f"ParseBench 兼容提取失败: {detail}")
    try:
        response = json.loads(completed.stdout)
        profiles = response["profiles"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ParseBenchCompatibilityError("ParseBench 兼容提取器返回了无效结果") from exc
    if not isinstance(profiles, list) or len(profiles) != len(markdowns):
        raise ParseBenchCompatibilityError("ParseBench 兼容提取结果数量与 Markdown 数量不一致")
    return profiles


def build_compatibility_profile(markdown: str) -> dict[str, Any]:
    return build_compatibility_profiles([markdown])[0]


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return {str(key): int(value) for key, value in counter.items()}


def _extract_profile(markdown: str) -> dict[str, Any]:
    # Imported only inside the ParseBench virtual environment.
    from parse_bench.evaluation.metrics.parse.rules_bag import (  # type: ignore[import-not-found]
        SentenceBagRule,
        WordBagRule,
        _extract_digit_counts,
    )

    sentence_observed = SentenceBagRule._extract_normalized_sentences_static(
        markdown, include_table_cells=False
    )
    sentence_full_text = SentenceBagRule._normalize_full_text(markdown)
    sentence_full_counts = {
        sentence: SentenceBagRule._count_sentence_in_full_text(sentence, sentence_full_text)
        for sentence in sentence_observed
    }
    # ParseBench's splitter can occasionally create fragments that its own full-text
    # matcher cannot find (notably across URL/email periods and paired underscores).
    # Such fragments remain valid for unexpected-content whitelisting, but they must
    # not become required/specific/order anchors because they are unmatchable by the
    # corresponding ParseBench rules even against the original gold markdown.
    matchable_sentences = [
        sentence for sentence in sentence_observed if sentence_full_counts[sentence] > 0
    ]
    sentence_missing = Counter(
        {sentence: sentence_full_counts[sentence] for sentence in matchable_sentences}
    )
    sentence_allowed = Counter(
        {
            sentence: max(count, sentence_full_counts[sentence])
            for sentence, count in sentence_observed.items()
        }
    )

    word_unexpected = WordBagRule._extract_normalized_words_static(
        markdown, include_table_cells=False
    )
    word_missing_observed = WordBagRule._extract_normalized_words_static(
        markdown, include_table_cells=True
    )
    word_full_text = WordBagRule._normalize_full_word_text(markdown)
    word_missing = Counter(
        {
            word: max(
                count,
                WordBagRule._count_word_in_full_text(word, word_full_text),
            )
            for word, count in word_missing_observed.items()
        }
    )
    word_allowed = Counter(
        {
            word: max(
                count,
                WordBagRule._count_word_in_full_text(word, word_full_text),
            )
            for word, count in word_unexpected.items()
        }
    )
    digits = _extract_digit_counts(markdown, include_table_cells=True)

    return {
        "sentences": matchable_sentences,
        "sentence_missing": _counter_dict(sentence_missing),
        "sentence_unexpected": _counter_dict(sentence_observed),
        "sentence_too_many": _counter_dict(sentence_allowed),
        "word_missing": _counter_dict(word_missing),
        "word_unexpected": _counter_dict(word_unexpected),
        "word_too_many": _counter_dict(word_allowed),
        "digits": _counter_dict(digits),
        "compatibility_target": "ParseBench rules_bag runtime",
    }


def _worker(root: Path) -> int:
    sys.path.insert(0, str(root / "src"))
    request = json.load(sys.stdin)
    markdowns = request.get("markdowns")
    if not isinstance(markdowns, list) or not all(isinstance(item, str) for item in markdowns):
        raise ValueError("markdowns must be a list of strings")
    json.dump(
        {"profiles": [_extract_profile(markdown) for markdown in markdowns]},
        sys.stdout,
        ensure_ascii=False,
    )
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit("Usage: parsebench_compat.py --worker <parsebench-root>")
    raise SystemExit(_worker(Path(sys.argv[2]).resolve()))
