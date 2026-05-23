# Architecture — disclosed and mutable

Polibio.ai divides into a fixed scaffolding (released as-is) and a set of parts you edit to carry it to your field.

## Disclosed (the scaffolding)

- **The runtime** — Claude Code reads `CLAUDE.md` at the start of every session.
- **The agents** — four analytical layers, one intellectual operation per agent, passing typed artifacts down the chain.
- **The evidence layer** — the connectors in `tools/`, plus MCP servers, feeding a provenance-logged DuckDB **DataPool**.
- **The lifecycle** — the four-phase order, which encodes the field's epistemology.

## Mutable (what you bring)

- **`CLAUDE.md` §2, §3, §5** — your field's intellectual commitments, analytical moves, and evidence stack.
- **The Layer-2 agents** (`.claude/agents/interpretation/`) — one per move.
- **The interlocutor library** — your field's canonical texts (mechanism in `interlocutors/`).
- **The evidence sources** — add or drop connectors to match your questions.

## The analytical move is the productive unit

The pattern that may travel is not the agent or the layer but the **move**: `tradition → input → operation → typed artifact → next move`. The agent reads the tradition's own texts (not summaries), applies the operation, and emits a typed result the next move consumes. The architecture is specific to one field; the *pattern* is the contribution.

## The evidence hierarchy

Sources are ranked most-proximate → last resort: local DataPools → firm/micro-level disclosure → sub-national agencies → national/international harmonised → academic literature (curated library first) → web. The most granular, proximate source is preferred, because the phenomena social science studies operate at the meso-level and aggregates miss what analysis must see.

## Conditions for travel

Decomposable operations · accessible evidence · a sequenceable epistemology. See `docs/adapting-to-your-field.md`.
