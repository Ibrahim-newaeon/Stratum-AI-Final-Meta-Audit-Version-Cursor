# Stratum Studio visual system

Concept boards (same geometry, light + dark tokens only):

- Light — Pearl & Indigo: `stratum-studio-light-pearl-indigo.png`
- Dark — Navy & Periwinkle: `stratum-studio-dark-navy-periwinkle.png`

## Tokens

| Role | Light | Dark |
|------|-------|------|
| Background | `#F6F7FB` | `#0F1424` |
| Sidebar | `#FFFFFF` | `#131A2C` |
| Cards | `#FFFFFF` | `#1B243B` |
| Borders | `#DDE2EC` | `#33415F` |
| Primary text | `#182235` | `#F1F5FF` |
| Secondary text | `#526078` | `#B7C3DA` |
| Accent / buttons | `#4F46E5` | `#A5B4FC` |
| Active nav wash | `#EEF2FF` | `#293456` |

Accent button text: white on light, navy `#0F1424` on dark.

## Shell

- Narrow left sidebar + top search (`⌘ K`) + primary action
- Primary nav: Workspace, Campaigns, CDP, Assets, Settings
- Secondary: Meta Setup
- Theme toggle switches Pearl ↔ Navy

Implemented in:

- `frontend/src/theme/studio-tokens.css`
- `frontend/src/components/studio/StudioAppShell.tsx`
- `frontend/src/views/studio/WorkspaceOverview.tsx`
- `frontend/src/views/studio/DesignSystemPage.tsx`
