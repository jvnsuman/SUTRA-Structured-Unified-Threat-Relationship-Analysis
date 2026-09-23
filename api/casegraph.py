"""
api/casegraph.py

One function that turns a case's persisted mentions + relations into a
resolved graph, applying the investigators' resolution overrides.
api/routes/query.py, alerts.py and reports.py all go through it, so the
graph an investigator sees, the alerts computed on it and the report
generated from it can never disagree about who is the same person.
"""

from sqlalchemy.orm import Session

import graph.build as graph_build
from db import repository as repo
from nlp.resolution import resolve_entities


def build_case_graph(db: Session, case_id: str):
    """Returns (graph, resolved_entities, mentions); graph is None when
    the case has no persisted entities yet."""
    mentions = repo.get_entities_for_case(db, case_id)
    if not mentions:
        return None, [], []
    overrides = repo.get_resolution_overrides(db, case_id)
    resolved = resolve_entities(mentions, overrides=overrides)
    relations = repo.get_relationships_for_case(db, case_id)
    return graph_build.build_graph(resolved, relations), resolved, mentions
