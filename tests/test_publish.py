import io
import zipfile

import pytest
from fastapi import HTTPException

from app.generator import DatasetValidationError
from app.main import _publish_archive


def archive_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_publish_archive_uses_configured_shared_directory(tmp_path, monkeypatch):
    shared = tmp_path / "datasets"
    monkeypatch.setenv("SHARED_DATASET_DIR", str(shared))
    payload = archive_bytes({"sample.pdf": b"%PDF-1.4", "validation_report.json": b"{}"})

    target = _publish_archive(payload, "department_v1")

    assert target == shared / "department_v1"
    assert (target / "sample.pdf").read_bytes() == b"%PDF-1.4"
    assert (target / "validation_report.json").read_bytes() == b"{}"


def test_publish_archive_does_not_overwrite_existing_dataset(tmp_path, monkeypatch):
    shared = tmp_path / "datasets"
    existing = shared / "department_v1"
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_text("original", encoding="utf-8")
    monkeypatch.setenv("SHARED_DATASET_DIR", str(shared))

    with pytest.raises(HTTPException) as exc_info:
        _publish_archive(archive_bytes({"new.txt": b"new"}), "department_v1")

    assert exc_info.value.status_code == 409
    assert (existing / "keep.txt").read_text(encoding="utf-8") == "original"
    assert not (existing / "new.txt").exists()


def test_publish_archive_rejects_nested_or_escaping_paths(tmp_path, monkeypatch):
    shared = tmp_path / "datasets"
    monkeypatch.setenv("SHARED_DATASET_DIR", str(shared))

    with pytest.raises(DatasetValidationError):
        _publish_archive(archive_bytes({"../outside.txt": b"unsafe"}), "unsafe")

    assert not (tmp_path / "outside.txt").exists()
    assert not (shared / "unsafe").exists()
