from __future__ import annotations

import hashlib
import html
import io
import json
import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


CONTENT_RULE_TYPES = {
    "missing_sentence_percent",
    "unexpected_sentence_percent",
    "too_many_sentence_occurence_percent",
    "missing_word_percent",
    "unexpected_word_percent",
    "too_many_word_occurence_percent",
    "bag_of_digit_percent",
    "missing_specific_sentence",
    "missing_specific_word",
    "order",
    "is_header",
    "is_footer",
}

FORMATTING_RULE_TYPES = {
    "is_bold",
    "is_italic",
    "is_underline",
    "is_strikeout",
    "is_mark",
    "is_sup",
    "is_sub",
    "is_title",
    "title_hierarchy_percent",
    "is_latex",
    "is_code_block",
}

SUPPORTED_RULE_TYPES = CONTENT_RULE_TYPES | FORMATTING_RULE_TYPES

_FENCE_RE = re.compile(r"^\s*```\s*([^\s`]*)\s*$")
_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*:?-{3,}:?\s*$")
_WORD_RE = re.compile(
    r"[A-Za-z0-9]+(?:[._/@:+-][A-Za-z0-9]+)*|[\u3400-\u4dbf\u4e00-\u9fff]+",
    re.UNICODE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])\s*|(?<=[.])\s+(?=[A-Z0-9\u3400-\u9fff])")


class DatasetValidationError(ValueError):
    pass


@dataclass(slots=True)
class SourceDocument:
    stem: str
    pdf_name: str
    pdf_bytes: bytes
    md_name: str
    markdown: str


@dataclass(slots=True)
class GeneratedDocument:
    source: SourceDocument
    sidecar: dict[str, Any]
    table_markdown: str | None
    stats: dict[str, int]
    warnings: list[str]


def slugify(value: str, fallback: str = "default") -> str:
    value = unicodedata.normalize("NFKC", value or "").strip().lower()
    value = re.sub(r"[^a-z0-9\u3400-\u9fff_-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_-")
    return value or fallback


def safe_filename(name: str) -> str:
    clean = Path(name or "").name
    if not clean or clean in {".", ".."} or "\x00" in clean:
        raise DatasetValidationError("文件名无效")
    return clean


def decode_markdown(data: bytes, name: str) -> str:
    if len(data) > 25 * 1024 * 1024:
        raise DatasetValidationError(f"{name}: Markdown 超过 25 MB")
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            text = data.decode(encoding)
            if not text.strip():
                raise DatasetValidationError(f"{name}: Markdown 内容为空")
            return text.replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    raise DatasetValidationError(f"{name}: 必须使用 UTF-8 编码")


def pair_uploads(
    pdf_files: Iterable[tuple[str, bytes]], md_files: Iterable[tuple[str, bytes]]
) -> list[SourceDocument]:
    pdf_map: dict[str, tuple[str, bytes]] = {}
    md_map: dict[str, tuple[str, bytes]] = {}

    for raw_name, data in pdf_files:
        name = safe_filename(raw_name)
        if Path(name).suffix.lower() != ".pdf":
            raise DatasetValidationError(f"{name}: PDF 上传区只允许 .pdf")
        if not data:
            raise DatasetValidationError(f"{name}: 文件为空")
        if len(data) > 100 * 1024 * 1024:
            raise DatasetValidationError(f"{name}: PDF 超过 100 MB")
        if b"%PDF-" not in data[:1024]:
            raise DatasetValidationError(f"{name}: 文件内容不是有效的 PDF")
        key = Path(name).stem.casefold()
        if key in pdf_map:
            raise DatasetValidationError(f"PDF 名称重复: {Path(name).stem}")
        pdf_map[key] = (name, data)

    for raw_name, data in md_files:
        name = safe_filename(raw_name)
        if Path(name).suffix.lower() not in {".md", ".markdown"}:
            raise DatasetValidationError(f"{name}: Markdown 上传区只允许 .md/.markdown")
        key = Path(name).stem.casefold()
        if key in md_map:
            raise DatasetValidationError(f"Markdown 名称重复: {Path(name).stem}")
        md_map[key] = (name, data)

    if not pdf_map or not md_map:
        raise DatasetValidationError("至少需要上传一个 PDF 和一个同名 Markdown")
    if len(pdf_map) + len(md_map) > 400:
        raise DatasetValidationError("单次最多上传 200 对文件")

    missing_md = sorted(set(pdf_map) - set(md_map))
    missing_pdf = sorted(set(md_map) - set(pdf_map))
    errors = []
    if missing_md:
        errors.append("缺少同名 Markdown: " + ", ".join(missing_md))
    if missing_pdf:
        errors.append("缺少同名 PDF: " + ", ".join(missing_pdf))
    if errors:
        raise DatasetValidationError("；".join(errors))

    documents = []
    for key in sorted(pdf_map):
        pdf_name, pdf_data = pdf_map[key]
        md_name, md_data = md_map[key]
        documents.append(
            SourceDocument(
                stem=Path(pdf_name).stem,
                pdf_name=pdf_name,
                pdf_bytes=pdf_data,
                md_name=md_name,
                markdown=decode_markdown(md_data, md_name),
            )
        )
    return documents


