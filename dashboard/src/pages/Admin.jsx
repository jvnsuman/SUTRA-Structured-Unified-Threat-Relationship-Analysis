/**
 * dashboard/src/pages/Admin.jsx
 *
 * Admin/Super-Admin-only page for three backend capabilities that
 * previously had no frontend surface at all:
 *
 *  - Escalated access requests (api/routes/access_requests.py's
 *    GET /pending/admin) — a deny at PENDING_INVESTIGATOR escalates
 *    here rather than closing the request (see that module's
 *    docstring); before this page existed, an escalated request had
 *    no way to ever be resolved.
 *  - Case confidentiality (api/routes/cases.py's POST
 *    /{case_id}/confidentiality) — the "normal" vs "restricted" tier
 *    that CrossCaseMatches.jsx's whole display already branches on,
 *    but that no one could actually set.
 *  - Assigning/removing an investigator on a case
 *    (api/routes/cases.py's POST /{case_id}/assign and /unassign).
 *    There's no user-listing endpoint in this project (see
 *    api/routes/users.py's docstring), so a new investigator is
 *    looked up by badge ID one at a time via GET
 *    /users/lookup/{badge_id}, and existing ones are resolved back
 *    to names via GET /users/{user_id} rather than shown as raw ids.
 *  - Creating a new account (api/routes/users.py's POST /) —
 *    there was previously no way to provision an account at all
 *    except a seed script. Scoped to investigator/analyst roles in
 *    the admin's own agency from this form; an admin/super_admin
 *    account or a cross-agency account still requires calling the
 *    API directly (see that route's docstring for why).
 *
 * All panels act on whichever case is currently selected in the
 * toolbar's CaseSelector (where relevant), the same pattern
 * Reports/DataSources/CrossCaseMatches already use. The case list
 * itself is fetched once here and passed down, rather than each
 * panel fetching its own copy.
 */

import { AlertCircle, ShieldAlert, ShieldCheck, UserMinus, UserPlus } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { useToast } from '../components/Toast'

