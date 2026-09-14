# Stratum AI — product priorities (post-audit)

Grounded in implementation and `CLAUDE.md`. This file tracks durable product
priority decisions; it is not a live feature inventory.

## Full frontend redesign (in progress)

Marketing site and auth are migrating to **[Radix Themes](https://www.radix-ui.com/themes)**
(`@radix-ui/themes`) as the primary component layer. Dashboard views migrate
incrementally (strangler pattern).

| Phase | Status |
|-------|--------|
| Meta activation hub (`/dashboard/activation`) + required Marketing API token | **Shipped** |
| Home, login, signup, `PageLayout` content pages | **In progress** (Radix Themes) |
| Dashboard shell + feature views | Planned — keep API hooks; restyle per module |

**Rule for agents/contributors:** new marketing and auth UI uses Radix Themes +
`MarketingShell`. Do not add ad-hoc HoloGlass inline theme objects to new pages.
Dashboard internals may still use Tailwind + Primitives until their module is migrated.

Brand: midnight `#0b1215`, teal accent `#00c7be`, gold CTA `#e2b347`. Dark appearance only.

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
