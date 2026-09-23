"""
api/routes/ledger.py

Read-only endpoints for the hash-chained integrity ledger (see
ledger/chain.py for the design). There is no write endpoint here on
purpose — every entry is appended internally, from the specific event
call sites that append_ledger_entry is used from (case creation,
evidence linking, access-request resolution), never directly from a
client request. Writing to the ledger is a side effect of doing
something else, not an action in its own right.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.auth import get_current_user
from db import repository as repo
from db.connection import get_db
from ledger.chain import verify_chain
from schema.user import User

router = APIRouter()


@router.get("/verify")
def verify_ledger(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Walk the entire chain and recompute every hash — the actual
    tamper-evidence check (see ledger.chain.verify_chain). Available
    to any authenticated user: verifying integrity doesn't reveal any
    case-specific data (payloads are returned in /entries below, which
    is where access control on their CONTENT would need to live if
    this project's payloads ever grow to include anything sensitive —
    right now they only carry IDs, not raw case content).
    """
    entries = repo.get_all_ledger_entries(db)
    result = verify_chain(entries)
    return result.to_dict()


@router.get("/entries")
def list_ledger_entries(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Every ledger entry, in order — the full audit trail. See
    ledger.chain.LedgerEntry.to_dict for the shape of each entry.
    """
    entries = repo.get_all_ledger_entries(db)
    return {"entries": [e.to_dict() for e in entries]}
