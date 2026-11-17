from fastapi import Request, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ConfigDict
from uuid import UUID
from pathlib import Path
from . import datasets
from .records import get, list_records

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Scan(Strict):
    request_id: UUID
    source: str
    role: str = Field(min_length=1, max_length=80)
    language: str = "zh"
    recursive: bool = True

class Prepare(Strict):
    request_id: UUID
    scan_id: str
    excluded: list[str] = Field(default_factory=list)
    min_seconds: float = Field(default=1, ge=.5, le=30)
    max_seconds: float = Field(default=12, ge=.5, le=30)
    silence_db: float = Field(default=-40, ge=-80, le=-10)
    silence_seconds: float = Field(default=.4, ge=.1, le=3)

class ClipEdit(Strict):
    id: str
    text: str | None = None
    language: str | None = None
    reviewed: bool | None = None
    excluded: bool | None = None

class DraftEdit(Strict):
    revision: int
    clips: list[ClipEdit]

class Publish(Strict):
    revision: int

class Retry(Strict):
    request_id: UUID
    clip_ids: list[str]

class Folder(Strict):
    initial: str = ""

class Identified(Strict):
    request_id: UUID

def register(app):
    @app.post("/api/folders/pick")
    def folder(body: Folder):
        from .folder_picker import pick_folder
        return {"path": pick_folder(body.initial)}

    @app.post("/api/datasets/scan", status_code=202)
    def scan(body: Scan, request: Request):
        return request.app.state.jobs.submit("dataset_scan", body.model_dump(exclude={"request_id"}), str(body.request_id))

    @app.post("/api/datasets/prepare", status_code=202)
    def prepare(body: Prepare, request: Request):
        jobs = request.app.state.jobs
        get(jobs, "scan", body.scan_id)
        if body.min_seconds > body.max_seconds:
            raise ValueError("最小时长不能超过最大片段时长")
        return jobs.submit("dataset_prepare", body.model_dump(exclude={"request_id"}), str(body.request_id))

    @app.get("/api/datasets")
    def versions(request: Request):
        return datasets.versions(request.app.state.jobs)

    @app.post("/api/dataset-versions/{key}/draft")
    def edit_version(key: str, body: Identified, request: Request):
        return datasets.edit_version(request.app.state.jobs, key, str(body.request_id))

    @app.get("/api/dataset-drafts")
    def drafts(request: Request):
        return list_records(request.app.state.jobs, "draft")

    @app.get("/api/dataset-drafts/{key}")
    def draft(key: str, request: Request):
        return datasets.get_draft(request.app.state.jobs, key)

    @app.patch("/api/dataset-drafts/{key}")
    def edit(key: str, body: DraftEdit, request: Request):
        return datasets.update_draft(request.app.state.jobs, key, body.revision,
                                     [c.model_dump(exclude_none=True) for c in body.clips])

    @app.post("/api/dataset-drafts/{key}/publish")
    def publish(key: str, body: Publish, request: Request):
        return datasets.publish(request.app.state.jobs, key, body.revision)

    @app.post("/api/dataset-drafts/{key}/asr", status_code=202)
    def retry(key: str, body: Retry, request: Request):
        jobs = request.app.state.jobs
        datasets.get_draft(jobs, key)
        return jobs.submit("dataset_asr", {"draft_id": key, "clip_ids": body.clip_ids}, str(body.request_id))

    @app.get("/api/audio/{key}")
    def audio(key: str, request: Request, download: bool = False):
        record = get(request.app.state.jobs, "media", key)
        path = Path(record["path"])
        if record.get("state") == "DELETED" or not path.is_file():
            raise HTTPException(410, "AUDIO_EXPIRED：音频已失效")
        return FileResponse(path, filename=path.name if download else None)
