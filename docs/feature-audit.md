# Stratum AI — product priorities (post-audit)

Grounded in implementation and `CLAUDE.md`. This file tracks durable product
priority decisions; it is not a live feature inventory.

## Full frontend redesign (in progress)

Visual system is **Stratum Studio** from the Pearl & Indigo / Navy & Periwinkle
concept boards (`docs/design/`). Same layout geometry in both themes; only color
tokens and artwork change.

| Phase | Status |
|-------|--------|
| Meta activation hub + required Marketing API token | **Shipped** |
| Studio design tokens + AppShell (light/dark) | **Shipped** |
| Workspace overview + Design system content page | **Shipped** |
| Home, login, signup, marketing PageLayout | **Shipped** (Studio tokens) |
| Remaining dashboard feature views (Campaigns, CDP, …) | Planned — strangler under StudioAppShell |

**Rule for agents/contributors:** new UI uses Studio CSS variables
(`--studio-*`) and Inter. Do not reintroduce teal HoloGlass theme objects or
the NeuralNetworkBg dashboard chrome. Toggle light/dark via ThemeProvider.

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
