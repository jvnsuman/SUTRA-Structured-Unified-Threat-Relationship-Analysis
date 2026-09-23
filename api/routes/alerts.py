"""
api/routes/alerts.py

Serves the "Recent Alerts" feed shown on the new dashboard home page
(see SUTRA_Project_Notes.md Section 12/17 for the mockup this
implements). This is NOT a separate alerting subsystem — it is a thin
read-only view over graph.analytics.detect_anomalies, which already
exists and is real (tested in tests/test_graph_pipeline.py). No
alert is synthesized or hardcoded here.

Entity/relationship persistence (schema/entities.py's Entity/
Relationship -> EntityORM/RelationshipORM) is now wired up in
ingestion.py, so this runs detect_anomalies against a real,
case-specific graph when one exists.

Financial-structuring / communication-burst checks (previously
graph-structural-only): detect_anomalies's two document-based checks
need documents shaped like data/generate_synthetic.py's
SyntheticDocument (.doc_id + .structured fields). schema.entities.
SourceDocument now carries the same .structured field (see that
module's docstring), so real ingested CDR/financial documents can feed
these checks too, not just synthetic ones — this route now fetches a
case's real documents via db.repository.get_documents_for_case and
passes them through. Documents with no structured data (FIRs,
surveillance reports, etc.) simply contribute nothing to these two
checks, same as before.

Status: [DONE] — real code path, running for real once a case has
persisted entities. Honest empty-state (not fabricated) when it doesn't.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.auth import get_current_user
from db import repository as repo
from db.connection import get_db
from graph.analytics import detect_anomalies
from api.casegraph import build_case_graph
from schema.user import User

router = APIRouter()


# Anomaly-type -> (display title template, severity, hashtag-style tags)
# shown in the alert feed. Keeps api/routes/alerts.py decoupled from
# graph.analytics's internal finding "type" strings changing shape.
_ALERT_PRESENTATION = {
    "hub_and_spoke": {
        "title": "High Risk Connection Detected",
        "severity": "high",
        "tags": ["#HighRisk", "#Association"],
    },
    "financial_structuring": {
        "title": "Suspicious Transaction",
        "severity": "medium",
        "tags": ["#Financial", "#Suspicious"],
    },
    "communication_burst": {
        "title": "Communication Burst Identified",
        "severity": "medium",
        "tags": ["#Phone", "#Pattern"],
    },
}


@router.get("/{case_id}")
def list_alerts(case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Return real anomaly-detection findings for a case, presented as
    alert-feed entries. Requires the same case authorization as
    api/routes/query.py — an alert feed is still case data.
    """
    authorized_case_ids = {c.id for c in repo.get_cases_for_user(db, user)}
    if case_id not in authorized_case_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to view alerts for this case",
        )

    graph, _resolved, _mentions = build_case_graph(db, case_id)
    if graph is not None:
        # Real documents now passed through — see module docstring.
        # detect_anomalies duck-types on .doc_id/.structured, and
        # schema.entities.SourceDocument has both (structured defaults
        # to {}, so FIR/surveillance/etc documents simply contribute
        # nothing to the two document-based checks below).
        documents = repo.get_documents_for_case(db, case_id)
        findings = detect_anomalies(graph, documents)
    else:
        findings = []

    alerts = []
    now = datetime.now(timezone.utc).isoformat()
    for index, finding in enumerate(findings):
        presentation = _ALERT_PRESENTATION.get(
            finding["type"],
            {"title": finding["type"].replace("_", " ").title(), "severity": "medium", "tags": []},
        )
        alerts.append({
            # Unique per finding: several findings of one type can share (or
            # lack) a doc_id/node_id (e.g. aggregate structuring findings), and
            # the dashboard uses this id as a React list key.
            "id": f"alert-{index}-{finding.get('doc_id') or finding.get('node_id') or 'case'}-{finding['type']}",
            "title": presentation["title"],
            "detail": finding["detail"],
            "severity": presentation["severity"],
            "tags": presentation["tags"],
            "timestamp": now,
        })

    return {"case_id": case_id, "alerts": alerts}
