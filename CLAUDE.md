# <FIELD> Research Infrastructure

<!--
  This is the constitution Claude Code reads at the start of every session.
  It is a TEMPLATE. Three sections are field-specific and you rewrite them
  wholesale: §2 Intellectual commitments, §3 Analytical moves, §5 Evidence stack.
  Four sections are structural — keep their shape, change their contents:
  §4 Layer architecture, §6 Search hierarchy, §7 Voice, §8 Standards.
  §1 frames the inquiry; §9 sets conventions.

  The approach travels where three conditions hold (see docs/adapting-to-your-field.md):
  decomposable operations, accessible evidence, a sequenceable epistemology.
  Replace every <ANGLE-BRACKET> and `TODO`. Delete these comments when done.
-->

## 1. What this studies

<!-- The question, the phenomenon, the unit of analysis, and what counts as a "case". Keep it to a paragraph. -->

TODO — e.g.: *"This program studies <PHENOMENON>. The unit of analysis is <UNIT>. A case is <DEFINITION>. The work is inductive: a case is selected because it is puzzling, documented before theory is applied, and only then interpreted."*

## 2. Intellectual commitments  ·  FIELD-SPECIFIC

<!-- The traditions your field draws on, and for EACH the single analytical operation it contributes.
     This is what keeps the agents from flattening: each tradition does one job. -->

TODO — list the traditions and, per tradition, the one operation it provides. For example:
- `<Tradition A>` — <the classification / mechanism / measurement it supplies>
- `<Tradition B>` — <…>

## 3. Analytical moves  ·  FIELD-SPECIFIC

<!-- The repertoire. Each move: tradition -> input -> operation -> typed output, plus the order moves chain.
     One agent per move (see .claude/agents/interpretation/). The move, not the agent, is the productive unit. -->

TODO — define each move in one line:
- **<Move 1>** (`<tradition>`). Input: <…>. Operation: <…>. Output: <typed result>.
- **<Move 2>** … and the order in which they chain (the output of one constrains the input of the next).

## 4. Layer architecture  —  the sequence is the epistemology

The system organises agents into four layers. Their order encodes how evidence acquires authority: discovery before interpretation, interpretation before quantitative probing, design before fieldwork. **Keep this structure; change only the agents' field content.**

| Layer | Folder | Stance | Does |
|---|---|---|---|
| **L1 · Empirical discovery** | `.claude/agents/empirical-discovery/` | theory-agnostic | finds the phenomenon, assembles evidence, scans comparators |
| **L2 · Interpretation** | `.claude/agents/interpretation/` | theory-driven | applies the analytical moves of §3 |
| **L3 · Quantitative probing** | `.claude/agents/causal-assessment/` | diagnostic | tests whether the mechanism left a trace → consistent / inconsistent / inconclusive / surprising |
| **L4 · Research design & fieldwork** | `.claude/agents/research-design/` | operational | turns analysis into a fieldwork-ready plan |

Discovery (L1) must stay theory-agnostic: the agents that find the phenomenon do not carry the vocabulary the agents that explain it (L2) apply. This is what keeps the induction honest.

## 5. Evidence stack  ·  FIELD-SPECIFIC  (most proximate → last resort)

<!-- Rank YOUR sources by the field's trust: the most granular/proximate source preferred over the aggregate.
     The included tools/ cover many social-science sources; add or remove to match your questions.
     Add one access note per source. Keys come from .env — never commit them. -->

TODO — rank your sources, e.g.:
1. **Local constructed datasets** — `tools/datapool/` (DuckDB, provenance-logged) — *always preferred once built.*
2. **<firm / micro-level source>** — <access note>
3. **<sub-national / agency source>** — <access note>
4. **<national / harmonised source>** — FRED, Eurostat, OECD, World Bank … (`tools/`)
5. **Academic literature** — your curated library FIRST, then a citation database (see §6)
6. **Web** — government documents, news, regulatory filings (last resort)

## 6. Search hierarchy

Always search the most curated source first; never use a lower layer when a higher one answers the query.

1. **Your own reference library** (curated, annotated) — a colleague, not a database
2. **Field citation corpus** (forward/backward citation chaining)
3. **Catalogue / archival discovery**
4. **Web** (non-academic sources only)

## 7. Voice  ·  constituted, not configured

<!-- Voice emerges through correction, not a static style guide. These three components govern
     your analytical prose tone; keep them as you extend the system to your field. -->

- A **style register** describing your voice (rules + examples).
- An **interlocutor library** — your field's canonical texts (see `interlocutors/README.md`). Agents learn *how* a scholar sees, not what they concluded. No copyrighted PDFs in the repo.
- A **correction repository** — side-by-side agent/your-edit pairs that accumulate into a living specification.

## 8. Standards

Every output must satisfy these before it counts:
- Every causal claim carries process-tracing evidence (pattern / sequence / trace / account).
- Every quantitative result is a **probe**, not a verdict — it checks whether a qualitative mechanism left a trace.
- Every observation in a dataset traces to a logged retrieval; no imputation without a recorded method.
- Discovery stays theory-agnostic; interpretation is where theory enters.
- Anti-functionalist checks on every cross-case pattern.
- Structural description of properties and consequences — not normative criticism.

## 9. Conventions

- **Cases:** `cases/<case-name>/` with `primary/`, `data/`, `analysis/`.
- **Outputs:** everything generated goes under `outputs/` — never outside the project.
- **DataPools:** `tools/datapool/init_pool.py <case>` builds a provenance-tracked DuckDB store.

---
*Polibio.ai · an Italia Innovation tools & methods release · template version. Companion paper: SSRN (forthcoming).*
