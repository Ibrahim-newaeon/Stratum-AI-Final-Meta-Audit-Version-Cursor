/**
 * Marketing homepage — Kinetic Signal Observatory (approved live system)
 */

import { SEO, pageSEO } from '@/components/common/SEO'
import { KineticLiveMarketingShell } from '@/components/kinetic/KineticMarketingShell'
import KineticMarketingHome from '@/views/kinetic/KineticMarketingHome'

export default function HomePage() {
  return (
    <>
      <SEO {...pageSEO.landing} />
      <KineticLiveMarketingShell>
        <KineticMarketingHome />
      </KineticLiveMarketingShell>
    </>
  )
}
