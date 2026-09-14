# Stratum AI — product priorities (post-audit)

Grounded in implementation and `CLAUDE.md`. This file tracks durable product
priority decisions; it is not a live feature inventory.

## Full frontend redesign

**Live system:** **Kinetic Signal Observatory** — cobalt / cyan / lime / coral,
Helvetica + Garamond, Trust Gate orbit, Signal Weather dashboard. Live on `/`,
auth, and `/dashboard` (shell + overview). Feature pages inherit Kinetic chrome.

Sandbox: `/studio-preview/kinetic`. Archives: Evidence (`/studio-preview/workspace`),
Luminous (`/studio-preview/luminous`). See `docs/design/README.md`.

| Phase | Status |
|-------|--------|
| Meta activation hub + required Marketing API token | **Shipped** |
| Kinetic Signal Observatory (marketing, auth, dashboard shell + overview) | **Live** |
| Remaining feature views restyled to Kinetic modules | In progress |
| Evidence Room / Luminous | Archived previews |

**Rule:** live UI uses scoped `--ks-*` inside `.ks-root`. Sample overview data
must stay labeled until wired to APIs. GA4/GTM = measurement only. No fabricated
confidence scores.

## Rules → Meta policy (decided)

**LOCAL_ONLY.** Automation Rules must not POST to the Meta Marketing API.
Pause/budget update Stratum's local `Campaign` copy only (and only when
`RULES_LOCAL_CAMPAIGN_MUTATIONS_ENABLED` is true). Live Ads Manager changes go
through Autopilot (`write_client`) when deliberately enabled.

See `docs/architecture/trust-engine.md` and `app/services/rules_meta_policy.py`.

## Related docs

- [Trust engine](architecture/trust-engine.md)
- [Integration boundaries](integrations/README.md)
- Contributor guardrails: `CLAUDE.md`
