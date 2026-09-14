# Stratum AI — product priorities (post-audit)

Grounded in implementation and `CLAUDE.md`. This file tracks durable product
priority decisions; it is not a live feature inventory.

## Full frontend redesign (in progress)

Visual system is the **Evidence Room** (Paper, Ink, Intervention): bone/paper
canvas, oxblood CTAs and seams, Instrument Serif + IBM Plex. Restraint is the
spectacle — decisions, evidence, holds, and human authority are the material.

| Phase | Status |
|-------|--------|
| Meta activation hub + required Marketing API token | **Shipped** |
| Evidence Room tokens, marketing homepage narrative, cross-examination | **Shipped** |
| Dashboard shell + Overview (Attention Ledger, Decision Under Review) | **Shipped** |
| Remaining Operate / Intelligence feature views | Planned under EvidenceAppShell |

**Rule:** new UI uses `--er-*` / Evidence Room classes. No neural networks, neon,
HUD chrome, or fabricated confidence scores. Sample data must be labeled.
Prior Pearl/Indigo Studio tokens are superseded.

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