def _strip_inline_markdown(text: str) -> str:
    text = re.sub(r"!\[([^]]*)]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^]]+)]\(([^)]*)\)", r"\1 \2", text)
    text = re.sub(r"<((?:https?://|mailto:)[^>]+|[^>@\s]+@[^>\s]+)>", r"\1", text)
    text = re.sub(r"</?(?:b|strong|i|em|u|s|del|mark|sup|sub)\b[^>]*>", "", text, flags=re.I)
    text = re.sub(r"[*_~`]", "", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def _is_table_block(lines: list[str], start: int) -> bool:
    if start + 1 >= len(lines) or "|" not in lines[start] or "|" not in lines[start + 1]:
        return False
    cells = _split_table_row(lines[start + 1])
    return bool(cells) and all(_TABLE_SEPARATOR_RE.match(cell) for cell in cells)


def _split_table_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells = re.split(r"(?<!\\)\|", line)
    return [cell.replace("\\|", "|").strip() for cell in cells]


def _inline_html(value: str) -> str:
    escaped = html.escape(value.strip())
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"__(.+?)__", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"~~(.+?)~~", r"<del>\1</del>", escaped)
    return escaped


def extract_tables(markdown: str) -> tuple[list[str], str]:
    lines = markdown.splitlines()
    tables: list[str] = []
    output: list[str] = []
    i = 0
    while i < len(lines):
        if not _is_table_block(lines, i):
            output.append(lines[i])
            i += 1
            continue
        header = _split_table_row(lines[i])
        separator = _split_table_row(lines[i + 1])
        rows: list[list[str]] = []
        i += 2
        while i < len(lines) and "|" in lines[i] and lines[i].strip():
            row = _split_table_row(lines[i])
            if len(row) != len(header):
                break
            rows.append(row)
            i += 1
        alignments = []
        for cell in separator:
            stripped = cell.strip()
            if stripped.startswith(":") and stripped.endswith(":"):
                alignments.append("center")
            elif stripped.endswith(":"):
                alignments.append("right")
            elif stripped.startswith(":"):
                alignments.append("left")
            else:
                alignments.append(None)
        parts = ["<table>", "  <thead>", "    <tr>"]
        for index, cell in enumerate(header):
            attr = f' align="{alignments[index]}"' if alignments[index] else ""
            parts.append(f"      <th{attr}>{_inline_html(cell)}</th>")
        parts.extend(["    </tr>", "  </thead>", "  <tbody>"])
        for row in rows:
            parts.append("    <tr>")
            for index, cell in enumerate(row):
                attr = f' align="{alignments[index]}"' if alignments[index] else ""
                parts.append(f"      <td{attr}>{_inline_html(cell)}</td>")
            parts.append("    </tr>")
        parts.extend(["  </tbody>", "</table>"])
        table_html = "\n".join(parts)
        tables.append(table_html)
        output.append(table_html)
    return tables, "\n".join(output)


def _plain_blocks(markdown: str) -> list[str]:
    lines = markdown.splitlines()
    blocks: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence:
            cleaned = line.strip()
            if cleaned:
                blocks.append(cleaned)
            i += 1
            continue
        if _is_table_block(lines, i):
            header = _split_table_row(lines[i])
            i += 2
            rows = [header]
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_table_row(lines[i]))
                i += 1
            blocks.extend(" ".join(_strip_inline_markdown(c) for c in row if c.strip()) for row in rows)
            continue
        cleaned = re.sub(r"^\s*(?:#{1,6}\s+|>+\s*|[-+*]\s+|\d+[.)]\s+)", "", line)
        cleaned = re.sub(r"^\s*[-*_]{3,}\s*$", "", cleaned)
        cleaned = _strip_inline_markdown(cleaned)
        if cleaned:
            blocks.append(cleaned)
        i += 1
    return blocks


