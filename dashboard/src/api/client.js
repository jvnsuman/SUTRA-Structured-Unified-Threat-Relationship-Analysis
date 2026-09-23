/**
 * dashboard/src/api/client.js
 *
 * Thin fetch wrapper around the FastAPI backend (api/main.py). Handles
 * the auth token transparently and turns HTTP errors into JS Error
 * objects components can branch on — in particular err.pending (a 501:
 * the requested pipeline step, e.g. graph.build, isn't implemented
 * yet) versus a real error.
 *
 * NOTE ON THIS FILE'S HISTORY: an earlier edit (adding createCase)
 * was made from an incomplete copy of this file and accidentally
 * dropped getAlerts/listCases/getEvidence/generateReport/
 * downloadReport/updateSettings, breaking AlertsFeed, CaseSelector,
 * EvidencePanel, Reports, and Settings. Those methods have been
 * restored below based on their call sites (Reports.jsx, Settings.jsx,
 * AlertsFeed.jsx, CaseSelector.jsx, EvidencePanel.jsx). If anything
 * still breaks with "api.xxx is not a function", it means another
 * method used somewhere wasn't caught here — grep the dashboard for
 * `api.` calls and compare against the methods below.
 */

const BASE_URL = '/api'
const TOKEN_KEY = 'cna_auth_token'

/**
 * Read the current session token, or null. Checks localStorage first
 * (a "remembered" session), then sessionStorage (a session that was
 * deliberately not remembered — see setToken below), so either kind
 * of stored session is picked up the same way.
 */
export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(TOKEN_KEY)
}

/**
 * Store (or clear, if token is falsy) the session token.
 * `remember` picks WHERE it's stored: localStorage survives closing
 * the browser, sessionStorage clears when it closes. Always clears
 * both first so switching between a remembered and a not-remembered
 * login never leaves a stale copy of the token behind in the other
 * one.
 */
function setToken(token, remember = true) {
  localStorage.removeItem(TOKEN_KEY)
  sessionStorage.removeItem(TOKEN_KEY)
  if (token) {
    (remember ? localStorage : sessionStorage).setItem(TOKEN_KEY, token)
  }
}

/**
 * Make a request to the backend, attaching the auth token if present.
 * Resolves with the parsed JSON body on success; throws an Error with
 * .status and .pending set on failure.
 */
