from __future__ import annotations

import json
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .generator import (
    DatasetValidationError,
    GeneratedDocument,
    compile_dataset,
    generate_document,
    pair_uploads,
    slugify,
)
from .parsebench_compat import (
    ParseBenchCompatibilityError,
    build_compatibility_profiles,
)


BASE_DIR = Path(__file__).resolve().parent
SESSION_TTL_SECONDS = 2 * 60 * 60


@dataclass(slots=True)
class BuildSession:
    created_at: float
    dataset_name: str
    department: str
    documents: list[GeneratedDocument]


class ExportPayload(BaseModel):
    sidecars: dict[str, dict[str, Any]] | None = None


app = FastAPI(
    title="ParseBench Dataset Builder",
    version="1.0.0",
    description="将同名 PDF 与黄金 Markdown 编译为 Sidecar 和 ParseBench JSONL 数据集。",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

_sessions: dict[str, BuildSession] = {}
_session_lock = Lock()


def _cleanup_sessions() -> None:
    cutoff = time.time() - SESSION_TTL_SECONDS
    with _session_lock:
        expired = [key for key, value in _sessions.items() if value.created_at < cutoff]
        for key in expired:
            del _sessions[key]


def _get_session(session_id: str) -> BuildSession:
    _cleanup_sessions()
    with _session_lock:
        session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="构建会话不存在或已过期，请重新上传")
    return session


@app.exception_handler(DatasetValidationError)
async def validation_error_handler(_, exc: DatasetValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(ParseBenchCompatibilityError)
async def compatibility_error_handler(_, exc: ParseBenchCompatibilityError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "parsebench-dataset-builder", "version": app.version}


@app.post("/api/analyze")
async def analyze(
    pdf_files: list[UploadFile] = File(...),
    md_files: list[UploadFile] = File(...),
    dataset_name: str = Form("custom_parsebench_dataset"),
    department: str = Form("default"),
) -> dict[str, Any]:
    dataset_name = dataset_name.strip() or "custom_parsebench_dataset"
    department = department.strip() or "default"
    if len(pdf_files) > 200 or len(md_files) > 200:
        raise DatasetValidationError("单次最多上传 200 对文件")
    pdf_payloads: list[tuple[str, bytes]] = []
    md_payloads: list[tuple[str, bytes]] = []
    total_bytes = 0
    for file in pdf_files:
        data = await file.read(100 * 1024 * 1024 + 1)
        total_bytes += len(data)
        pdf_payloads.append((file.filename or "", data))
    for file in md_files:
        data = await file.read(25 * 1024 * 1024 + 1)
        total_bytes += len(data)
        md_payloads.append((file.filename or "", data))
    if total_bytes > 500 * 1024 * 1024:
        raise DatasetValidationError("单次上传总大小不能超过 500 MB")
    sources = pair_uploads(pdf_payloads, md_payloads)
    profiles = build_compatibility_profiles([source.markdown for source in sources])
    documents = [
        generate_document(
            source,
            department=department,
            compatibility_profile=profile,
        )
        for source, profile in zip(sources, profiles)
    ]
    session_id = uuid.uuid4().hex
    with _session_lock:
        _sessions[session_id] = BuildSession(
            created_at=time.time(),
            dataset_name=dataset_name,
            department=department,
            documents=documents,
        )
    return {
        "session_id": session_id,
        "dataset_name": dataset_name,
        "department": department,
        "expires_in_seconds": SESSION_TTL_SECONDS,
        "documents": [
            {
                "stem": doc.source.stem,
                "pdf_name": doc.source.pdf_name,
                "md_name": doc.source.md_name,
                "stats": doc.stats,
                "warnings": doc.warnings,
                "sidecar": doc.sidecar,
            }
            for doc in documents
        ],
        "totals": {
            "documents": len(documents),
            "rules": sum(doc.stats["rules"] for doc in documents),
            "content_rules": sum(doc.stats["content_rules"] for doc in documents),
            "formatting_rules": sum(doc.stats["formatting_rules"] for doc in documents),
            "tables": sum(doc.stats["tables"] for doc in documents),
        },
    }


@app.post("/api/export/{session_id}")
def export_dataset(session_id: str, payload: ExportPayload, mode: str = "full") -> Response:
    session = _get_session(session_id)
    sidecars = payload.sidecars or {doc.source.stem: doc.sidecar for doc in session.documents}
    unknown = set(sidecars) - {doc.source.stem for doc in session.documents}
    if unknown:
        raise DatasetValidationError("包含未知文档的 Sidecar: " + ", ".join(sorted(unknown)))
    for doc in session.documents:
        if doc.source.stem not in sidecars:
            sidecars[doc.source.stem] = doc.sidecar
    data = compile_dataset(
        session.documents,
        sidecars,
        dataset_name=session.dataset_name,
        department=session.department,
        mode=mode,
    )
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", session.dataset_name).strip("._") or "dataset"
    suffix = {"full": "complete", "sidecar": "sidecar", "jsonl": "jsonl"}.get(mode, mode)
    filename = f"{safe_name}_{suffix}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