def extract_sentences(markdown: str) -> list[str]:
    sentences: list[str] = []
    for block in _plain_blocks(markdown):
        for value in _SENTENCE_SPLIT_RE.split(block):
            value = value.strip().rstrip("。.!?！？；;").strip()
            if value and len(value) >= 2:
                sentences.append(value)
    return sentences


def extract_words(markdown: str) -> list[str]:
    plain = "\n".join(_plain_blocks(markdown)).lower()
    return [match.group(0) for match in _WORD_RE.finditer(plain)]


def _extract_formatting(markdown: str) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    found: list[tuple[str, dict[str, Any]]] = []
    hierarchy: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(0, hierarchy)]

    for line in markdown.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            text = _strip_inline_markdown(heading.group(2))
            if text:
                found.append(("is_title", {"text": text, "level": level}))
                while stack and stack[-1][0] >= level:
                    stack.pop()
                parent = stack[-1][1] if stack else hierarchy
                node: dict[str, Any] = {}
                parent[text] = node
                stack.append((level, node))

    patterns = [
        ("is_bold", re.compile(r"(?<!\*)\*\*(?!\*)(.+?)(?<!\*)\*\*(?!\*)|__(.+?)__")),
        ("is_italic", re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")),
        ("is_strikeout", re.compile(r"~~(.+?)~~")),
        ("is_underline", re.compile(r"<u>(.+?)</u>", re.I | re.S)),
        ("is_mark", re.compile(r"<mark>(.+?)</mark>", re.I | re.S)),
        ("is_sup", re.compile(r"<sup>(.+?)</sup>", re.I | re.S)),
        ("is_sub", re.compile(r"<sub>(.+?)</sub>", re.I | re.S)),
    ]
    for rule_type, pattern in patterns:
        for match in pattern.finditer(markdown):
            value = next((g for g in match.groups() if g is not None), match.group(0))
            value = _strip_inline_markdown(value)
            if value:
                found.append((rule_type, {"text": value}))

    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        fence = _FENCE_RE.match(lines[i])
        if not fence:
            i += 1
            continue
        language = fence.group(1) or ""
        code: list[str] = []
        i += 1
        while i < len(lines) and not _FENCE_RE.match(lines[i]):
            code.append(lines[i])
            i += 1
        if code:
            found.append(("is_code_block", {"language": language, "code": "\n".join(code)}))
        i += 1

    for match in re.finditer(r"(?<!\\)\$(?!\s)([^$\n]+?)(?<!\s)\$", markdown):
        formula = match.group(1).strip()
        if formula and not formula.replace(".", "", 1).isdigit():
            found.append(("is_latex", {"formula": formula}))

    return found, hierarchy


def _rule_id(stem: str, rule_type: str, payload: dict[str, Any], index: int) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:10]
    return f"{slugify(stem)}_{rule_type}_{index}_{digest}"


