"""
api/routes/reports.py

Generate and retrieve case reports (a Markdown case summary or a CSV
export of per-document-type ingestion counts). Reports are built on
demand from whatever is actually persisted for a case right now —
case metadata (db.repository.get_case) and per-document-type
ingestion counts (db.repository.get_document_summary_for_case) — then
stored so the Reports page can list and re-download them without
regenerating.

Formats: Markdown, CSV (entity table) and PDF (reportlab). Report content
comes from api/report_builder.py: sources, network size, ranked key
individuals with the reason for each, entities awaiting review, detected
patterns and source documents -- not just document counts.
"""

import base64
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from api import report_builder
from api.auth import get_current_user
from db import repository as repo
from db.connection import get_db
from schema.case import Case
from schema.report import VALID_REPORT_FORMATS, Report
from schema.user import User

router = APIRouter()


def _check_case_access(db: Session, case_id: str, user: User) -> Case:
    """Shared authorization + existence check, same pattern as
    api/routes/query.py: 403 if the user can't see this case at all,
    404 if it doesn't exist.
    """
    authorized_case_ids = {c.id for c in repo.get_cases_for_user(db, user)}
    if case_id not in authorized_case_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not authorized to view this case")
    case = repo.get_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return case


@router.post("/{case_id}/generate", status_code=status.HTTP_201_CREATED)
def generate_report_endpoint(
    case_id: str, data: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    """Generate a new report for a case and persist it.
    Body: {"format": "markdown" | "csv" | "pdf"}.
    """
    case = _check_case_access(db, case_id, user)
    fmt = data.get("format", "markdown")
    if fmt not in VALID_REPORT_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"format must be one of {sorted(VALID_REPORT_FORMATS)}"
        )

    report_data = report_builder.build_case_report_data(db, case)
    if fmt == "markdown":
        content = report_builder.to_markdown(report_data)
    elif fmt == "csv":
        content = report_builder.to_csv(report_data)
    else:
        content = base64.b64encode(report_builder.to_pdf(report_data)).decode("ascii")

    report = Report(id=str(uuid.uuid4()), case_id=case_id, title=f"Case summary — {case.title}", format=fmt, content=content)
    created = repo.create_report(db, report)
    repo.append_audit(db, "report_generated", actor=user, target_type="case", target_id=case_id,
                      detail={"format": fmt, "report_id": created.id})
    return created.to_dict()


@router.get("/{case_id}")
def list_reports_endpoint(case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """List previously generated reports for a case (metadata only — no content)."""
    _check_case_access(db, case_id, user)
    reports = repo.list_reports_for_case(db, case_id)
    return {"reports": [r.to_dict() for r in reports]}


@router.get("/{case_id}/{report_id}/download")
def download_report_endpoint(
    case_id: str, report_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Response:
    """Return a report's raw content with a Content-Disposition
    header so the browser downloads it as a file.
    """
    _check_case_access(db, case_id, user)
    report = repo.get_report(db, report_id)
    if report is None or report.case_id != case_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")

    media_type = {"markdown": "text/markdown", "csv": "text/csv", "pdf": "application/pdf"}[report.format]
    extension = {"markdown": "md", "csv": "csv", "pdf": "pdf"}[report.format]
    content = base64.b64decode(report.content) if report.format == "pdf" else report.content
    repo.append_audit(db, "report_downloaded", actor=user, target_type="case", target_id=case_id,
                      detail={"report_id": report.id, "format": report.format})
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{report.id}.{extension}"'},
    )
