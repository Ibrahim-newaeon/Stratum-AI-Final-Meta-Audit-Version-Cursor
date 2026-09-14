# StratumAI — Design systems

## Active product UI (until Luminous approved)

**Evidence Room** — bone / oxblood, Instrument Serif + IBM Plex. Live on marketing + dashboard shell.

- Tokens: `frontend/src/theme/evidence-tokens.css`
- Preview: `/studio-preview/workspace`
- Detail: see Evidence Room section below

## Proposed — Luminous Control (awaiting approval)

Creative territory: calm command surface for Meta revenue ops. Pearl & Indigo (light) / Navy & Periwinkle (dark). Inter + IBM Plex Mono. ~10px radii.

| Role | Light | Dark |
|------|-------|------|
| Canvas | `#F6F7FB` | `#0F1424` |
| Surface | `#FFFFFF` | `#1B243B` |
| Text | `#182235` | `#F1F5FF` |
| Accent | Indigo `#4F46E5` | Periwinkle `#A5B4FC` |
| Pass | Teal | Teal |
| Hold | Amber | Amber |
| Block | Rose | Rose |

### Signature modules (keep from loved mocks)

1. **Trust-Gated Autopilot** — Signal Health → Trust Gate → Automation Decision flow
2. **CDP Identity Graph** — EMQ + identity nodes; light Gen Z “identity strata” chips scoped inside CDP only
3. **Action Queue** — gate score + status before money moves
4. **Honest KPIs** — undefined metrics show as `—`, not fake numbers

### Implementation (preview only)

- `frontend/src/theme/luminous-tokens.css`
- `frontend/src/components/luminous/*`
- `frontend/src/views/luminous/LuminousCommandOverview.tsx`
- **Preview:** `/studio-preview/luminous` (Pearl / Navy toggle in sidebar)

Do **not** migrate home, auth, or full dashboard until this design is approved. Evidence Room remains the live shell.

Reference boards: `docs/design/luminous-control-board.png`, `docs/design/luminous-overview-angled.png`

---

## Evidence Room (current)

Creative territory: every decision leaves a paper trail.

| Role | Value |
|------|-------|
| Bone (canvas) | `#F2EEE5` |
| Paper (surfaces) | `#FAF8F3` |
| Carbon (text) | `#191919` |
| Graphite (muted) | `#5E5A55` |
| Oxblood (CTA / seam) | `#762C38` |

Typography: Instrument Serif, IBM Plex Sans, IBM Plex Mono. Signature motif: horizontal incision / seam.

Preview: `/studio-preview/workspace`
