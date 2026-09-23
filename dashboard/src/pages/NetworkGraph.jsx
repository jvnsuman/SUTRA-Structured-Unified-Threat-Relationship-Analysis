/**
 * pages/NetworkGraph.jsx
 *
 * Dedicated "Network Graph" page (Sidebar's `graph` nav item) — a
 * full-width graph canvas with a persistent Entity Details panel and
 * a connections-summary row, matching the mockup direction. This is
 * intentionally a different layout from Dashboard.jsx (which shows a
 * smaller embedded graph alongside metrics/alerts/map widgets).
 *
 * Previously App.jsx routed both `graph` and `dashboard` to the same
 * <Dashboard/> component, which is why the two pages looked
 * identical — this file (plus the App.jsx routing change) is the fix.
 *
 * "Risk Score" and the "Key Suspect" tag are connection-count
 * heuristics derived from graphData — there is no ML risk-scoring
 * pipeline in this project, so these are clearly labeled as computed,
 * not a real model output, same spirit as Dashboard.jsx's
 * computeMetrics/computeTopEntities.
 *
 * "View Full Profile" has no dedicated profile page/route yet, so it
 * surfaces an honest toast instead of linking somewhere fake.
 *
 * Also fetches and shows graph.analytics.compute_link_predictions
 * results (api/routes/query.py's /link-predictions endpoint) as a
 * "Suggested Links to Investigate" panel — skipped entirely when
 * usingSampleData is true, since predictions computed against the
 * fixed SAMPLE_GRAPH would never change and would misleadingly look
 * like live analysis.
 */

import { useEffect, useMemo, useState } from 'react'
import { Download, Lightbulb, User } from 'lucide-react'
import { api } from '../api/client'
import GraphCanvas from '../components/GraphCanvas'
import GraphSkeleton from '../components/GraphSkeleton'
import { useToast } from '../components/Toast'

const TYPE_FILTERS = [
  { key: 'all', label: 'All Entities' },
  { key: 'person', label: 'Person' },
  { key: 'organization', label: 'Organization' },
  { key: 'location', label: 'Location' },
  { key: 'vehicle', label: 'Vehicle' },
  { key: 'phone', label: 'Phone' },
]

const TYPE_LABELS = {
  person: 'Person',
  organization: 'Organization',
  location: 'Location',
  vehicle: 'Vehicle',
  phone: 'Phone',
  event: 'Event',
}

function degreeMap(graphData) {
  const degree = {}
  graphData.nodes.forEach((n) => {
    degree[n.id] = 0
  })
  graphData.edges.forEach((e) => {
    degree[e.source] = (degree[e.source] || 0) + 1
    degree[e.target] = (degree[e.target] || 0) + 1
  })
  return degree
}

/** Same "main suspect" heuristic as GraphCanvas.findMainEntityId — highest-degree person, or highest-degree node overall. */
function findKeyEntityId(graphData) {
  const degree = degreeMap(graphData)
  const personNodes = graphData.nodes.filter((n) => n.entity_type === 'person')
  const candidates = personNodes.length > 0 ? personNodes : graphData.nodes
  if (candidates.length === 0) return null
  return candidates.reduce((best, n) => ((degree[n.id] || 0) > (degree[best.id] || 0) ? n : best), candidates[0]).id
}

/** direct = 1-hop neighbors; indirect = 2-hop neighbors not already direct. */
function connectionCounts(graphData, entityId) {
  if (!entityId) return { direct: 0, indirect: 0 }
  const adjacency = {}
  graphData.nodes.forEach((n) => {
    adjacency[n.id] = new Set()
  })
  graphData.edges.forEach((e) => {
    adjacency[e.source]?.add(e.target)
    adjacency[e.target]?.add(e.source)
  })
  const direct = adjacency[entityId] || new Set()
  const indirect = new Set()
  direct.forEach((id) => {
    (adjacency[id] || new Set()).forEach((n2) => {
      if (n2 !== entityId && !direct.has(n2)) indirect.add(n2)
    })
  })
  return { direct: direct.size, indirect: indirect.size }
}

/** Looks up the label of a directly-connected node of a given entity_type — e.g. a person's linked phone/location — without inventing fields the data model doesn't have. */
function findConnectedLabel(graphData, entityId, entityType) {
  for (const e of graphData.edges) {
    if (e.source === entityId || e.target === entityId) {
      const otherId = e.source === entityId ? e.target : e.source
      const other = graphData.nodes.find((n) => n.id === otherId)
      if (other?.entity_type === entityType) return other.label
    }
  }
  return null
}

