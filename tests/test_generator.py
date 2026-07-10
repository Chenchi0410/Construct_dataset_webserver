import io
import json
import zipfile

from app.generator import SourceDocument, compile_dataset, generate_document, pair_uploads


MARKDOWN = """# Heading level 1
First paragraph.
This is **bold text** and *italic text*.

```python
print("hello")
```

| A | B |
|---|---:|
| 1 | 2 |
"""


def source():
    return SourceDocument("test", "test.pdf", b"%PDF-1.4\nfixture", "test.md", MARKDOWN)


def test_pair_uploads_matches_case_insensitively():
    docs = pair_uploads([("Test.PDF", b"%PDF-1.4\nfixture")], [("test.md", MARKDOWN.encode())])
    assert len(docs) == 1
    assert docs[0].stem == "Test"


def test_generate_supported_dimensions():
    result = generate_document(source(), department="QA", difficulty="easy", document_type="misc")
    types = {rule["type"] for rule in result.sidecar["test_rules"]}
    assert "missing_sentence_percent" in types
    assert "is_title" in types
    assert "is_bold" in types
    assert "is_italic" in types
    assert "is_code_block" in types
    assert result.table_markdown and "<table>" in result.table_markdown


def test_compile_full_zip():
    result = generate_document(source(), department="QA", difficulty="easy", document_type="misc")
    payload = compile_dataset(
        [result], {"test": result.sidecar}, dataset_name="demo", department="QA", mode="full"
    )
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        assert "sidecar/qa/test.test.json" in names
        assert "parsebench_jsonl/text_content.jsonl" in names
        assert "parsebench_jsonl/text_formatting.jsonl" in names
        assert "parsebench_jsonl/table.jsonl" in names
        content = archive.read("parsebench_jsonl/text_content.jsonl").decode()
        first = json.loads(content.splitlines()[0])
        assert first["category"] == "text_content"
        assert isinstance(first["rule"], str)



def test_edited_sidecar_is_table_source_of_truth():
    result = generate_document(source(), department="QA", difficulty="easy", document_type="misc")
    sidecar = dict(result.sidecar)
    sidecar["expected_markdown"] = "<table><tr><td>edited</td></tr></table>"
    payload = compile_dataset(
        [result], {"test": sidecar}, dataset_name="demo", department="QA", mode="jsonl"
    )
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        row = json.loads(archive.read("table.jsonl").decode().strip())
        assert "edited" in row["expected_markdown"]
        assert row["expected_markdown"].count("<table>") == 1
