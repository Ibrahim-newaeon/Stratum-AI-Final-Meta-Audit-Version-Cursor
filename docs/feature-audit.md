# Stratum AI — product priorities (post-audit)

Grounded in implementation and `CLAUDE.md`. This file tracks durable product
priority decisions; it is not a live feature inventory.

## Full frontend redesign (in progress)

**Live shell:** Evidence Room (bone/oxblood) on marketing + dashboard.

**Proposed next system (design preview):** **Kinetic Signal Observatory** —
cobalt / cyan / lime / coral, Helvetica + Garamond only, Trust Gate orbit as the
hero, asymmetric Signal Weather dashboard. Previews:

- `/studio-preview/kinetic` (marketing)
- `/studio-preview/kinetic/dashboard` (overview)

Earlier Luminous Control preview remains at `/studio-preview/luminous` for
comparison only. See `docs/design/README.md`.

Do not migrate app-wide until Kinetic is approved. Until then:

| Phase | Status |
|-------|--------|
| Meta activation hub + required Marketing API token | **Shipped** |
| Evidence Room tokens, marketing homepage, Front Desk | **Shipped (live)** |
| Luminous Control Command Overview | Preview (superseded candidate) |
| Kinetic Signal Observatory marketing + dashboard | **Preview (preferred)** |
| App-wide migration (home, auth, all dashboard features) | After Kinetic sign-off |

**Rule:** live UI keeps `--er-*`. Kinetic uses scoped `--ks-*` inside `.ks-root`
only. Sample data must be labeled. GA4/GTM = measurement only, never ad channels.
No fabricated confidence scores.

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