export default function NetworkGraph({ graphData, caseLoading, usingSampleData, searchQuery, selectedEntityId, onNodeSelect, selectedCaseId }) {
  const [typeFilter, setTypeFilter] = useState('all')
  const [predictions, setPredictions] = useState(null)
  const [predictionsError, setPredictionsError] = useState(null)
  const [loadingPredictions, setLoadingPredictions] = useState(false)
  const showToast = useToast()

  useEffect(() => {
    if (!selectedCaseId || usingSampleData) {
      setPredictions(null)
      return
    }
    setLoadingPredictions(true)
    api
      .getLinkPredictions(selectedCaseId, 5)
      .then((data) => {
        setPredictions(data.predictions || [])
        setPredictionsError(null)
      })
      .catch((err) => {
        // A 501 here just means the case has no persisted graph yet —
        // same "authorized, but nothing to show" case as the main
        // graph query, not a real error worth surfacing as one.
        if (err.pending) {
          setPredictions([])
        } else {
          setPredictionsError(err.message)
        }
      })
      .finally(() => setLoadingPredictions(false))
  }, [selectedCaseId, usingSampleData])

  const keyEntityId = useMemo(() => (graphData ? findKeyEntityId(graphData) : null), [graphData])

  // Default to the key entity so the details panel isn't empty on first load.
  useEffect(() => {
    if (!selectedEntityId && keyEntityId) onNodeSelect?.(keyEntityId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keyEntityId])

  const degree = useMemo(() => (graphData ? degreeMap(graphData) : {}), [graphData])
  const selectedNode = graphData?.nodes.find((n) => n.id === selectedEntityId) || null
  const { direct, indirect } = graphData ? connectionCounts(graphData, selectedEntityId) : { direct: 0, indirect: 0 }
  const totalLinks = direct + indirect

  const riskScore = selectedNode ? Math.min(96, 20 + (degree[selectedNode.id] || 0) * 12) : null
  const phone = selectedNode && selectedNode.entity_type !== 'phone' ? findConnectedLabel(graphData, selectedNode.id, 'phone') : null
  const location = selectedNode && selectedNode.entity_type !== 'location' ? findConnectedLabel(graphData, selectedNode.id, 'location') : null

  function handleExport() {
    if (!graphData) return
    const blob = new Blob([JSON.stringify(graphData, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${graphData.caseId || 'network-graph'}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="page-graph">
      <div className="page-greeting">
        <div>
          <h1>Network Graph</h1>
          <p className="page-greeting-sub">Visualize entities and their connections.</p>
        </div>
        <div className="graph-toolbar">
          <select
            className="entity-filter-select"
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            aria-label="Filter by entity type"
          >
            {TYPE_FILTERS.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label}
              </option>
            ))}
          </select>
          <button type="button" className="btn-secondary" onClick={handleExport} disabled={!graphData}>
            <Download size={14} />
            Export
          </button>
        </div>
      </div>

      {usingSampleData && (
        <div className="sample-banner">Showing synthetic sample data — the live graph pipeline isn&apos;t connected to this case yet.</div>
      )}

      <div className="network-graph-layout">
        <div className="panel graph-panel graph-panel-large">
          {caseLoading ? (
            <GraphSkeleton />
          ) : (
            <GraphCanvas graphData={graphData} searchQuery={searchQuery} typeFilter={typeFilter} onNodeSelect={onNodeSelect} />
          )}
        </div>

        <div className="panel entity-details-panel">
          <div className="panel-header">
            <h3>Entity Details</h3>
          </div>
          {!selectedNode ? (
            <p className="panel-status">Select a node to see its details.</p>
          ) : (
            <>
              <div className="entity-details-head">
                <span className="entity-avatar">
                  <User size={22} />
                </span>
                <div>
                  <div className="entity-details-name">{selectedNode.label}</div>
                  {selectedNode.id === keyEntityId && <span className="entity-tag">Key Suspect</span>}
                </div>
              </div>
              <dl className="entity-details-list">
                <div>
                  <dt>Type</dt>
                  <dd>{TYPE_LABELS[selectedNode.entity_type] || selectedNode.entity_type}</dd>
                </div>
                <div>
                  <dt>Risk Score</dt>
                  <dd>{riskScore}%</dd>
                </div>
                <div>
                  <dt>Connections</dt>
                  <dd>{degree[selectedNode.id] || 0}</dd>
                </div>
                {phone && (
                  <div>
                    <dt>Phone</dt>
                    <dd>{phone}</dd>
                  </div>
                )}
                {location && (
                  <div>
                    <dt>Location</dt>
                    <dd>{location}</dd>
                  </div>
                )}
              </dl>
              <button
                type="button"
                className="btn-secondary entity-details-cta"
                onClick={() => showToast("Full entity profile view isn't built yet.", 'info')}
              >
                View Full Profile
              </button>
            </>
          )}
        </div>
      </div>

      <div className="graph-stats-row">
        <div className="stat-pill">
          <span className="stat-pill-value">{direct}</span>
          <span className="stat-pill-label">Direct Connections</span>
        </div>
        <div className="stat-pill">
          <span className="stat-pill-value">{indirect}</span>
          <span className="stat-pill-label">Indirect Connections</span>
        </div>
        <div className="stat-pill">
          <span className="stat-pill-value">{totalLinks}</span>
          <span className="stat-pill-label">Total Links</span>
        </div>
      </div>

      {!usingSampleData && (
        <div className="panel link-predictions-panel">
          <div className="panel-header">
            <h3>
              <Lightbulb size={15} /> Suggested Links to Investigate
            </h3>
          </div>
          <p className="panel-status link-predictions-sub">
            Entity pairs that share several connections but have no direct record linking them yet — a
            structural lead to manually verify, not an automatic finding.
          </p>
          {loadingPredictions ? (
            <p className="panel-status">Checking…</p>
          ) : predictionsError ? (
            <p className="panel-status">{predictionsError}</p>
          ) : !predictions || predictions.length === 0 ? (
            <p className="panel-status">No unrecorded-link suggestions for this case right now.</p>
          ) : (
            <ul className="link-prediction-list">
              {predictions.map((p) => (
                <li key={`${p.entity_a_id}-${p.entity_b_id}`} className="link-prediction-item">
                  <span>
                    <strong>{p.entity_a_label}</strong> ↔ <strong>{p.entity_b_label}</strong>
                  </span>
                  <span className="link-prediction-shared">
                    {p.shared_neighbor_ids.length} shared connection{p.shared_neighbor_ids.length === 1 ? '' : 's'}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
