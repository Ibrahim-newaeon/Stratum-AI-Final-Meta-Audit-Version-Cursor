import { useState } from 'react'
import { LuminousAppShell } from '@/components/luminous/LuminousAppShell'

/**
 * Public design-preview host for Luminous Control.
 * Does not replace Evidence Room until Ibrahim approves.
 */
export default function LuminousPreviewLayout() {
  const [mode, setMode] = useState<'light' | 'dark'>('light')

  return (
    <LuminousAppShell
      mode={mode}
      preview
      onToggleMode={() => setMode((m) => (m === 'light' ? 'dark' : 'light'))}
    />
  )
}