def generate_document(
    source: SourceDocument,
    *,
    department: str,
    difficulty: str,
    document_type: str,
) -> GeneratedDocument:
    sentences = extract_sentences(source.markdown)
    words = extract_words(source.markdown)
    sentence_bag = dict(Counter(sentences))
    word_bag = dict(Counter(words))
    digit_bag = dict(Counter(ch for ch in "\n".join(_plain_blocks(source.markdown)) if ch.isdigit()))
    tables, html_gold = extract_tables(source.markdown)
    formatting, hierarchy = _extract_formatting(source.markdown)

    tags = [f"department_{slugify(department)}", slugify(document_type, "misc"), difficulty]
    raw_rules: list[tuple[str, dict[str, Any]]] = []
    if sentence_bag:
        raw_rules.extend(
            (rule_type, {"bag_of_sentence": sentence_bag})
            for rule_type in (
                "missing_sentence_percent",
                "unexpected_sentence_percent",
                "too_many_sentence_occurence_percent",
            )
        )
    if word_bag:
        raw_rules.extend(
            (rule_type, {"bag_of_word": word_bag})
            for rule_type in (
                "missing_word_percent",
                "unexpected_word_percent",
                "too_many_word_occurence_percent",
            )
        )
    if digit_bag:
        raw_rules.append(("bag_of_digit_percent", {"bag_of_digit": digit_bag}))
    raw_rules.extend(("missing_specific_sentence", {"sentence": sentence}) for sentence in dict.fromkeys(sentences))
    raw_rules.extend(("missing_specific_word", {"word": word}) for word in dict.fromkeys(words))
    raw_rules.extend(
        ("order", {"before": before, "after": after, "max_diffs": 0})
        for before, after in zip(sentences, sentences[1:])
        if before != after
    )
    raw_rules.extend(formatting)
    if hierarchy:
        raw_rules.append(("title_hierarchy_percent", {"title_hierarchy": hierarchy}))

    rules: list[dict[str, Any]] = []
    per_type: Counter[str] = Counter()
    for rule_type, payload in raw_rules:
        index = per_type[rule_type]
        per_type[rule_type] += 1
        rules.append(
            {
                "type": rule_type,
                "id": _rule_id(source.stem, rule_type, payload, index),
                **payload,
                "verified": True,
                "tags": tags,
            }
        )

    sidecar: dict[str, Any] = {
        "annotation_mode": "parse",
        "test_rules": rules,
        "tags": tags,
        "metadata": {
            "source": "construct-dataset-webserver",
            "department": department,
            "document_type": document_type,
            "difficulty": difficulty,
            "gold_markdown_sha256": hashlib.sha256(source.markdown.encode("utf-8")).hexdigest(),
            "pdf_sha256": hashlib.sha256(source.pdf_bytes).hexdigest(),
        },
    }
    if tables:
        sidecar["expected_markdown"] = html_gold

    warnings = []
    if not sentences:
        warnings.append("未提取到可评测句子")
    if not formatting:
        warnings.append("未检测到受支持的 Markdown 格式")
    if not tables:
        warnings.append("未检测到 Markdown 管道表格")
    stats = {
        "rules": len(rules),
        "content_rules": sum(r["type"] in CONTENT_RULE_TYPES for r in rules),
        "formatting_rules": sum(r["type"] in FORMATTING_RULE_TYPES for r in rules),
        "tables": len(tables),
        "sentences": len(sentences),
        "words": len(words),
    }
    return GeneratedDocument(
        source=source,
        sidecar=sidecar,
        table_markdown="\n\n".join(tables) if tables else None,
        stats=stats,
        warnings=warnings,
    )


def validate_sidecar(sidecar: dict[str, Any], stem: str) -> None:
    if not isinstance(sidecar, dict):
        raise DatasetValidationError(f"{stem}: Sidecar 必须是 JSON 对象")
    rules = sidecar.get("test_rules")
    if not isinstance(rules, list):
        raise DatasetValidationError(f"{stem}: test_rules 必须是数组")
    seen: set[str] = set()
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise DatasetValidationError(f"{stem}: 规则 {index + 1} 不是对象")
        rule_type = rule.get("type")
        if rule_type not in SUPPORTED_RULE_TYPES:
            raise DatasetValidationError(f"{stem}: 不支持规则类型 {rule_type!r}")
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            raise DatasetValidationError(f"{stem}: 规则 {index + 1} 缺少 id")
        if rule_id in seen:
            raise DatasetValidationError(f"{stem}: 规则 id 重复: {rule_id}")
        seen.add(rule_id)


def _jsonl_row(
    *, pdf_path: str, category: str, rule: dict[str, Any], tags: list[str]
) -> dict[str, Any]:
    payload = {
        key: value
        for key, value in rule.items()
        if key not in {"type", "id", "verified", "tags", "page"}
    }
    return {
        "pdf": pdf_path,
        "category": category,
        "id": rule["id"],
        "type": rule["type"],
        "rule": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        "page": rule.get("page"),
        "expected_markdown": None,
        "tags": rule.get("tags") or tags,
    }