function EscalatedRequestsPanel() {
  const [pending, setPending] = useState(null)
  const [error, setError] = useState(null)
  const showToast = useToast()

  const refresh = useCallback(() => {
    api
      .getPendingAccessRequestsForAdmin()
      .then((data) => setPending(data.requests || []))
      .catch((err) => setError(err.message))
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  async function act(requestId, approve) {
    try {
      if (approve) {
        await api.approveAccessRequest(requestId)
        showToast('Request approved.', 'success')
      } else {
        await api.denyAccessRequest(requestId)
        showToast('Request denied.', 'success')
      }
      refresh()
    } catch (err) {
      showToast(err.message, 'error')
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <h3>Escalated Access Requests</h3>
      </div>
      <p className="panel-note">
        Requests an investigator denied (or that timed out awaiting their decision) — this is the final review.
      </p>
      {error && (
        <div className="notice-banner small">
          <AlertCircle size={13} />
          {error}
        </div>
      )}
      {pending === null ? (
        <p className="panel-status">Loading...</p>
      ) : pending.length === 0 ? (
        <p className="panel-status">No access requests awaiting admin review.</p>
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

function ConfidentialityPanel({ cases, refreshCases, selectedCaseId }) {
  const [saving, setSaving] = useState(false)
  const showToast = useToast()

  const currentCase = cases?.find((c) => c.id === selectedCaseId) || null

  async function handleSet(tier) {
    if (!selectedCaseId) return
    setSaving(true)
    try {
      await api.setCaseConfidentiality(selectedCaseId, tier)
      showToast(`Case marked ${tier}.`, 'success')
      refreshCases()
    } catch (err) {
      showToast(err.message, 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <h3>Case Confidentiality</h3>
      </div>
      {!selectedCaseId ? (
        <p className="panel-status">Select a case in the toolbar above to manage its confidentiality tier.</p>
      ) : !currentCase ? (
        <p className="panel-status">Loading...</p>
      ) : (
        <>
          <p className="panel-note">
            <strong>{currentCase.title}</strong> is currently <strong>{currentCase.confidentiality}</strong>.
            Restricted cases show an unauthorized cross-case viewer only the owning agency&apos;s name; normal
            cases also show a title and short summary alongside the &quot;Request Access&quot; action.
          </p>
          <div className="admin-tier-actions">
            <button
              type="button"
              className="btn-secondary"
              disabled={saving || currentCase.confidentiality === 'normal'}
              onClick={() => handleSet('normal')}
            >
              <ShieldCheck size={14} />
              Mark Normal
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={saving || currentCase.confidentiality === 'restricted'}
              onClick={() => handleSet('restricted')}
            >
              <ShieldAlert size={14} />
              Mark Restricted
            </button>
          </div>
        </>
      )}
    </div>
  )
}

function CurrentInvestigatorsList({ currentCase, onRemoved }) {
  const [resolved, setResolved] = useState({})
  const [removingId, setRemovingId] = useState(null)
  const showToast = useToast()

  useEffect(() => {
    let cancelled = false
    for (const id of currentCase.assigned_investigator_ids || []) {
      api
        .getUserById(id)
        .then((u) => {
          if (!cancelled) setResolved((prev) => ({ ...prev, [id]: u }))
        })
        .catch(() => {
          /* Best-effort — falls back to showing the raw id below. */
        })
    }
    return () => {
      cancelled = true
    }
  }, [currentCase.id, currentCase.assigned_investigator_ids])

  async function handleRemove(id) {
    setRemovingId(id)
    try {
      await api.unassignInvestigator(currentCase.id, id)
      showToast('Investigator removed from this case.', 'success')
      onRemoved()
    } catch (err) {
      showToast(err.message, 'error')
    } finally {
      setRemovingId(null)
    }
  }

  const ids = currentCase.assigned_investigator_ids || []
  if (ids.length === 0) return <p className="panel-status">No investigators assigned to this case.</p>

  return (
    <ul className="pending-request-list">
      {ids.map((id) => {
        const u = resolved[id]
        return (
          <li key={id} className="pending-request-item">
            <div>
              <strong>{u ? u.name : id}</strong>
              {u && (
                <p className="pending-request-reason">
                  {u.badge_id} · {u.role.replace('_', ' ')}
                </p>
              )}
            </div>
            <button
              type="button"
              className="btn-secondary"
              disabled={removingId === id || ids.length === 1}
              title={ids.length === 1 ? "Can't remove a case's last investigator" : undefined}
              onClick={() => handleRemove(id)}
            >
              <UserMinus size={14} />
              {removingId === id ? 'Removing...' : 'Remove'}
            </button>
          </li>
        )
      })}
    </ul>
  )
}

function AssignInvestigatorPanel({ cases, refreshCases, selectedCaseId }) {
  const [badgeId, setBadgeId] = useState('')
  const [found, setFound] = useState(null)
  const [lookupError, setLookupError] = useState(null)
  const [looking, setLooking] = useState(false)
  const [assigning, setAssigning] = useState(false)
  const showToast = useToast()

  const currentCase = cases?.find((c) => c.id === selectedCaseId) || null

  async function handleLookup(e) {
    e.preventDefault()
    const trimmed = badgeId.trim()
    if (!trimmed) return
    setLooking(true)
    setLookupError(null)
    setFound(null)
    try {
      const user = await api.lookupUserByBadgeId(trimmed)
      setFound(user)
    } catch (err) {
      setLookupError(err.message)
    } finally {
      setLooking(false)
    }
  }

  async function handleAssign() {
    if (!found || !selectedCaseId) return
    setAssigning(true)
    try {
      await api.assignInvestigator(selectedCaseId, found.id)
      showToast(`${found.name} assigned to this case.`, 'success')
      setFound(null)
      setBadgeId('')
      refreshCases()
    } catch (err) {
      showToast(err.message, 'error')
    } finally {
      setAssigning(false)
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <h3>Assign Investigator</h3>
      </div>
      {!selectedCaseId || !currentCase ? (
        <p className="panel-status">Select a case in the toolbar above to manage its investigators.</p>
      ) : (
        <>
          <CurrentInvestigatorsList currentCase={currentCase} onRemoved={refreshCases} />

          <p className="panel-note" style={{ marginTop: 14 }}>
            Look up a user by badge ID, then add them as an investigator on this case. There&apos;s no user
            directory in this system yet, so this checks one badge ID at a time.
          </p>
          <form className="admin-lookup-form" onSubmit={handleLookup}>
            <label>
              Badge ID
              <input
                type="text"
                value={badgeId}
                onChange={(e) => setBadgeId(e.target.value)}
                placeholder="e.g. INV004"
                disabled={looking || assigning}
              />
            </label>
            <button type="submit" className="btn-secondary" disabled={looking || !badgeId.trim()}>
              {looking ? 'Looking up...' : 'Look up'}
            </button>
          </form>

          {lookupError && (
            <div className="notice-banner small">
              <AlertCircle size={13} />
              {lookupError}
            </div>
          )}

          {found && (
            <div className="admin-lookup-result">
              <div>
                <strong>{found.name}</strong>
                <p className="pending-request-reason">
                  {found.badge_id} · {found.role.replace('_', ' ')}
                </p>
              </div>
              <button type="button" className="btn-primary" onClick={handleAssign} disabled={assigning}>
                <UserPlus size={14} />
                {assigning ? 'Assigning...' : 'Assign to case'}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function CreateAccountPanel({ session }) {
  const [name, setName] = useState('')
  const [badgeId, setBadgeId] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState('investigator')
  const [creating, setCreating] = useState(false)
  const showToast = useToast()

  async function handleSubmit(e) {
    e.preventDefault()
    setCreating(true)
    try {
      const created = await api.createUser(name.trim(), badgeId.trim(), password, role)
      showToast(`Account created for ${created.name} (${created.badge_id}).`, 'success')
      setName('')
      setBadgeId('')
      setPassword('')
      setRole('investigator')
    } catch (err) {
      showToast(err.message, 'error')
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <h3>Create Account</h3>
      </div>
      <p className="panel-note">
        Creates an account in your own agency ({session?.agency_id}). {session?.role === 'super_admin' ? (
          <>Only investigator/analyst roles are supported from this form — use the API directly for an admin/
          super_admin account or a different agency.</>
        ) : (
          <>Admins can only create investigator or analyst accounts here, not another admin.</>
        )}
      </p>
      <form className="change-password-form" onSubmit={handleSubmit}>
        <label>
          Name
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} required disabled={creating} />
        </label>
        <label>
          Badge ID
          <input type="text" value={badgeId} onChange={(e) => setBadgeId(e.target.value)} required disabled={creating} />
        </label>
        <label>
          Temporary password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            disabled={creating}
          />
        </label>
        <label>
          Role
          <select
            className="entity-filter-select"
            value={role}
            onChange={(e) => setRole(e.target.value)}
            disabled={creating}
          >
            <option value="investigator">Investigator</option>
            <option value="analyst">Analyst</option>
          </select>
        </label>
        <button type="submit" className="btn-primary" disabled={creating}>
          {creating ? 'Creating...' : 'Create Account'}
        </button>
      </form>
    </div>
  )
}

function AuditLogPanel() {
  const [entries, setEntries] = useState(null)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('')

  const load = useCallback(() => {
    setEntries(null)
    api
      .getAuditLog({ limit: 100, action: filter || undefined })
      .then((data) => setEntries(data.entries || []))
      .catch((err) => setError(err.message))
  }, [filter])

  useEffect(() => {
    load()
  }, [load])

  return (
    <div className="panel">
      <div className="panel-header">
        <h3>Audit Log</h3>
      </div>
      <p className="panel-note">
        Logins, failed logins, account and case-access changes, ingestion, resolution overrides and report exports. Newest first.
      </p>
      <div className="audit-controls">
        <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter by action">
          <option value="">All actions</option>
          {['login', 'login_failed', 'password_changed', 'user_created', 'case_created', 'investigator_assigned',
            'investigator_unassigned', 'case_confidentiality_changed', 'case_status_changed', 'access_request_approved',
            'document_ingested', 'resolution_never_merge', 'resolution_force_merge', 'report_generated'].map((a) => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>
        <button type="button" className="btn-secondary" onClick={load}>Refresh</button>
      </div>
      {error && <p className="panel-status">{error}</p>}
      {!error && entries === null && <p className="panel-status">Loading...</p>}
      {entries && entries.length === 0 && <p className="panel-status">No audit entries.</p>}
      {entries && entries.length > 0 && (
        <div className="audit-table-wrap">
          <table className="audit-table">
            <thead>
              <tr><th>When</th><th>Who</th><th>Action</th><th>Target</th><th>From</th></tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id} className={e.success ? '' : 'audit-failed'}>
                  <td>{new Date(e.created_at).toLocaleString()}</td>
                  <td>{e.actor_badge_id || '—'}</td>
                  <td>{e.action}</td>
                  <td>{e.target_type ? `${e.target_type}: ${String(e.target_id || '').slice(0, 12)}` : '—'}</td>
                  <td>{e.ip_address || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export default function Admin({ selectedCaseId, session }) {
  const [cases, setCases] = useState(null)
  const [casesError, setCasesError] = useState(null)

  const refreshCases = useCallback(() => {
    api
      .listCases()
      .then((data) => setCases(data.cases || []))
      .catch((err) => setCasesError(err.message))
  }, [])

  useEffect(() => {
    refreshCases()
  }, [refreshCases])

  return (
    <div className="page-admin">
      <h1>Admin</h1>
      <p className="page-sub">Access-request escalations, case confidentiality, and investigator assignment.</p>

      {casesError && (
        <div className="notice-banner small">
          <AlertCircle size={13} />
          {casesError}
        </div>
      )}

      <EscalatedRequestsPanel />
      <ConfidentialityPanel cases={cases} refreshCases={refreshCases} selectedCaseId={selectedCaseId} />
      <AssignInvestigatorPanel cases={cases} refreshCases={refreshCases} selectedCaseId={selectedCaseId} />
      <CreateAccountPanel session={session} />
      <AuditLogPanel />
    </div>
  )
}
