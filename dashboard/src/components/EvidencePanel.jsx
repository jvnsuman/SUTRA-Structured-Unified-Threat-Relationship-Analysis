/**
 * dashboard/src/components/EvidencePanel.jsx
 *
 * Right-panel evidence trail for the currently-selected graph node:
 *   1. HOW the node was resolved -- canonical name, merge confidence,
 *      the human-readable reasons, the surface forms (aliases) and the
 *      individual mentions it was merged from. A "needs review" node
 *      (merged on 0.60-0.80 evidence) is flagged, and an investigator
 *      with write access can split any mention off with one click
 *      ("Not the same person"), which is stored as an override and
 *      applied on every future graph build.
 *   2. The source documents backing the entity (api/routes/evidence.py).
 */

import { AlertCircle, AlertTriangle, Clock, FileSearch, FileText, MousePointerClick, Scissors } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api/client'

function ResolutionDetails({ node, caseId, canEdit, onResolutionChanged }) {
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const members = node.members || []
  const anchor = members[0]
  const pct = Math.round((node.mergeConfidence ?? 1) * 100)

  async function split(member) {
    if (!anchor || !window.confirm(`Treat "${member.text}" as a DIFFERENT entity from "${anchor.text}"?`)) return
    setBusy(member.mention_id)
    setError(null)
    try {
      await api.addResolutionOverride(caseId, anchor.mention_id, member.mention_id, 'never_merge', 'split from evidence panel')
      onResolutionChanged?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="resolution-details">
      <div className="resolution-head">
        <strong>{node.label}</strong>
        <span className={`resolution-conf ${node.needsReview ? 'is-review' : ''}`}>{pct}% match confidence</span>
      </div>
      {node.needsReview && (
        <p className="resolution-review">
          <AlertTriangle size={13} />
          Merged on limited evidence. Please verify before relying on it.
        </p>
      )}
      {node.aliases?.length > 0 && (
        <p className="resolution-aliases">
          Also written as: {node.aliases.join(', ')}
        </p>
      )}
      {node.mergeReasons?.length > 0 && (
        <ul className="resolution-reasons">
          {node.mergeReasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
      {members.length > 1 && (
        <details className="resolution-members">
          <summary>{members.length} mentions merged</summary>
          <ul>
            {members.map((m, i) => (
              <li key={m.mention_id}>
                <span className="resolution-member-text">&ldquo;{m.text}&rdquo;</span>
                <span className="resolution-member-doc">{m.doc_id}</span>
                {canEdit && i > 0 && (
                  <button type="button" className="btn-link" disabled={busy === m.mention_id} onClick={() => split(m)}>
                    <Scissors size={11} /> Not the same person
                  </button>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
      {error && <p className="evidence-status evidence-error"><AlertCircle size={13} /> {error}</p>}
    </div>
  )
}

export default function EvidencePanel({ selectedEntityId, node, caseId, canEdit, onResolutionChanged }) {
  const [state, setState] = useState({ status: 'idle', evidence: [], message: null })

  useEffect(() => {
    if (!selectedEntityId) {
      setState({ status: 'idle', evidence: [], message: null })
      return
    }
    let cancelled = false
    setState({ status: 'loading', evidence: [], message: null })

    api
      .getEvidence(selectedEntityId)
      .then((data) => {
        if (cancelled) return
        setState({ status: 'ready', evidence: data.evidence || [], message: null })
      })
      .catch((err) => {
        if (cancelled) return
        if (err.pending) {
          setState({ status: 'pending', evidence: [], message: 'The evidence-trail pipeline is not connected yet.' })
        } else {
          setState({ status: 'error', evidence: [], message: err.message })
        }
      })

    return () => {
      cancelled = true
    }
  }, [selectedEntityId])

  if (!selectedEntityId) {
    return (
      <div className="evidence-panel evidence-panel-empty">
        <MousePointerClick size={26} />
        Select a node to see its evidence trail.
      </div>
    )
  }

  return (
    <div className="evidence-panel">
      <h3>
        <FileSearch size={15} />
        Evidence{node ? ` — ${node.label}` : ''}
      </h3>
      {node && (
        <ResolutionDetails node={node} caseId={caseId} canEdit={canEdit} onResolutionChanged={onResolutionChanged} />
      )}
      {state.status === 'loading' && (
        <p className="evidence-status">
          <span className="spinner" />
          Loading...
        </p>
      )}
      {state.status === 'pending' && (
        <p className="evidence-status evidence-pending">
          <Clock size={14} />
          {state.message}
        </p>
      )}
      {state.status === 'error' && (
        <p className="evidence-status evidence-error">
          <AlertCircle size={14} />
          {state.message}
        </p>
      )}
      {state.status === 'ready' && state.evidence.length === 0 && (
        <p className="evidence-status">No supporting documents on file for this entity.</p>
      )}
      {state.status === 'ready' && state.evidence.length > 0 && (
        <ul className="evidence-list">
          {state.evidence.map((doc) => (
            <li key={doc.id} className="evidence-item">
              <div className="evidence-item-type">
                <FileText size={12} />
                {doc.document_type}
                <span className="evidence-item-id">{doc.id}</span>
              </div>
              <div className="evidence-item-text">{doc.raw_text}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
