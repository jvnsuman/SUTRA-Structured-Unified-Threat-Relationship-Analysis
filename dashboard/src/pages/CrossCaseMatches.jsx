/**
 * pages/CrossCaseMatches.jsx
 *
 * Deliberately a SEPARATE page from the main Network Graph — NOT
 * entities merged into the current case's graph (see api/routes/
 * cross_case.py's module docstring for why). Shows every entity in
 * the currently selected case that also appears in a case the
 * viewer isn't authorized for, gated by that other case's
 * confidentiality tier:
 *   - normal: title + description + owning agency shown
 *   - restricted: ONLY the owning agency name shown
 * with a "Request Access" action either way.
 *
 * Also surfaces this user's own pending outgoing requests (nothing
 * fancy — just enough to avoid double-requesting the same case),
 * requests awaiting THEIR decision (Investigators/Admins), and the
 * full resolved+pending history of requests made against whichever
 * case is currently selected (api/routes/access_requests.py's
 * GET /case/{case_id}, visible to anyone authorized to view that
 * case).
 */

import { AlertTriangle, Lock, ShieldQuestion, Users } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { useToast } from '../components/Toast'

const CAN_APPROVE_ROLES = new Set(['investigator', 'admin', 'super_admin'])

function RequestAccessButton({ caseId, match, onRequested }) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const showToast = useToast()

  async function submit(e) {
    e.preventDefault()
    if (!reason.trim()) return
    setSubmitting(true)
    try {
      await api.requestCrossCaseAccess(caseId, match.matched_entity_id, match.other_case.case_id, reason.trim())
      showToast('Access request sent.', 'success')
      setOpen(false)
      setReason('')
      onRequested()
    } catch (err) {
      showToast(err.message, 'error')
    } finally {
      setSubmitting(false)
    }
  }

  if (!open) {
    return (
      <button type="button" className="btn-secondary" onClick={() => setOpen(true)}>
        Request Access
      </button>
    )
  }

  return (
    <form className="cross-case-request-form" onSubmit={submit}>
      <textarea
        rows={2}
        placeholder="Why do you need access to this case?"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        disabled={submitting}
        autoFocus
      />
      <div className="cross-case-request-form-actions">
        <button type="button" className="btn-secondary" onClick={() => setOpen(false)} disabled={submitting}>
          Cancel
        </button>
        <button type="submit" className="btn-primary" disabled={submitting || !reason.trim()}>
          {submitting ? 'Sending…' : 'Send Request'}
        </button>
      </div>
    </form>
  )
}

function MatchCard({ caseId, match, onRequested }) {
  const isRestricted = match.other_case.confidentiality === 'restricted'
  return (
    <div className="panel cross-case-match-card">
      <div className="cross-case-match-entity">
        <span className={`type-pill tint-${match.matched_entity_type === 'person' ? 'violet' : 'primary'}`}>
          {match.matched_entity_type}
        </span>
        <strong>{match.matched_entity_text}</strong>
        <span className="cross-case-match-score">{Math.round(match.similarity)}% match</span>
      </div>

      <div className="cross-case-match-other-case">
        {isRestricted ? (
          <div className="cross-case-restricted-notice">
            <Lock size={15} />
            <span>
              This entity also appears in a <strong>restricted</strong> case belonging to{' '}
              <strong>{match.other_case.agency_name}</strong>. No further details can be shown without approval.
            </span>
          </div>
        ) : (
          <div>
            <div className="cross-case-match-other-title">{match.other_case.title}</div>
            <p className="cross-case-match-other-description">{match.other_case.description}</p>
            <span className="cross-case-match-agency">
              <Users size={13} /> {match.other_case.agency_name}
            </span>
          </div>
        )}
      </div>

      <RequestAccessButton caseId={caseId} match={match} onRequested={onRequested} />
    </div>
  )
}

