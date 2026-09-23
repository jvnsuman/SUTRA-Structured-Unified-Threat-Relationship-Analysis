/**
 * TopBar.jsx
 *
 * Global search + notifications bell + user identity chip, shown
 * above every page. session.role drives the label shown under the
 * user's name (Investigator / Analyst / Admin / Super Admin — see
 * schema.user.Role) so the role scoping from Section 13 is always
 * visible, not just enforced silently server-side.
 *
 * notificationCount is a real count of pending access requests
 * awaiting this person's decision (App.jsx's refreshNotifications —
 * see api.getPendingAccessRequestsForMe/ForAdmin), not a hardcoded
 * placeholder — the bell only lights up when there's actually
 * something to act on.
 */

import { Bell, Search } from 'lucide-react'

function initials(name) {
  if (!name) return '?'
  return name.split(' ').map((p) => p[0]).slice(0, 2).join('').toUpperCase()
}

const ROLE_LABELS = {
  investigator: 'Investigator',
  analyst: 'Analyst',
  admin: 'Admin',
  super_admin: 'Super Admin',
}

export default function TopBar({ session, searchQuery, onSearchChange, notificationCount = 0 }) {
  return (
    <header className="topbar">
      <div className="topbar-search">
        <Search size={16} />
        <input
          type="text"
          placeholder="Search person, phone number, location, vehicle, organization..."
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
        />
      </div>

      <div className="topbar-right">
        <button
          type="button"
          className="topbar-bell"
          aria-label={notificationCount > 0 ? `Notifications (${notificationCount} pending)` : 'Notifications'}
        >
          <Bell size={18} />
          {notificationCount > 0 && <span className="topbar-bell-dot" />}
        </button>
        <div className="topbar-user">
          <span className="topbar-user-avatar">{initials(session?.name)}</span>
          <div className="topbar-user-text">
            <span className="topbar-user-name">{session?.name || 'Unknown user'}</span>
            <span className="topbar-user-role">{ROLE_LABELS[session?.role] || session?.role}</span>
          </div>
        </div>
      </div>
    </header>
  )
}
