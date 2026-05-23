# Adapting Polibio.ai to your field

Polibio.ai is scaffolding. Making it yours is mostly a matter of rewriting three sections of `CLAUDE.md` and pointing the agents at your field's canon and sources. This guide walks the steps and states the conditions under which the approach travels.

## Will it travel? Three conditions

The architecture works where your field satisfies all three. Check before investing:

1. **Decomposable operations.** Your analysis separates into discrete moves that chain — each with a definable input, operation, and output — without losing its force. If your analytical act is a single, holistic perception (some literary or purely ethnographic work), the agent-per-move design will not fit, and a different architecture is needed.
2. **Accessible evidence.** Your sources are queryable through APIs, or at least machine-readable. Where the decisive evidence is embodied, oral, or undigitised, the system can plan fieldwork but cannot gather the evidence.
3. **A sequenceable epistemology.** There is a defensible order in which the work proceeds (e.g. discover → interpret → test, or its principled reverse). The order need not be uncontested — but you must choose one and encode it.

Candidate fields that tend to satisfy all three: economic history, comparative constitutional law, historical sociology, political economy, organisational and management research, parts of development and demography.

## The steps

**1 · Rewrite the three field-specific sections of `CLAUDE.md`.**
- **§2 Intellectual commitments** — the traditions you draw on, and for *each* the single analytical operation it contributes. This is what stops the model from flattening: one tradition, one job.
- **§3 Analytical moves** — your repertoire. Each move: `tradition → input → operation → typed output`, plus the order moves chain.
- **§5 Evidence stack** — your sources, ranked most-proximate → last-resort, one access note each.

**2 · Keep the structural sections.** §4 (layer architecture), §6 (search hierarchy), §8 (standards) keep their shape; change only their contents. §1 frames the inquiry; §9 sets conventions.

**3 · Write your Layer-2 agents.** One agent per move, in `.claude/agents/interpretation/`. Start from the two generalized templates there, and read the worked institutional-L&E set in `examples/` as a demonstration of the pattern. Layers 1, 3, and 4 (discovery, quantitative probing, research design) are largely field-agnostic — adjust their vocabulary, but their operations carry over.

**4 · Populate the interlocutor library.** Per move, the one or two works that *teach* it (see `interlocutors/README.md`). The agents learn the method, not the conclusions.

**5 · Configure evidence access.** `cp .env.example .env` and add only the keys your sources need; `cp .mcp.json.example .mcp.json` for MCP servers. Add or remove connectors in `tools/` to match your questions.

**6 · Direct the system.** Open the folder in Claude Code and run the lifecycle: `/probe-claim`, `/construct-datapool`, `/scan-jurisdictions`, `/plan-fieldwork`. You evaluate every output — the system amplifies what you can verify; it does not exceed it.
