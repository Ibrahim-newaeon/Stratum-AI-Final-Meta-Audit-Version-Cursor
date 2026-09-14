# Stratum AI — product priorities (post-audit)

Grounded in implementation and `CLAUDE.md`. This file tracks durable product
priority decisions; it is not a live feature inventory.

## Full frontend redesign (in progress)

**Live shell:** Evidence Room (bone/oxblood) on marketing + dashboard.

**Proposed next system (design preview only):** **Luminous Control** — Pearl &
Indigo / Navy & Periwinkle, Inter, Trust-Gated Autopilot + CDP Identity Graph
hero modules. Preview: `/studio-preview/luminous`. See `docs/design/README.md`.

Do not migrate app-wide until Luminous is approved. Until then:

| Phase | Status |
|-------|--------|
| Meta activation hub + required Marketing API token | **Shipped** |
| Evidence Room tokens, marketing homepage narrative, cross-examination | **Shipped** |
| Dashboard shell + Overview (Attention Ledger, Decision Under Review) | **Shipped** |
| Luminous Control Command Overview (design approval) | **Preview** |
| Remaining Operate / Intelligence feature views | Hold — migrate after design sign-off |

**Rule:** live UI keeps `--er-*` / Evidence Room. Luminous uses scoped `--lc-*`
inside `.lc-root` only. Sample data must be labeled. No fabricated confidence
scores. Prior Pearl/Indigo Studio tokens remain superseded by Evidence Room
until Luminous replaces both.

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
