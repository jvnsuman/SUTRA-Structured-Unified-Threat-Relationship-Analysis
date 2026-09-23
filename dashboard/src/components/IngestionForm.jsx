/**
 * dashboard/src/components/IngestionForm.jsx
 *
 * Submits a new source document (FIR, CDR, financial record, etc.) to
 * api/routes/ingestion.py for the currently-selected case. Disabled
 * until a case is selected, since every document needs a case_id.
 */

import { AlertCircle, Info, Upload } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api/client'
import { useToast } from './Toast'

const DOCUMENT_TYPES = ['fir', 'cdr', 'financial', 'surveillance', 'social', 'criminal_history', 'intel']

// CDR / financial records can carry structured rows. They are parsed
// directly into entities + edges (nlp/structured.py) and also feed the
// financial-structuring and communication-burst detectors.
const STRUCTURED_HINTS = {
  cdr: '{"calls": [{"caller": "9812345678", "callee": "9898989898", "timestamp": "2026-08-01T10:00:00", "duration_seconds": 42}]}',
  financial:
    '{"sender_account": "123456789012", "receiver_account": "987654321098", "transactions": [{"amount": 49000, "timestamp": "2026-08-01T10:00:00", "currency": "INR"}]}',
}

export default function IngestionForm({ caseId, onIngested }) {
  const [documentType, setDocumentType] = useState(DOCUMENT_TYPES[0])
  const [rawText, setRawText] = useState('')
  const [structuredText, setStructuredText] = useState('')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const showToast = useToast()

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    let structured
    if (STRUCTURED_HINTS[documentType] && structuredText.trim()) {
      try {
        structured = JSON.parse(structuredText)
      } catch {
        setError('Structured data is not valid JSON.')
        return
      }
    }
    setSubmitting(true)
    try {
      const result = await api.ingest({
        id: crypto.randomUUID(),
        document_type: documentType,
        raw_text: rawText,
        case_id: caseId,
        ...(structured ? { structured } : {}),
      })
      showToast(result.detail || `Document ${result.status}.`, 'success')
      setRawText('')
      setStructuredText('')
      onIngested?.(result)
    } catch (err) {
      setError(err.message)
      showToast(err.message, 'error')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form className="ingestion-form" onSubmit={handleSubmit}>
      <h3>
        <Upload size={14} />
        Ingest Document
      </h3>
      <label>
        Document type
        <select value={documentType} onChange={(e) => setDocumentType(e.target.value)}>
          {DOCUMENT_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </label>
      <label>
        Raw text
        <textarea value={rawText} onChange={(e) => setRawText(e.target.value)} rows={4} required />
      </label>
      {STRUCTURED_HINTS[documentType] && (
        <label>
          Structured records (optional JSON)
          <textarea
            value={structuredText}
            onChange={(e) => setStructuredText(e.target.value)}
            rows={4}
            placeholder={STRUCTURED_HINTS[documentType]}
            spellCheck={false}
          />
        </label>
      )}
      <button type="submit" disabled={submitting || !caseId}>
        {submitting && <span className="spinner" style={{ borderTopColor: 'white', borderColor: 'rgba(255,255,255,0.35)' }} />}
        {submitting ? 'Submitting...' : 'Ingest'}
      </button>
      {!caseId && (
        <p className="ingestion-hint">
          <Info size={13} />
          Select a case first.
        </p>
      )}
      {error && (
        <p className="ingestion-error">
          <AlertCircle size={13} />
          {error}
        </p>
      )}
    </form>
  )
}
