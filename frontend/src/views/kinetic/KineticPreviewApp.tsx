import { useState } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { KineticDashShell } from '@/components/kinetic/KineticDashShell'
import { KineticMarketingShell } from '@/components/kinetic/KineticMarketingShell'
import KineticMarketingHome from '@/views/kinetic/KineticMarketingHome'
import KineticDashboardOverview, {
  KineticPlaceholder,
} from '@/views/kinetic/KineticDashboardOverview'

/**
 * Design sandbox still available at `/studio-preview/kinetic/*`
 * Live product now uses Kinetic shells on `/`, auth, and `/dashboard`.
 */
export default function KineticPreviewApp() {
  const [mode, setMode] = useState<'light' | 'dark'>('light')
  const toggle = () => setMode((m) => (m === 'light' ? 'dark' : 'light'))
  const base = '/studio-preview/kinetic'

  return (
    <Routes>
      <Route
        element={
          <KineticMarketingShell mode={mode} onToggleMode={toggle} preview />
        }
      >
        <Route index element={<KineticMarketingHome />} />
      </Route>
      <Route
        path="dashboard"
        element={
          <KineticDashShell
            mode={mode}
            onToggleMode={toggle}
            basePath={base}
            preview
          />
        }
      >
        <Route index element={<KineticDashboardOverview />} />
        <Route path="campaigns" element={<KineticPlaceholder title="Campaigns" />} />
        <Route path="trust-engine" element={<KineticPlaceholder title="Trust Engine" />} />
        <Route path="autopilot" element={<KineticPlaceholder title="Autopilot" />} />
        <Route path="cdp" element={<KineticPlaceholder title="CDP" />} />
        <Route path="measurement" element={<KineticPlaceholder title="Measurement" />} />
        <Route path="attribution" element={<KineticPlaceholder title="Attribution" />} />
        <Route path="assets" element={<KineticPlaceholder title="Assets" />} />
        <Route path="integrations" element={<KineticPlaceholder title="Integrations" />} />
        <Route path="billing" element={<KineticPlaceholder title="Billing" />} />
        <Route path="settings" element={<KineticPlaceholder title="Settings" />} />
      </Route>
      <Route path="*" element={<Navigate to="." replace />} />
    </Routes>
  )
}
