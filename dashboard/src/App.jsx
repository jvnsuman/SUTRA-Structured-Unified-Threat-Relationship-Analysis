/**
 * dashboard/src/App.jsx
 *
 * New sidebar-nav shell (see SUTRA_Project_Notes.md Section 12/17
 * "sidebar-nav mockup direction"). Replaces the old dark-theme top-bar
 * layout, keeping the same real data flow: login gate, case selection,
 * api.queryCase against the real per-case graph.
 *
 * A 501 from api.queryCase (api/routes/query.py: authorized, but this
 * case has no persisted entities/relationships yet) renders as an
 * honest empty graph for that case — NOT the old SAMPLE_GRAPH demo
 * dataset. A brand-new case must never appear to already contain a
 * network; usingSampleData is kept as a prop/flag for any future,
 * deliberately-triggered demo mode, but nothing currently sets it true.
 *
 * Role enforcement (Section 13): Analyst is cross-case READ-ONLY. The
 * backend already enforces this (schema.user.Role.ANALYST has no edit
 * permission in api/routes/cases.py's assign endpoint, and analysts
 * were never given a case-creation path). This file adds the matching
 * UI-side constraint: the ingestion form (Data Sources page) and the
 * "open new case" action are hidden for analyst/investigator-only
 * actions are hidden when session.role === 'analyst', so the UI
 * doesn't dangle affordances a backend call would just reject anyway.
 * The Admin page (pages/Admin.jsx) is gated the same way, in reverse
 * (admin/super_admin only).
 *
 * Session restore: a stored token is resolved back into a real
 * session via api.me() (GET /auth/me) rather than a role-less
 * `{ restored: true }` placeholder — that placeholder used to make
 * role-gated UI (the Admin nav item, the Analyst read-only banner)
 * disappear or misbehave until the next full login.
 */

import { AlertTriangle, Share2, ShieldAlert, ShieldCheck, Users } from 'lucide-react'
import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { api, getToken } from './api/client'
import Sidebar from './components/Sidebar'
import TopBar from './components/TopBar'
import CaseSelector from './components/CaseSelector'
import CaseStatusControl from './components/CaseStatusControl'
import EvidencePanel from './components/EvidencePanel'
import ErrorBoundary from './components/ErrorBoundary'
import LoginForm from './components/LoginForm'

// Lazy-loaded: each page (and its dependencies, e.g. NetworkGraph's
// cytoscape) becomes its own chunk, fetched on first navigation to it
// instead of being bundled into the initial load. See Suspense fallback
// in renderPage() below for the loading state.
const Dashboard = lazy(() => import('./pages/Dashboard'))
const NetworkGraph = lazy(() => import('./pages/NetworkGraph'))
const SearchInvestigate = lazy(() => import('./pages/SearchInvestigate'))
const CrossCaseMatches = lazy(() => import('./pages/CrossCaseMatches'))
const LedgerIntegrity = lazy(() => import('./pages/LedgerIntegrity'))
const DataSources = lazy(() => import('./pages/DataSources'))
const Reports = lazy(() => import('./pages/Reports'))
const SettingsPage = lazy(() => import('./pages/Settings'))
const Admin = lazy(() => import('./pages/Admin'))

const READ_ONLY_ROLES = new Set(['analyst'])
const ADMIN_ROLES = new Set(['admin', 'super_admin'])

