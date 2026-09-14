# StratumAI — Design systems

## Proposed next — Kinetic Signal Observatory (approval)

Replaces rigid Pearl/Indigo, navy/periwinkle, and beige/oxblood as the **candidate**
product visual language. Bright, asymmetric, signal-flow first. Trust Gate is the hero.

| Role | Light | Dark |
|------|-------|------|
| Canvas | `#F7FAFC` | `#08111F` |
| Surface | `#FFFFFF` | `#0D1A2D` |
| Ink | `#101828` | `#F5F8FF` |
| Cobalt | `#4054F5` | `#6E7CFF` |
| Cyan | `#00CFE8` | `#39E8FF` |
| Lime | `#D7F54A` | `#D8FF53` |
| Coral | `#FF5F6D` | `#FF7785` |

Typography: **Instagram Sans Headline** (display) · **Instagram Sans** (UI) ·
**Instagram Sans Condensed** (labels) · **Helvetica School** (fallback) ·
IBM Plex Mono (tabular metadata). Self-hosted under `frontend/public/fonts/kinetic/`.

### Signature UI

1. Living Trust Gate orbit (EXECUTE / HOLD / MANUAL REQUIRED)
2. Signal Weather + five health components
3. Campaign Pulse rows (health strips, not equal cards)
4. Evidence Stream with reasons + timestamps
5. Meta act / GA4 verify / GTM tag measurement lanes

### Preview (no auth)

- Marketing: `/studio-preview/kinetic`
- Dashboard: `/studio-preview/kinetic/dashboard`
- Day panel / Night observatory toggle in header

Tokens: `frontend/src/theme/kinetic-tokens.css`  
Components: `frontend/src/components/kinetic/`  
Views: `frontend/src/views/kinetic/`

**Do not** migrate live marketing/dashboard until Ibrahim approves Kinetic.
Evidence Room remains the live shell; Luminous remains an earlier preview only.

Craft influence (secondary): Signal/Craft graphite + orange action energy may inform
primary CTAs after approval — Kinetic remains the master system name.

---

## Live product UI (until Kinetic approved)

**Evidence Room** — bone / oxblood. Marketing + dashboard shell.

- Preview: `/studio-preview/workspace`

## Earlier proposal — Luminous Control

Pearl & Indigo / Navy & Periwinkle. Preview: `/studio-preview/luminous`.
Superseded as the preferred direction if Kinetic is approved.
