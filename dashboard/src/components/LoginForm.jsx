/**
 * dashboard/src/components/LoginForm.jsx
 *
 * "Welcome Back" login card matching the mockup. Labeled Email/
 * Username, but this project's accounts are keyed by badge ID (e.g.
 * INV001 — see api/auth.py), so whatever value is typed here is still
 * sent as badge_id underneath; there's no separate email-based
 * account system to switch to.
 *
 * "Remember me" is real: unchecked keeps the session in
 * sessionStorage instead of localStorage (api.login's third
 * argument — see api/client.js's setToken), so it clears when the
 * browser closes. "Forgot password?" and "Contact
 * Administrator" are real notices via useToast rather than dead
 * links — this project has no self-service reset or sign-up flow, so
 * both honestly point the user at an administrator instead of
 * pretending to do something that isn't built.
 */

import { AlertCircle, Eye, EyeOff } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api/client'
import { useToast } from './Toast'

export default function LoginForm({ onLoggedIn }) {
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [rememberMe, setRememberMe] = useState(true)
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const showToast = useToast()

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const session = await api.login(identifier, password, rememberMe)
      onLoggedIn(session)
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="login-card-wrap">
      <form className="login-form" onSubmit={handleSubmit}>
        <h2>Welcome Back</h2>
        <p className="login-form-subtitle">Sign in to access SUTRA — the criminal network analysis system.</p>

        <label>
          Email / Username
          <input
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            placeholder="Enter your email or username"
            autoFocus
            autoComplete="username"
          />
        </label>

        <label>
          Password
          <div className="login-password-field">
            <input
              type={showPassword ? 'text' : 'password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter your password"
              autoComplete="current-password"
            />
            <button
              type="button"
              className="login-password-toggle"
              onClick={() => setShowPassword((v) => !v)}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
            >
              {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </label>

        <div className="login-form-row">
          <label className="login-remember">
            <input type="checkbox" checked={rememberMe} onChange={(e) => setRememberMe(e.target.checked)} />
            Remember me
          </label>
          <button
            type="button"
            className="login-link-button"
            onClick={() => showToast('Contact your system administrator to reset your password.', 'info')}
          >
            Forgot password?
          </button>
        </div>

        {error && (
          <p className="login-error">
            <AlertCircle size={15} />
            {error}
          </p>
        )}

        <button type="submit" className="login-submit" disabled={submitting}>
          {submitting && (
            <span className="spinner" style={{ borderTopColor: 'white', borderColor: 'rgba(255,255,255,0.35)' }} />
          )}
          {submitting ? 'Signing in...' : 'Login'}
        </button>

        <p className="login-demo-hint">demo: INV001 / investigator123</p>

        <p className="login-form-footer">
          Don&apos;t have an account?{' '}
          <button
            type="button"
            className="login-link-button"
            onClick={() => showToast('New investigator accounts are provisioned by an administrator.', 'info')}
          >
            Contact Administrator
          </button>
        </p>
      </form>
    </div>
  )
}