function RequestHistoryPanel({ selectedCaseId }) {
  const [history, setHistory] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!selectedCaseId) {
      setHistory(null)
      return
    }
    let cancelled = false
    api
      .getAccessRequestsForCase(selectedCaseId)
      .then((data) => {
        if (!cancelled) setHistory(data.requests || [])
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [selectedCaseId])

  if (!selectedCaseId || history === null) return null

  return (
    <div className="panel">
      <div className="panel-header">
        <h3>Access Request History</h3>
        <span className="panel-header-sub">Every request — resolved or pending — for this case&apos;s data</span>
      </div>
      {error && <p className="panel-status">{error}</p>}
      {history.length === 0 ? (
        <p className="panel-status">No one has requested access to this case yet.</p>
      ) : (
        <ul className="pending-request-list">
          {history.map((r) => (
            <li key={r.id} className="pending-request-item">
              <div>
                <strong className={`access-request-status status-${r.status}`}>{r.status.replace(/_/g, ' ')}</strong>
                <p className="pending-request-reason">{r.reason}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function PendingDecisionsPanel({ session }) {
  const [pending, setPending] = useState(null)
  const [error, setError] = useState(null)
  const showToast = useToast()

  function refresh() {
    api
      .getPendingAccessRequestsForMe()
      .then((data) => setPending(data.requests || []))
      .catch((err) => setError(err.message))
  }

  useEffect(() => {
    if (CAN_APPROVE_ROLES.has(session.role)) refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!CAN_APPROVE_ROLES.has(session.role)) return null
  if (error) return <div className="panel panel-status">{error}</div>
  if (pending === null) return null

  async function act(requestId, approve) {
    try {
      if (approve) {
        await api.approveAccessRequest(requestId)
        showToast('Request approved.', 'success')
      } else {
        await api.denyAccessRequest(requestId)
        showToast('Request denied — escalated to admin if not already resolved there.', 'success')
      }
      refresh()
    } catch (err) {
      showToast(err.message, 'error')
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <h3>Awaiting Your Decision</h3>
      </div>
      {pending.length === 0 ? (
        <p className="panel-status">No access requests awaiting your decision.</p>
      ) : (
        <ul className="pending-request-list">
          {pending.map((r) => (
            <li key={r.id} className="pending-request-item">
              <div>
                <strong>Case {r.target_case_id}</strong>
                <p className="pending-request-reason">{r.reason}</p>
              </div>
              <div className="pending-request-actions">
                <button type="button" className="btn-secondary" onClick={() => act(r.id, false)}>
                  Deny
                </button>
                <button type="button" className="btn-primary" onClick={() => act(r.id, true)}>
                  Approve
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function CrossCaseMatches({ session, selectedCaseId }) {
  const [matches, setMatches] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  function refresh() {
    if (!selectedCaseId) return
    setLoading(true)
    api
      .getCrossCaseMatches(selectedCaseId)
      .then((data) => {
        setMatches(data.matches || [])
        setError(null)
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedCaseId])

  return (
    <div className="page-cross-case">
      <h1>Cross-Case Matches</h1>
      <p className="page-sub">
        Entities in this case that also appear in other cases you aren&apos;t currently authorized to view.
      </p>

      <PendingDecisionsPanel session={session} />
      <RequestHistoryPanel selectedCaseId={selectedCaseId} />

      {!selectedCaseId ? (
        <div className="panel panel-status">Select a case to check for cross-case matches.</div>
      ) : loading || (matches === null && !error) ? (
        <div className="panel panel-status">Checking for matches…</div>
      ) : error ? (
        <div className="error-banner">
          <AlertTriangle size={15} />
          {error}
        </div>
      ) : matches.length === 0 ? (
        <div className="panel panel-status">
          <ShieldQuestion size={16} />
          No cross-case matches found for this case&apos;s entities.
        </div>
      ) : (
        <div className="cross-case-match-list">
          {matches.map((m) => (
            <MatchCard
              key={`${m.matched_entity_id}-${m.other_case.case_id}`}
              caseId={selectedCaseId}
              match={m}
              onRequested={refresh}
            />
          ))}
        </div>
      )}
    </div>
  )
}