def compile_dataset(
    documents: list[GeneratedDocument],
    sidecars: dict[str, dict[str, Any]],
    *,
    dataset_name: str,
    department: str,
    mode: str = "full",
) -> bytes:
    if mode not in {"full", "sidecar", "jsonl"}:
        raise DatasetValidationError("导出模式无效")
    department_slug = slugify(department)
    content_rows: list[dict[str, Any]] = []
    formatting_rows: list[dict[str, Any]] = []
    table_rows: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    all_ids: set[str] = set()

    for document in documents:
        stem = document.source.stem
        sidecar = sidecars.get(stem, document.sidecar)
        validate_sidecar(sidecar, stem)
        tags = sidecar.get("tags") if isinstance(sidecar.get("tags"), list) else []
        pdf_rel = f"docs/{department_slug}/{document.source.pdf_name}"
        for rule in sidecar["test_rules"]:
            if rule["id"] in all_ids:
                raise DatasetValidationError(f"全局规则 id 重复: {rule['id']}")
            all_ids.add(rule["id"])
            if rule["type"] in CONTENT_RULE_TYPES:
                content_rows.append(_jsonl_row(pdf_path=pdf_rel, category="text_content", rule=rule, tags=tags))
            elif rule["type"] in FORMATTING_RULE_TYPES:
                formatting_rows.append(_jsonl_row(pdf_path=pdf_rel, category="text_formatting", rule=rule, tags=tags))

        expected_markdown = sidecar.get("expected_markdown")
        table_blocks = (
            re.findall(r"<table\b[^>]*>.*?</table>", expected_markdown, flags=re.I | re.S)
            if isinstance(expected_markdown, str)
            else []
        )
        table_gold = "\n\n".join(table_blocks) if table_blocks else None
        if table_gold:
            table_id = f"{slugify(stem)}_expected_markdown"
            if table_id in all_ids:
                table_id += "_table"
            all_ids.add(table_id)
            table_rows.append(
                {
                    "pdf": pdf_rel,
                    "category": "table",
                    "id": table_id,
                    "type": "expected_markdown",
                    "rule": "{}",
                    "page": None,
                    "expected_markdown": table_gold,
                    "tags": tags,
                }
            )
        manifest.append(
            {
                "document_id": f"{department_slug}/{stem}",
                "department": department,
                "pdf": pdf_rel,
                "gold_markdown": (
                    f"sidecar/{department_slug}/{document.source.md_name}"
                    if mode == "full"
                    else f"{department_slug}/{document.source.md_name}"
                    if mode == "sidecar"
                    else None
                ),
                "pdf_sha256": hashlib.sha256(document.source.pdf_bytes).hexdigest(),
                "gold_md_sha256": hashlib.sha256(document.source.markdown.encode("utf-8")).hexdigest(),
                "rules": len(sidecar["test_rules"]),
                "tables": 1 if table_gold else 0,
            }
        )

    report = {
        "valid": True,
        "dataset_name": dataset_name,
        "department": department,
        "documents": len(documents),
        "rules": {
            "text_content": len(content_rows),
            "text_formatting": len(formatting_rows),
            "table": len(table_rows),
            "total": len(content_rows) + len(formatting_rows) + len(table_rows),
        },
        "checks": [
            "PDF/Markdown 同名配对通过",
            "Sidecar 结构校验通过",
            "规则类型校验通过",
            "规则 ID 全局唯一",
            "JSONL 编译完成",
        ],
    }

    def lines(rows: list[dict[str, Any]]) -> bytes:
        return ("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)).encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if mode in {"full", "sidecar"}:
            prefix = "sidecar/" if mode == "full" else ""
            for document in documents:
                folder = f"{prefix}{department_slug}/"
                archive.writestr(folder + document.source.pdf_name, document.source.pdf_bytes)
                archive.writestr(folder + document.source.md_name, document.source.markdown.encode("utf-8"))
                archive.writestr(
                    folder + f"{document.source.stem}.test.json",
                    (json.dumps(sidecars.get(document.source.stem, document.sidecar), ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                )
        if mode in {"full", "jsonl"}:
            prefix = "parsebench_jsonl/" if mode == "full" else ""
            for document in documents:
                archive.writestr(
                    f"{prefix}docs/{department_slug}/{document.source.pdf_name}",
                    document.source.pdf_bytes,
                )
            archive.writestr(prefix + "text_content.jsonl", lines(content_rows))
            archive.writestr(prefix + "text_formatting.jsonl", lines(formatting_rows))
            archive.writestr(prefix + "table.jsonl", lines(table_rows))
        archive.writestr(
            "manifest.jsonl",
            lines(manifest),
        )
        archive.writestr(
            "validation_report.json",
            (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
    return buffer.getvalue()