async function request(path, { method = 'GET', body, params } = {}) {
  const headers = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let url = `${BASE_URL}${path}`
  if (params) url += `?${new URLSearchParams(params).toString()}`

  let res
  try {
    res = await fetch(url, {
      method,
      headers: body ? { ...headers, 'Content-Type': 'application/json' } : headers,
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch (networkErr) {
    const err = new Error('Could not reach the API — is the backend running?')
    err.status = 0
    err.pending = false
    throw err
  }

  let data = null
  try {
    data = await res.json()
  } catch {
    data = null
  }

  if (!res.ok) {
    const err = new Error(data?.detail || `Request failed with status ${res.status}`)
    err.status = res.status
    err.pending = res.status === 501
    throw err
  }
  return data
}

/**
 * Like request(), but for endpoints that return a raw file (e.g. a
 * PDF/CSV report) rather than JSON. Triggers a normal browser
 * "Save As" download via a temporary <a> click, instead of resolving
 * with parsed data.
 */
async function requestFileDownload(path, { method = 'GET', body, filename } = {}) {
  const headers = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let res
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      method,
      headers: body ? { ...headers, 'Content-Type': 'application/json' } : headers,
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch (networkErr) {
    const err = new Error('Could not reach the API — is the backend running?')
    err.status = 0
    err.pending = false
    throw err
  }

  if (!res.ok) {
    let detail = `Request failed with status ${res.status}`
    try {
      const data = await res.json()
      detail = data?.detail || detail
    } catch {
      /* body wasn't JSON (likely a real file stream on success path, or empty on error) */
    }
    const err = new Error(detail)
    err.status = res.status
    err.pending = res.status === 501
    throw err
  }

  const blob = await res.blob()
  const objectUrl = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename || 'download'
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(objectUrl)
}

export const api = {
  /**
   * Log in and persist the returned session token. `remember`
   * (default true) controls WHERE it's persisted — true keeps the
   * session across browser restarts (localStorage), false clears it
   * when the browser closes (sessionStorage) — see LoginForm.jsx's
   * "Remember me" checkbox.
   */
  async login(badgeId, password, remember = true) {
    const data = await request('/auth/login', {
      method: 'POST',
      body: { badge_id: badgeId, password },
    })
    setToken(data.token, remember)
    return data
  },

  /**
   * Resolve the current session token back into { name, role, id,
   * badge_id, agency_id } (see api/main.py's GET /auth/me). Used to
   * restore a real session on page load instead of a role-less
   * placeholder — see App.jsx.
   */
  me() {
    return request('/auth/me')
  },

  /**
   * Change the current user's own password (see
   * api/auth.py's change_password_endpoint). Throws with a 401 if
   * currentPassword is wrong, or a 400 if newPassword is too short.
   */
  changePassword(currentPassword, newPassword) {
    return request('/auth/change-password', {
      method: 'POST',
      body: { current_password: currentPassword, new_password: newPassword },
    })
  },

  /** Invalidate the session server-side (best-effort) and clear it locally. */
  async logout() {
    const token = getToken()
    setToken(null)
    if (token) {
      try {
        await request('/auth/logout', { method: 'POST', params: { token } })
      } catch {
        /* token already invalid or server unreachable; client-side token is cleared regardless */
      }
    }
  },

  /** Submit a source document for ingestion (see api/routes/ingestion.py). */
  ingest(document) {
    return request('/ingest/', { method: 'POST', body: document })
  },

  /**
   * Fetch per-document-type ingestion counts for a case (see
   * dashboard/src/pages/DataSources.jsx and
   * api/routes/ingestion.py's GET /{case_id}/summary, backed by
   * db.repository.get_document_summary_for_case). Returns
   * { sources: [{ document_type, count, last_updated }] }.
   */
  getDocumentSummary(caseId) {
    return request(`/ingest/${encodeURIComponent(caseId)}/summary`)
  },

  /** List every case the logged-in user is authorized to see. */
  listCases() {
    return request('/cases/')
  },

  /** Create a new case with the given title and description (see api/routes/cases.py). */
  createCase(title, description) {
    return request('/cases/', { method: 'POST', body: { title, description } })
  },

  /**
   * Fetch a case's graph (see api/routes/query.py — builds a real
   * graph from persisted entities/relationships, or throws a 501,
   * surfaced here as err.pending, if the case has no entities yet).
   */
  queryCase(caseId) {
    return request(`/query/${encodeURIComponent(caseId)}`)
  },

  /**
   * Entity-resolution review workflow (api/routes/query.py). An investigator
   * can split a wrong merge ("never_merge") or confirm a missed one
   * ("force_merge") on two entity mentions; the graph is rebuilt on the
   * next queryCase.
   */
  listResolutionOverrides(caseId) {
    return request(`/query/${encodeURIComponent(caseId)}/resolution/overrides`)
  },
  addResolutionOverride(caseId, mentionAId, mentionBId, action, note) {
    return request(`/query/${encodeURIComponent(caseId)}/resolution/overrides`, {
      method: 'POST',
      body: { mention_a_id: mentionAId, mention_b_id: mentionBId, action, note },
    })
  },
  deleteResolutionOverride(caseId, overrideId) {
    return request(
      `/query/${encodeURIComponent(caseId)}/resolution/overrides/${encodeURIComponent(overrideId)}`,
      { method: 'DELETE' },
    )
  },

  /** Admin-only audit log (api/routes/audit.py). */
  getAuditLog({ limit = 100, offset = 0, action } = {}) {
    const q = new URLSearchParams({ limit, offset })
    if (action) q.set('action', action)
    return request(`/audit/?${q.toString()}`)
  },

  /** Fetch the evidence trail for a selected entity. */
  getEvidence(entityId) {
    return request(`/evidence/${encodeURIComponent(entityId)}`)
  },

  /**
   * Fetch flagged anomalies/alerts for a case (see api/routes/alerts.py,
   * a read-only wrapper around graph.analytics.detect_anomalies).
   * Returns { alerts: [{ id, title, detail, severity, tags }] }.
   */
  getAlerts(caseId) {
    return request(`/alerts/${encodeURIComponent(caseId)}`)
  },

  /**
   * Trigger report generation for a case, in the given format
   * (e.g. "pdf", "csv"). See dashboard/src/pages/Reports.jsx and
   * api/routes/reports.py's POST /{case_id}/generate.
   */
  generateReport(caseId, format) {
    return request(`/reports/${encodeURIComponent(caseId)}/generate`, {
      method: 'POST',
      body: { format },
    })
  },

  /**
   * List previously generated reports for a case, metadata only (see
   * dashboard/src/pages/Reports.jsx and api/routes/reports.py's
   * GET /{case_id}). Returns { reports: [{ id, title, format,
   * created_at, ... }] }.
   */
  listReports(caseId) {
    return request(`/reports/${encodeURIComponent(caseId)}`)
  },

  /**
   * Download a previously-generated report file. `report` is the
   * report object as listed in Reports.jsx — must carry an `id`,
   * which is used to build the download URL per
   * api/routes/reports.py's GET /{case_id}/{report_id}/download
   * (a GET with no body, not a POST). Triggers a browser download
   * rather than resolving with JSON.
   */
  downloadReport(caseId, report) {
    return requestFileDownload(
      `/reports/${encodeURIComponent(caseId)}/${encodeURIComponent(report.id)}/download`,
      {
        method: 'GET',
        filename: report?.filename || report?.name || `report-${report?.id || 'download'}`,
      }
    )
  },

  /**
   * Fetch the current user's stored preferences (see
   * dashboard/src/pages/Settings.jsx and api/routes/settings.py's
   * GET /). Returns { preferences: {...} }.
   */
  getSettings() {
    return request('/settings/')
  },

  /**
   * Update user preferences/settings (see
   * dashboard/src/pages/Settings.jsx and api/routes/settings.py's
   * PUT /). Settings.jsx calls this with a single { [key]: value }
   * patch — settings.py's PUT handler needs to accept a partial
   * update (merge with existing) for that to behave as Settings.jsx
   * expects; confirm that's how it's implemented if this doesn't
   * behave as a patch. Returns { preferences: {...} }.
   */
  updateSettings(patch) {
    return request('/settings/', {
      method: 'PUT',
      body: patch,
    })
  },

  /**
   * Cross-case entity match panel (see api/routes/cross_case.py) —
   * for every entity in this case, matches found in cases the viewer
   * is NOT already authorized to see. Each match's other_case is
   * confidentiality-gated: a "restricted" case includes only
   * agency_name, no title/description. Returns { matches: [...] }.
   */
  getCrossCaseMatches(caseId) {
    return request(`/cases/${encodeURIComponent(caseId)}/cross-case-matches`)
  },

  /**
   * Raise an access request from a cross-case match (see
   * api/routes/cross_case.py's request-access endpoint). `caseId` and
   * `matchedEntityId` identify the match this request came from, for
   * the requester's own audit trail; `targetCaseId` is the actual
   * case being requested (other_case.case_id from getCrossCaseMatches).
   */
  requestCrossCaseAccess(caseId, matchedEntityId, targetCaseId, reason) {
    return request(
      `/cases/${encodeURIComponent(caseId)}/cross-case-matches/${encodeURIComponent(matchedEntityId)}/request-access`,
      { method: 'POST', body: { target_case_id: targetCaseId, reason } }
    )
  },

  /**
   * Access requests currently awaiting the current user's decision,
   * as an assigned investigator on the target case (see
   * api/routes/access_requests.py). Returns { requests: [...] }.
   */
  getPendingAccessRequestsForMe() {
    return request('/access-requests/pending/mine')
  },

  /**
   * Access requests escalated to admin review — ADMIN/SUPER_ADMIN
   * only (see api/routes/access_requests.py). Returns { requests: [...] }.
   */
  getPendingAccessRequestsForAdmin() {
    return request('/access-requests/pending/admin')
  },

  /** Every access request (any status) raised against a case. */
  getAccessRequestsForCase(caseId) {
    return request(`/access-requests/case/${encodeURIComponent(caseId)}`)
  },

  /**
   * Approve an access request — callable by the target case's
   * assigned investigator while pending_investigator, or by an
   * admin once escalated to pending_admin (see
   * api/routes/access_requests.py).
   */
  approveAccessRequest(requestId) {
    return request(`/access-requests/${encodeURIComponent(requestId)}/approve`, { method: 'POST' })
  },

  /**
   * Deny an access request. At pending_investigator this escalates
   * to pending_admin rather than closing the request (see
   * schema/access_request.py's lifecycle docstring); at
   * pending_admin it is final. `note` is optional context shown to
   * the requester.
   */
  denyAccessRequest(requestId, note) {
    return request(`/access-requests/${encodeURIComponent(requestId)}/deny`, {
      method: 'POST',
      body: { note },
    })
  },

  /**
   * Set a case's confidentiality tier ("normal" or "restricted") —
   * ADMIN/SUPER_ADMIN only (see api/routes/cases.py).
   */
  setCaseConfidentiality(caseId, confidentiality) {
    return request(`/cases/${encodeURIComponent(caseId)}/confidentiality`, {
      method: 'POST',
      body: { confidentiality },
    })
  },

  /**
   * Set a case's lifecycle status ("open", "under_review", or
   * "closed") — callable by an investigator assigned to the case, or
   * by an ADMIN/SUPER_ADMIN (see api/routes/cases.py's status
   * endpoint). Unlike setCaseConfidentiality, this is NOT admin-only.
   */
  setCaseStatus(caseId, status) {
    return request(`/cases/${encodeURIComponent(caseId)}/status`, {
      method: 'POST',
      body: { status },
    })
  },

  /**
   * Add an investigator to a case, by their internal user_id (NOT
   * badge_id — see lookupUserByBadgeId, which resolves one to the
   * other) — ADMIN/SUPER_ADMIN only (see api/routes/cases.py's
   * assign endpoint).
   */
  assignInvestigator(caseId, userId) {
    return request(`/cases/${encodeURIComponent(caseId)}/assign`, {
      method: 'POST',
      body: { user_id: userId },
    })
  },

  /**
   * Remove an investigator from a case, by their internal user_id —
   * ADMIN/SUPER_ADMIN only (see api/routes/cases.py's unassign
   * endpoint). Rejected by the backend if userId is the case's last
   * remaining investigator.
   */
  unassignInvestigator(caseId, userId) {
    return request(`/cases/${encodeURIComponent(caseId)}/unassign`, {
      method: 'POST',
      body: { user_id: userId },
    })
  },

  /**
   * Resolve a badge ID to the user's internal id/name/agency/role —
   * ADMIN/SUPER_ADMIN only (see api/routes/users.py). There's no
   * general user-listing endpoint in this project, so the Admin
   * page's "assign investigator" flow looks a badge ID up one at a
   * time rather than offering a dropdown of every user.
   */
  lookupUserByBadgeId(badgeId) {
    return request(`/users/lookup/${encodeURIComponent(badgeId)}`)
  },

  /**
   * Create a new account — ADMIN/SUPER_ADMIN only (see
   * api/routes/users.py's create endpoint). An ADMIN may only create
   * investigator/analyst accounts in their own agency; SUPER_ADMIN
   * may create any role in any agency (agencyId optional, defaults
   * to the caller's own).
   */
  createUser(name, badgeId, password, role, agencyId) {
    const body = { name, badge_id: badgeId, password, role }
    if (agencyId) body.agency_id = agencyId
    return request('/users/', { method: 'POST', body })
  },

  /**
   * Resolve an internal user_id back to a user's name/badge_id/role —
   * ADMIN/SUPER_ADMIN only (see api/routes/users.py). Used to show
   * readable names for a case's assigned_investigator_ids, which are
   * raw user_ids.
   */
  getUserById(userId) {
    return request(`/users/${encodeURIComponent(userId)}`)
  },

  /**
   * Walk the entire hash-chained integrity ledger and recompute every
   * entry's hash (see ledger/chain.py, api/routes/ledger.py). Returns
   * { valid, entries_checked, first_broken_sequence_number, detail }.
   */
  verifyLedger() {
    return request('/ledger/verify')
  },

  /** Every ledger entry, in order. Returns { entries: [...] }. */
  getLedgerEntries() {
    return request('/ledger/entries')
  },

  /**
   * Likely-but-unrecorded relationships within a case's own graph
   * (graph.analytics.compute_link_predictions via api/routes/query.py)
   * — a structural lead for an investigator to manually verify, not
   * an automatic edge. Returns { caseId, predictions: [...] }.
   */
  getLinkPredictions(caseId, topN = 10) {
    return request(`/query/${encodeURIComponent(caseId)}/link-predictions?top_n=${topN}`)
  },
}