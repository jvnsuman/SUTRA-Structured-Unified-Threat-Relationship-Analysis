"""
api/routes/

Route modules for the API: ingestion, query, evidence, cases, alerts,
reports, settings, cross_case, access_requests, ledger, users. Each
exposes a `router` object that api/main.py registers under its own
prefix (see that file's include_router calls).
"""
