# Polibio.ai

> **Agentic AI for field research and inductive theory in the social sciences.**

Polibio.ai is an open template for building a *domain-specific* agentic research system on [Claude Code](https://claude.com/claude-code). It carries a field study through four phases — iterating hypotheses against networked data, building datasets to a study's design, preparing fieldwork, and probing findings with theory and quantitative evidence — while leaving the judgment that makes the work scholarship with the researcher.

It is not a general assistant. It is scaffolding you fill with your own field's analytical operations, sources, and canon.

*Companion paper (SSRN): forthcoming · Live demo: [terminal.italiainnovation.com](https://terminal.italiainnovation.com) (`demo` / `demo`) · An [Italia Innovation](https://italiainnovation.com) tools & methods release.*

---

## Why

General-purpose models fail field-based, inductive research in two ways. They **flatten** the field — applying every theoretical tradition with equal weight, re-deriving the analytical apparatus from scratch each time. And they leave intact the oldest constraint of empirical work: **the available data dictates the study**, so the researcher bends the question to whatever statistics happen to exist.

Polibio.ai inverts both. The field's operations live *in the system* (encoded as agents that read the field's own texts), and datasets are **built to the design**, not the design to the data — with every observation traced to the call that produced it.

## The four-phase lifecycle

| | Phase | What happens |
|---|---|---|
| **A** | Iterate hypotheses | A conjecture meets a networked evidence base (APIs + MCP) and is revised in minutes, not weeks. |
| **B** | Build datasets to the design | The study specifies variables, units, and period; the system assembles them across sources with full provenance. |
| **C** | Prepare fieldwork | Triangulate preliminary evidence with literature (your library first) and the documentary record into an operational field plan. |
| **D** | Probe findings | Apply your field's theoretical frameworks, then quantitative tests — which return a *diagnosis* (consistent / inconsistent / inconclusive / surprising), never a verdict. |

Within phase D, theory precedes the statistics. The phase order encodes an epistemology: discover and gather before interpreting. Fieldwork itself sits between C and D — it is the researcher's, not the system's.

## What's in this repo

**Disclosed (released as scaffolding):** the four analytical layers and their agents, the slash commands, the data toolkit, the provenance-logged DataPool, and the `CLAUDE.md` skeleton.

**You bring (the mutable parts):** your field's intellectual commitments, its analytical moves, its evidence sources, and its canon (the "interlocutor library"). These are the three sections of `CLAUDE.md` you rewrite.

## Quickstart

```bash
# 1. Use this template (GitHub) or clone it
git clone https://github.com/italiainnovation/polibio.git && cd polibio

# 2. Make it yours — fill the three field-specific sections of CLAUDE.md
#    (intellectual commitments · analytical moves · evidence stack)

# 3. Configure data access (no keys are committed)
cp .env.example .env          # add the keys for the sources your field uses
cp .mcp.json.example .mcp.json

# 4. (optional) the data tools run standalone, no Claude Code needed:
python3 tools/fred.py --help

# 5. Open the folder in Claude Code and direct the agents:
#    /probe-claim   /construct-datapool   /scan-jurisdictions   /plan-fieldwork
```

## Repository structure

```
polibio/
├── CLAUDE.md              # the constitution — read each session; fill the 3 field-specific sections
├── .claude/
│   ├── agents/            # Layers 1–4: discovery · interpretation · causal · design
│   └── commands/          # the slash commands for those layers
├── tools/                 # provenance-first data connectors + DataPool (run standalone too)
├── interlocutors/         # how to give agents your field's canon (mechanism + manifest; no PDFs)
├── examples/              # one fully worked example of the pattern
├── docs/                  # the four phases, adapting to your field, the architecture
├── .env.example  .mcp.json.example
└── LICENSE  CITATION.cff
```

## Adapt it to your field

The approach travels where three conditions hold (see `docs/adapting-to-your-field.md`):

1. **Decomposable operations** — your field's analysis separates into discrete moves that chain.
2. **Accessible evidence** — your sources are queryable, or at least machine-readable.
3. **A sequenceable epistemology** — there is a defensible order in which the work proceeds.

You keep the structural sections of `CLAUDE.md` (layer sequence, search hierarchy, standards) and rewrite the field-specific ones. The included example (institutional law & economics) is one instantiation of the pattern — read it as a demonstration, not the contribution.

## The data toolkit

A provenance-first set of connectors to common social-science sources — usable on their own, even without Claude Code:

FRED · Eurostat · OECD · World Bank · BLS · US Census/QCEW/BTOS · ISTAT · NOMIS/ONS · Companies House · Land Registry · Charity Commission · INE/IGE/Banco de España/BOE-BORME/Seguridad Social/Catastro · EPO patents · DART.

Everything lands in a local DuckDB **DataPool** whose schema enforces, by design: every observation traces to a logged retrieval; no imputation without a recorded method; raw values are never overwritten; source conflicts are preserved.

## Read · try · cite

- **Paper:** *Polibio.ai — Agentic AI for Field Research and Inductive Theory in the Social Sciences* (SSRN, forthcoming).
- **Live demo:** [terminal.italiainnovation.com](https://terminal.italiainnovation.com) — sign in with `demo` / `demo`.
- **Cite:** see `CITATION.cff`.

## License

[MIT](LICENSE) (proposed). The data connectors query public APIs; they do not redistribute data. Bring your own API keys.

*An Italia Innovation tools & methods release.*
