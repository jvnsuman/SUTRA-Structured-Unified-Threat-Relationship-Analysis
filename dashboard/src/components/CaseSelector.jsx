/**
 * dashboard/src/components/CaseSelector.jsx
 *
 * Dropdown of every case the logged-in user is authorized to see
 * (api/routes/cases.py's GET /cases/, already role/agency-scoped
 * server-side), plus a "New Case" button that opens a small modal
 * (title + description) and creates the case via POST /cases/,
 * then immediately selects it.
 *
 * The modal is intentionally minimal — title and description only —
 * it exists to unblock investigators who have no case to select yet,
 * not to replace a fuller "create case" form later.
 */

import { Plus, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { useToast } from './Toast'

const MAX_TITLE_LENGTH = 120
const MAX_DESCRIPTION_LENGTH = 500

function NewCaseModal({ onClose, onCreated }) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [touched, setTouched] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [serverError, setServerError] = useState(null)
  const titleInputRef = useRef(null)
  const showToast = useToast()

  useEffect(() => {
    titleInputRef.current?.focus()
  }, [])

  useEffect(() => {
    function handleKeyDown(e) {
      if (e.key === 'Escape' && !submitting) onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [submitting, onClose])

  const trimmedTitle = title.trim()
  const trimmedDescription = description.trim()
  const titleError = trimmedTitle.length === 0 ? 'Case title is required.' : null
  const descriptionError =
    trimmedDescription.length === 0 ? 'Case description is required.' : null
  const isValid = !titleError && !descriptionError

  async function handleSubmit(e) {
    e.preventDefault()
    setTouched(true)
    setServerError(null)
    if (!isValid) return

    setSubmitting(true)
    try {
      const created = await api.createCase(trimmedTitle, trimmedDescription)
      showToast(`Case "${created.title}" created.`, 'success')
      onCreated(created)
    } catch (err) {
      setServerError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  function handleBackdropClick(e) {
    if (e.target === e.currentTarget && !submitting) onClose()
  }

  return (
    <div
      className="modal-backdrop"
      onMouseDown={handleBackdropClick}
      role="presentation"
    >
      <div
        className="modal new-case-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-case-modal-title"
      >
        <div className="modal-header">
          <h2 id="new-case-modal-title">New Case</h2>
          <button
            type="button"
            className="modal-close-button"
            onClick={onClose}
            disabled={submitting}
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} noValidate>
          <div className="modal-body">
            <div className="form-field">
              <label htmlFor="new-case-title">
                Title <span className="required-marker">*</span>
              </label>
              <input
                id="new-case-title"
                ref={titleInputRef}
                type="text"
                value={title}
                maxLength={MAX_TITLE_LENGTH}
                onChange={(e) => setTitle(e.target.value)}
                onBlur={() => setTouched(true)}
                disabled={submitting}
                aria-invalid={touched && !!titleError}
                aria-describedby={touched && titleError ? 'title-error' : undefined}
                placeholder="e.g. Sector 12 trafficking network"
              />
              <div className="field-footer">
                {touched && titleError ? (
                  <span id="title-error" className="field-error">
                    {titleError}
                  </span>
                ) : (
                  <span className="field-hint">&nbsp;</span>
                )}
                <span className="char-count">
                  {title.length}/{MAX_TITLE_LENGTH}
                </span>
              </div>
            </div>

            <div className="form-field">
              <label htmlFor="new-case-description">
                Description <span className="required-marker">*</span>
              </label>
              <textarea
                id="new-case-description"
                rows={4}
                value={description}
                maxLength={MAX_DESCRIPTION_LENGTH}
                onChange={(e) => setDescription(e.target.value)}
                onBlur={() => setTouched(true)}
                disabled={submitting}
                aria-invalid={touched && !!descriptionError}
                aria-describedby={
                  touched && descriptionError ? 'description-error' : undefined
                }
                placeholder="Briefly describe the case — scope, source records, or the lead you're following up on."
              />
              <div className="field-footer">
                {touched && descriptionError ? (
                  <span id="description-error" className="field-error">
                    {descriptionError}
                  </span>
                ) : (
                  <span className="field-hint">&nbsp;</span>
                )}
                <span className="char-count">
                  {description.length}/{MAX_DESCRIPTION_LENGTH}
                </span>
              </div>
            </div>

            {serverError && <div className="modal-server-error">{serverError}</div>}
          </div>

          <div className="modal-footer">
            <button
              type="button"
              className="button-secondary"
              onClick={onClose}
              disabled={submitting}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="button-primary"
              disabled={submitting || (touched && !isValid)}
            >
              {submitting ? 'Creating…' : 'Create Case'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function CaseSelector({ selectedCaseId, onSelectCase }) {
  const [cases, setCases] = useState([])
  const [error, setError] = useState(null)
  const [modalOpen, setModalOpen] = useState(false)

  function refreshCases(selectId) {
    return api
      .listCases()
      .then((data) => {
        const list = data.cases || []
        setCases(list)
        if (selectId) {
          onSelectCase(selectId)
        } else if (!selectedCaseId && list.length > 0) {
          onSelectCase(list[0].id)
        }
        return list
      })
      .catch((err) => setError(err.message))
  }

  useEffect(() => {
    refreshCases()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleCreated(created) {
    setModalOpen(false)
    await refreshCases(created.id)
  }

  if (error) return <div className="case-selector case-selector-error">{error}</div>

  return (
    <div className="case-selector-group">
      <select
        className="case-selector"
        value={selectedCaseId || ''}
        onChange={(e) => onSelectCase(e.target.value)}
      >
        <option value="" disabled>
          {cases.length === 0 ? 'No cases yet' : 'Select a case'}
        </option>
        {cases.map((c) => (
          <option key={c.id} value={c.id}>
            {c.title}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="new-case-button"
        onClick={() => setModalOpen(true)}
        title="Create a new case"
      >
        <Plus size={15} />
        New Case
      </button>

      {modalOpen && (
        <NewCaseModal onClose={() => setModalOpen(false)} onCreated={handleCreated} />
      )}
    </div>
  )
}
