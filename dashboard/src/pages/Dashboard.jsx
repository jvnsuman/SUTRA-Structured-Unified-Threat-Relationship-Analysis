/**
 * pages/Dashboard.jsx
 *
 * The "Good Morning, {name}" home page from the new mockup direction.
 * All numbers/segments/rankings below are DERIVED from graphData
 * (real case data via api.queryCase, or the labeled sample fallback
 * from sampleData.js — see App.jsx's existing fallback pattern) —
 * nothing here is a hardcoded mockup figure.
 */

import { Circle } from 'lucide-react'
import GraphCanvas from '../components/GraphCanvas'
import GraphSkeleton from '../components/GraphSkeleton'
import AlertsFeed from '../components/AlertsFeed'
import MetricStrip from '../components/MetricStrip'
import NetworkOverview from '../components/NetworkOverview'
import TopEntitiesTable from '../components/TopEntitiesTable'
import MapPanel from '../components/MapPanel'

function computeMetrics(graphData) {
  if (!graphData) return []
  const counts = { entities: 0, relationships: 0, locations: 0, vehicles: 0, phones: 0 }
  counts.entities = graphData.nodes.length
  counts.relationships = graphData.edges.length
  for (const n of graphData.nodes) {
    if (n.entity_type === 'location') counts.locations++
    if (n.entity_type === 'vehicle') counts.vehicles++
    if (n.entity_type === 'phone') counts.phones++
  }
  return [
    { key: 'entities', label: 'Total Entities', value: counts.entities },
    { key: 'relationships', label: 'Total Relationships', value: counts.relationships },
    { key: 'locations', label: 'Locations Identified', value: counts.locations },
    { key: 'vehicles', label: 'Vehicles Tracked', value: counts.vehicles },
    { key: 'phones', label: 'Phone Numbers', value: counts.phones },
  ]
}

function computeOverviewSegments(graphData) {
  if (!graphData || graphData.nodes.length === 0) return { segments: [], total: 0 }
  const byType = {}
  for (const n of graphData.nodes) byType[n.entity_type] = (byType[n.entity_type] || 0) + 1
  const total = graphData.nodes.length
  const labels = { person: 'Person', organization: 'Organization', location: 'Location', vehicle: 'Vehicle', phone: 'Phone' }
  const segments = Object.entries(byType)
    .map(([key, count]) => ({ key, label: labels[key] || key, pct: Math.round((count / total) * 100) }))
    .sort((a, b) => b.pct - a.pct)
  return { segments, total: graphData.edges.length }
}

function computeTopEntities(graphData) {
  if (!graphData) return []
  const degree = {}
  for (const n of graphData.nodes) degree[n.id] = 0
  for (const e of graphData.edges) {
    degree[e.source] = (degree[e.source] || 0) + 1
    degree[e.target] = (degree[e.target] || 0) + 1
  }
  return graphData.nodes
    .map((n) => ({ ...n, connections: degree[n.id] || 0 }))
    .sort((a, b) => b.connections - a.connections)
    .slice(0, 5)
}

export default function Dashboard({ session, graphData, caseLoading, usingSampleData, selectedCaseId, searchQuery, onNodeSelect }) {
  const metrics = computeMetrics(graphData)
  const { segments, total } = computeOverviewSegments(graphData)
  const topEntities = computeTopEntities(graphData)
  const locations = graphData ? graphData.nodes.filter((n) => n.entity_type === 'location') : []

  const firstName = session?.name?.split(' ')[0] || ''
  const today = new Date().toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' })
  const time = new Date().toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })

  return (
    <div className="page-dashboard">
      <div className="page-greeting">
        <div>
          <h1>Good Morning, {firstName}</h1>
          <p className="page-greeting-sub">Here&apos;s what&apos;s happening with SUTRA today.</p>
        </div>
        <div className="page-greeting-right">
          <span className="page-greeting-date">{today} · {time}</span>
          <span className="system-status">
            <Circle size={8} fill="currentColor" />
            System Online
          </span>
        </div>
      </div>

      {usingSampleData && (
        <div className="sample-banner">Showing synthetic sample data — the live graph pipeline isn&apos;t connected to this case yet.</div>
      )}

      <MetricStrip metrics={metrics} />

      <div className="dashboard-grid-main">
        <div className="panel graph-panel">
          <div className="panel-header">
            <h3>Criminal Network Graph</h3>
            <span className="panel-header-sub">Key individuals, organizations and their connections</span>
          </div>
          {caseLoading ? (
            <GraphSkeleton />
          ) : (
            <GraphCanvas graphData={graphData} searchQuery={searchQuery} onNodeSelect={onNodeSelect} />
          )}
        </div>
        <AlertsFeed caseId={selectedCaseId} />
      </div>

      <div className="dashboard-grid-bottom">
        <NetworkOverview segments={segments} total={total} />
        <TopEntitiesTable entities={topEntities} />
        <MapPanel locations={locations} />
      </div>
    </div>
  )
}