export default function App() {
  const [session, setSession] = useState(null)
  const [activePage, setActivePage] = useState('dashboard')
  const [selectedCaseId, setSelectedCaseId] = useState(null)
  const [caseData, setCaseData] = useState(null)
  const [reloadKey, setReloadKey] = useState(0) // bump to rebuild the graph after a resolution override
  const [caseLoading, setCaseLoading] = useState(false)
  const [usingSampleData, setUsingSampleData] = useState(false)
  const [loadError, setLoadError] = useState(null)
  const [selectedEntityId, setSelectedEntityId] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [notificationCount, setNotificationCount] = useState(0)

  const refreshNotifications = useCallback(() => {
    if (!session) return
    const calls = [api.getPendingAccessRequestsForMe()]
    if (ADMIN_ROLES.has(session.role)) calls.push(api.getPendingAccessRequestsForAdmin())
    Promise.all(calls)
      .then((results) => setNotificationCount(results.reduce((sum, r) => sum + (r.requests?.length || 0), 0)))
      .catch(() => {
        // Best-effort — the bell just stays at its last known count.
      })
  }, [session])

  // Refresh on login/session-restore, and again whenever the person
  // visits a page where they might resolve one of these requests, so
  // the bell doesn't stay stuck at a stale count.
  useEffect(() => {
    refreshNotifications()
  }, [refreshNotifications, activePage])

  useEffect(() => {
    if (!getToken()) return
    let cancelled = false
    api
      .me()
      .then((data) => {
        if (!cancelled) setSession(data)
      })
      .catch(() => {
        // Stored token is expired/invalid server-side — drop back to
        // the login screen rather than a half-restored session.
        if (!cancelled) api.logout()
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!session || !selectedCaseId) return
    let cancelled = false
    setCaseLoading(true)
    setCaseData(null)

    api
      .queryCase(selectedCaseId)
      .then((data) => {
        if (cancelled) return
        setCaseData(data)
        setUsingSampleData(false)
        setLoadError(null)
      })
      .catch((err) => {
        if (cancelled) return
        if (err.pending) {
          // 501 from api/routes/query.py: authorized, but this case has
          // no persisted entities/relationships yet. Show an honest
          // empty graph for *this* case rather than the SAMPLE_GRAPH
          // demo dataset — a new case must never appear to already
          // contain a network. (Cross-case entity linking — showing
          // this case's graph pulling in entities shared with other
          // cases — is a separate, not-yet-built feature; see project
          // notes / learnings for that design discussion.)
          setCaseData({ caseId: selectedCaseId, nodes: [], edges: [], stats: { entitiesLinked: 0, keyInfluencers: 0, flaggedPatterns: 0 }, influencer: null })
          setUsingSampleData(false)
          setLoadError(null)
        } else {
          setLoadError(err.message)
        }
      })
      .finally(() => {
        if (!cancelled) setCaseLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [session, selectedCaseId, reloadKey])

  if (!session) {
    return (
      <div className="login-shell">
        <div className="login-shell-hero">
          <div className="login-shell-network-bg" aria-hidden="true" />
          <div className="login-shell-hero-content">
            <div className="login-shell-brand">
              <span className="login-shell-brand-icon">
                <ShieldCheck size={22} strokeWidth={2.3} />
              </span>
              <div>
                <h1>SUTRA</h1>
                <p className="login-shell-tagline">Smarter Insights, Safer Communities.</p>
              </div>
            </div>
            <p className="login-shell-description">
              AI-powered platform to discover hidden connections, analyze criminal networks, and support law
              enforcement with <strong>intelligent data-driven insights.</strong>
            </p>
            <div className="login-shell-features">
              <div className="login-shell-feature">
                <span className="login-shell-feature-icon">
                  <Users size={18} />
                </span>
                Connect Entities
              </div>
              <div className="login-shell-feature">
                <span className="login-shell-feature-icon">
                  <Share2 size={18} />
                </span>
                Analyze Networks
              </div>
              <div className="login-shell-feature">
                <span className="login-shell-feature-icon">
                  <ShieldAlert size={18} />
                </span>
                Prevent Crime
              </div>
            </div>
          </div>
        </div>
        <LoginForm onLoggedIn={setSession} />
      </div>
    )
  }

  const isReadOnly = READ_ONLY_ROLES.has(session.role)

  function renderPage() {
    switch (activePage) {
      case 'dashboard':
        return (
          <Dashboard
            session={session}
            graphData={caseData}
            caseLoading={caseLoading}
            usingSampleData={usingSampleData}
            selectedCaseId={selectedCaseId}
            searchQuery={searchQuery}
            onNodeSelect={setSelectedEntityId}
          />
        )
      case 'graph':
        return (
          <NetworkGraph
            graphData={caseData}
            caseLoading={caseLoading}
            usingSampleData={usingSampleData}
            searchQuery={searchQuery}
            selectedEntityId={selectedEntityId}
            onNodeSelect={setSelectedEntityId}
            selectedCaseId={selectedCaseId}
          />
        )
      case 'search':
        return <SearchInvestigate graphData={caseData} onSelectEntity={setSelectedEntityId} />
      case 'cross-case':
        return <CrossCaseMatches session={session} selectedCaseId={selectedCaseId} />
      case 'ledger':
        return <LedgerIntegrity />
      case 'sources':
        return isReadOnly ? (
          <div className="page-sources">
            <h1>Data Sources</h1>
            <p className="page-sub">Your role (Analyst) has cross-case read-only access — data ingestion is restricted to Investigators and Admins.</p>
          </div>
        ) : (
          <DataSources selectedCaseId={selectedCaseId} />
        )
      case 'reports':
        return <Reports selectedCaseId={selectedCaseId} />
      case 'admin':
        return ADMIN_ROLES.has(session.role) ? (
          <Admin selectedCaseId={selectedCaseId} session={session} />
        ) : (
          <div className="page-admin">
            <h1>Admin</h1>
            <p className="page-sub">This page is restricted to Admin and Super Admin accounts.</p>
          </div>
        )
      case 'settings':
        return <SettingsPage session={session} />
      default:
        return null
    }
  }

  return (
    <div className="app-shell-v2">
      <Sidebar activePage={activePage} onNavigate={setActivePage} role={session.role} />
      <div className="app-shell-v2-main">
        <TopBar session={session} searchQuery={searchQuery} onSearchChange={setSearchQuery} notificationCount={notificationCount} />

        <div className="app-shell-v2-toolbar">
          <CaseSelector selectedCaseId={selectedCaseId} onSelectCase={setSelectedCaseId} />
          <CaseStatusControl selectedCaseId={selectedCaseId} session={session} />
          {isReadOnly && <span className="readonly-badge">Read-only access</span>}
          <button
            type="button"
            className="logout-link"
            onClick={() => {
              api.logout()
              setSession(null)
              setCaseData(null)
              setSelectedCaseId(null)
            }}
          >
            Sign out
          </button>
        </div>

        {loadError && (
          <div className="error-banner">
            <AlertTriangle size={15} />
            {loadError}
          </div>
        )}

        <main className="app-shell-v2-content">
          <ErrorBoundary resetKey={activePage} label={activePage}>
            <Suspense fallback={<div className="page-loading">Loading…</div>}>
              {renderPage()}
            </Suspense>
          </ErrorBoundary>
        </main>

        {activePage === 'dashboard' && selectedEntityId && (
          <div className="evidence-drawer">
            <EvidencePanel
              selectedEntityId={selectedEntityId}
              node={caseData?.nodes?.find((n) => n.id === selectedEntityId) || null}
              caseId={selectedCaseId}
              canEdit={session.role === 'investigator' || session.role === 'super_admin'}
              onResolutionChanged={() => setReloadKey((k) => k + 1)}
            />
          </div>
        )}
      </div>
    </div>
  )
}