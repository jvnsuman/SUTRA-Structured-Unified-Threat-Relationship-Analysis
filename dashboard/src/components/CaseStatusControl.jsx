/**
 * dashboard/src/components/CaseStatusControl.jsx
 *
 * Shown in the toolbar next to CaseSelector, for whichever case is
 * currently selected. schema/case.py's CaseStatus (open/under_review/
 * closed) previously had no endpoint or UI at all — every case
 * stayed "open" forever. api/routes/cases.py's POST
 * /{case_id}/status now allows the case's own assigned investigator,
 * not just an admin, to change it (see that route's docstring).
 *
 * Fetches the case list itself (same pattern CaseSelector and
 * pages/Admin.jsx's confidentiality panel already use) rather than
 * threading case metadata down from App.jsx, since nothing else in
 * this app lifts that state up yet.
 */

import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { useToast } from './Toast'

const STATUS_OPTIONS = ['open', 'under_review', 'closed']

export default function CaseStatusControl({ selectedCaseId, session }) {
  const [cases, setCases] = useState(null)
  const [saving, setSaving] = useState(false)
  const showToast = useToast()

  const refresh = useCallback(() => {
    api
      .listCases()
      .then((data) => setCases(data.cases || []))
      .catch(() => {
        /* silent — this is a small toolbar convenience, not a page
           of its own; a load failure here shouldn't put an error
           banner in front of every page. */
      })
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  if (!selectedCaseId || !cases) return null

  const currentCase = cases.find((c) => c.id === selectedCaseId)
  if (!currentCase) return null

  const canChange =
    session?.role === 'admin' || session?.role === 'super_admin' || currentCase.assigned_investigator_ids?.includes(session?.id)

  async function handleChange(e) {
    const nextStatus = e.target.value
    if (nextStatus === currentCase.status) return
    setSaving(true)
    try {
      await api.setCaseStatus(selectedCaseId, nextStatus)
      showToast(`Case marked ${nextStatus.replace('_', ' ')}.`, 'success')
      refresh()
    } catch (err) {
      showToast(err.message, 'error')
    } finally {
      setSaving(false)
    }
  }

  if (!canChange) {
    return <span className={`case-status-pill case-status-${currentCase.status}`}>{currentCase.status.replace('_', ' ')}</span>
  }

  return (
    <select
      className={`entity-filter-select case-status-select case-status-${currentCase.status}`}
      value={currentCase.status}
      onChange={handleChange}
      disabled={saving}
      aria-label="Case status"
    >
      {STATUS_OPTIONS.map((s) => (
        <option key={s} value={s}>
          {s.replace('_', ' ')}
        </option>
      ))}
    </select>
  )
}
