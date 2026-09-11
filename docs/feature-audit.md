# Stratum AI — product priorities (post-audit)

Grounded in implementation and `CLAUDE.md`. This file tracks durable product
priority decisions; it is not a live feature inventory.

## Full frontend redesign (important, not ASAP)

A **full redesign of the marketing website (landing pages) and the in-app
dashboard** is an **important** product goal, but it is **explicitly not an
immediate kickoff**.

| Do now | Do later (after Meta go-live + backend gaps) |
|--------|-----------------------------------------------|
| Meta activation smoke — `docs/05-operations/meta-activation-smoke.md` | Rebuild landing + dashboard UX / IA / visual system |
| Harden LOCAL_ONLY Rules, Autopilot defaults, trust gate | Polish or deep-refactor current Campaigns / Overview / CDP screens |
| Thin API clients the **new** FE can call | Large dashboard UI features the redesign will replace |

**Rule for agents/contributors:** treat redesign as a tracked priority, not the
next sprint. Prefer durable backend and Meta activation over investing in
throwaway UI on the present frontend.

### Design system when redesign starts

Use **[Radix Themes](https://www.radix-ui.com/themes)** (`@radix-ui/themes`) as
the primary component + theme layer for the new marketing site and dashboard —
not ad-hoc restyling of the current Tailwind / Radix **Primitives** stack.

- Today the SPA already depends on Radix Primitives (unstyled).
- Themes adds the styled system (`Theme` provider, tokens for accent / gray /
  radius / scaling, layout primitives like `Flex` / `Box` / `Text`).
- Keep brand typography and atmosphere constraints from product design rules.
- Do **not** default to purple-on-white Themes demos.
- Do **not** install Themes early just to polish screens the redesign will
  replace.

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
