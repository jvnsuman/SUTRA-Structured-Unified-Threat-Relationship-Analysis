"""
schema/report.py

A generated case report — a Markdown case summary or a CSV export of
per-document-type ingestion counts. See api/routes/reports.py for how
these get built (from whatever is actually persisted for a case right
now — schema.case.Case metadata and db.repository.get_document_summary_for_case)
and db/repository.py for persistence.

Kept separate from schema/case.py: a Report is a derived, disposable
artifact generated from a case's state, not part of the case itself.

PDF (reportlab) is stored base64-encoded in `content`, since the column
is text; api/routes/reports.py decodes it on download.
"""

from dataclasses import dataclass
from typing import Optional

VALID_REPORT_FORMATS = frozenset({"markdown", "csv", "pdf"})


@dataclass
class Report:
    """A single generated report, persisted so it can be listed and
    re-downloaded without regenerating it.
    """

    id: str
    case_id: str
    title: str
    format: str  # one of VALID_REPORT_FORMATS
    content: str
    created_at: Optional[str] = None  # server-assigned at persistence time (db.models.ReportORM's default)

    def __post_init__(self):
        """Validate on construction so an invalid Report can't exist."""
        if not self.id:
            raise ValueError("Report.id must be non-empty")
        if not self.case_id:
            raise ValueError("Report.case_id must be non-empty")
        if not self.title or not self.title.strip():
            raise ValueError("Report.title must be non-empty")
        if self.format not in VALID_REPORT_FORMATS:
            raise ValueError(f"Unknown report format {self.format!r}; must be one of {sorted(VALID_REPORT_FORMATS)}")

    def to_dict(self, include_content: bool = False) -> dict:
        """Serialize to a plain dict. Content is omitted by default —
        the list endpoint only needs metadata; the download endpoint
        opts in with include_content=True.
        """
        data = {
            "id": self.id,
            "case_id": self.case_id,
            "title": self.title,
            "format": self.format,
            "created_at": self.created_at,
        }
        if include_content:
            data["content"] = self.content
        return data
