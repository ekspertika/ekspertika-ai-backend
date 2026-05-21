"""FastAPI entry point for the Ekspertika compliance service."""

import logging
from typing import Literal
from uuid import UUID

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field

from app.flows.compliance_flow import run_compliance_check
from app.flows.supabase_flow import retry_errored_checks, run_project_check
from app.integrations.supabase_client import is_configured as supabase_is_configured
from app.models.check_item import ComplianceResult
from config.config import Config

bearer_scheme = HTTPBearer(auto_error=False)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    openai_configured: bool
    supabase_configured: bool


class CheckSummary(BaseModel):
    total: int
    pass_: int = Field(alias="pass")
    partial: int
    fail: int

    model_config = {"populate_by_name": True}


class CheckResponse(BaseModel):
    """Stateless single-PDF check result. Matches the FE ComplianceResult[] shape."""

    filename: str
    page_count: int
    is_scanned: bool
    warnings: list[str]
    results: list[ComplianceResult]
    summary: CheckSummary


class ProjectCheckAccepted(BaseModel):
    """Returned with HTTP 202 when a project-scoped check has been accepted for background processing."""

    project_id: str
    status: Literal["processing"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _verify_token(authorization: str | None = Header(default=None)) -> None:
    expected = Config.INTERNAL_API_TOKEN
    if not expected:
        return  # token not configured → auth disabled (dev mode)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    if authorization.removeprefix("Bearer ").strip() != expected:
        raise HTTPException(401, "Invalid bearer token")


def _validate_project_id(project_id: str) -> str:
    try:
        return str(UUID(project_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(400, "project_id must be a valid UUID") from exc


def _require_supabase() -> None:
    if not supabase_is_configured():
        raise HTTPException(
            500, "Supabase not configured (SUPABASE_URL / SUPABASE_SERVICE_KEY missing)"
        )


async def _run_project_check_safe(project_id: str) -> None:
    """BackgroundTasks wrapper — failures are already persisted to projects.error
    inside run_project_check, but we swallow the re-raise here so it doesn't bubble
    into FastAPI's task error logger as an unhandled exception."""
    try:
        await run_project_check(project_id)
    except Exception:
        # Already logged + persisted in run_project_check.
        pass


async def _retry_project_check_safe(project_id: str) -> None:
    try:
        await retry_errored_checks(project_id)
    except Exception:
        pass


app = FastAPI(
    title="Ekspertika Compliance API",
    version="0.1.0",
    description=(
        "AI compliance checker for Lithuanian construction documents (STR / HN / LST). "
        "Two flows: stateless single-PDF check (`POST /api/v1/check`) and project-scoped "
        "Supabase-coupled check (`POST /api/v1/check/{project_id}`) that writes results "
        "directly to `str_results` while the FE polls for progress.\n\n"
        "All `/api/v1/*` endpoints require a Bearer token matching `INTERNAL_API_TOKEN` "
        "in the service env."
    ),
    openapi_tags=[
        {"name": "system", "description": "Liveness and configuration probes."},
        {"name": "compliance", "description": "Run compliance checks against the regulatory corpus."},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=Config.allowed_origins(),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get(
    "/health",
    tags=["system"],
    summary="Liveness + configuration probe",
    response_model=HealthResponse,
)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        openai_configured=Config.validate(),
        supabase_configured=supabase_is_configured(),
    )


@app.post(
    "/api/v1/check",
    tags=["compliance"],
    summary="Stateless compliance check on a single PDF",
    response_model=CheckResponse,
    dependencies=[Depends(_verify_token), Depends(bearer_scheme)],
)
async def check(file: UploadFile = File(..., description="Construction project PDF")) -> dict:
    """Run the full compliance pipeline against a single uploaded PDF and return the results in-line.

    Does NOT write to Supabase — use `POST /api/v1/check/{project_id}` for that. Suitable for ad-hoc
    one-off checks (CLI tools, smoke tests, manual experimentation from Swagger).
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "File must be a PDF")
    if not Config.validate():
        raise HTTPException(500, "OPENAI_API_KEY not configured")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(400, "Empty file")

    try:
        return await run_compliance_check(pdf_bytes, filename=file.filename or "document.pdf")
    except Exception as exc:
        logger.exception("Compliance check failed")
        raise HTTPException(500, f"Check failed: {exc}") from exc


@app.post(
    "/api/v1/check/{project_id}",
    tags=["compliance"],
    summary="Kick off a project-scoped compliance run (writes to Supabase)",
    status_code=202,
    response_model=ProjectCheckAccepted,
    dependencies=[Depends(_verify_token), Depends(bearer_scheme)],
)
async def check_project(project_id: str, background_tasks: BackgroundTasks) -> ProjectCheckAccepted:
    """Kick off a Supabase-coupled compliance run for the given project.

    Returns 202 immediately — the FE polls the `projects` + `str_results` tables for progress.
    Failures are caught inside `run_project_check` and persisted to `projects.error`.
    """
    pid = _validate_project_id(project_id)
    if not Config.validate():
        raise HTTPException(500, "OPENAI_API_KEY not configured")
    _require_supabase()

    background_tasks.add_task(_run_project_check_safe, pid)
    return ProjectCheckAccepted(project_id=pid, status="processing")


@app.post(
    "/api/v1/check/{project_id}/retry",
    tags=["compliance"],
    summary="Retry only the errored rows of a previous project check",
    status_code=202,
    response_model=ProjectCheckAccepted,
    dependencies=[Depends(_verify_token), Depends(bearer_scheme)],
)
async def check_project_retry(
    project_id: str, background_tasks: BackgroundTasks
) -> ProjectCheckAccepted:
    """Re-run only `str_results` rows where `is_error=true`."""
    pid = _validate_project_id(project_id)
    if not Config.validate():
        raise HTTPException(500, "OPENAI_API_KEY not configured")
    _require_supabase()

    background_tasks.add_task(_retry_project_check_safe, pid)
    return ProjectCheckAccepted(project_id=pid, status="processing")
