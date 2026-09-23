/**
 * pages/LedgerIntegrity.jsx
 *
 * The dashboard surface for ledger/chain.py's hash-chained, append-
 * only integrity ledger (see api/routes/ledger.py). Deliberately not
 * scoped to the currently selected case — the ledger is a system-wide
 * audit trail (case creation, evidence linking, access-request
 * resolution across every case), not a per-case artifact.
 *
 * "Verify Now" actually recomputes every hash in the chain
 * server-side (ledger.chain.verify_chain) — this is real work, not a
 * canned "OK" — and reports either "Chain intact" or exactly which
 * entry broke and why, matching this project's UI-honesty rule
 * (described-only elements must not look functional; here the
 * inverse also holds — a functional integrity check must not be
 * dressed up as more mysterious/impressive than it is).
 */

import { CheckCircle2, Link2, RefreshCw, ShieldAlert, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api/client'

const EVENT_TYPE_LABELS = {
  genesis: 'Genesis',
  case_created: 'Case Created',
  evidence_linked: 'Evidence Linked',
  access_request_resolved: 'Access Request Resolved',
}

function formatTimestamp(iso) {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

function EntryPayloadSummary({ entry }) {
  const p = entry.payload
  switch (entry.event_type) {
    case 'case_created':
      return <span>{p.title} ({p.agency_id})</span>
    case 'evidence_linked':
      return <span>Entity {p.entity_id} ← document {p.document_id}</span>
    case 'access_request_resolved':
      return <span>Case {p.target_case_id} — {p.resolution} by {p.resolved_by_user_id}</span>
    case 'genesis':
      return <span>{p.message}</span>
    default:
      return <span>{JSON.stringify(p)}</span>
  }
}

export default function LedgerIntegrity() {
  const [entries, setEntries] = useState(null)
  const [entriesError, setEntriesError] = useState(null)
  const [loadingEntries, setLoadingEntries] = useState(false)

  const [verification, setVerification] = useState(null)
  const [verifying, setVerifying] = useState(false)
  const [verifyError, setVerifyError] = useState(null)

  function loadEntries() {
    setLoadingEntries(true)
    api
      .getLedgerEntries()
      .then((data) => {
        setEntries(data.entries || [])
        setEntriesError(null)
      })
      .catch((err) => setEntriesError(err.message))
      .finally(() => setLoadingEntries(false))
  }

  async function runVerification() {
    setVerifying(true)
    setVerifyError(null)
    try {
      const result = await api.verifyLedger()
      setVerification(result)
    } catch (err) {
      setVerifyError(err.message)
    } finally {
      setVerifying(false)
    }
  }

  useEffect(() => {
    loadEntries()
    runVerification()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="page-ledger">
      <h1>Integrity Ledger</h1>
      <p className="page-sub">
        A hash-chained, append-only audit trail of case creation, evidence linking, and access-request
        decisions across every case. Each entry's hash depends on the one before it, so altering or
        deleting any past entry is detectable.
      </p>

      <div className="panel ledger-verify-panel">
        <div className="ledger-verify-header">
          <div>
            <h3>Chain Verification</h3>
            <p className="panel-status">Recomputes every entry's hash from scratch — not a cached result.</p>
          </div>
          <button type="button" className="btn-primary" onClick={runVerification} disabled={verifying}>
            <RefreshCw size={14} className={verifying ? 'spin' : ''} />
            {verifying ? 'Verifying…' : 'Verify Now'}
          </button>
        </div>

        {verifyError ? (
          <div className="error-banner">
            <ShieldAlert size={15} />
            {verifyError}
          </div>
        ) : verification ? (
          <div className={`ledger-verify-result ${verification.valid ? 'ledger-verify-ok' : 'ledger-verify-broken'}`}>
            {verification.valid ? <CheckCircle2 size={18} /> : <XCircle size={18} />}
            <div>
              <strong>{verification.valid ? 'Chain intact' : 'Tampering detected'}</strong>
              <p>
                {verification.entries_checked} entr{verification.entries_checked === 1 ? 'y' : 'ies'} checked.{' '}
                {verification.detail}
                {!verification.valid && verification.first_broken_sequence_number !== null && (
                  <> First broken entry: sequence #{verification.first_broken_sequence_number}.</>
                )}
              </p>
            </div>
          </div>
        ) : null}
      </div>

      <div className="panel">
        <div className="panel-header">
          <h3>
            <Link2 size={15} /> Chain Entries
          </h3>
        </div>

        {loadingEntries || (entries === null && !entriesError) ? (
          <p className="panel-status">Loading…</p>
        ) : entriesError ? (
          <div className="error-banner">
            <ShieldAlert size={15} />
            {entriesError}
          </div>
        ) : entries.length === 0 ? (
          <p className="panel-status">No ledger entries yet.</p>
        ) : (
          <table className="ledger-entry-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Event</th>
                <th>Details</th>
                <th>Timestamp</th>
                <th>Hash</th>
              </tr>
            </thead>
            <tbody>
              {[...entries].reverse().map((entry) => (
                <tr key={entry.id}>
                  <td>{entry.sequence_number}</td>
                  <td>
                    <span className="type-pill tint-primary">
                      {EVENT_TYPE_LABELS[entry.event_type] || entry.event_type}
                    </span>
                  </td>
                  <td>
                    <EntryPayloadSummary entry={entry} />
                  </td>
                  <td>{formatTimestamp(entry.created_at)}</td>
                  <td className="ledger-hash-cell" title={entry.entry_hash}>
                    {entry.entry_hash.slice(0, 12)}…
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
