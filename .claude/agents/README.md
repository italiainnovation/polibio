# Agents

Polibio.ai organises agents into four analytical layers. Each agent encodes **one** intellectual operation, and the order of the layers encodes the field's epistemology (`CLAUDE.md` §4): discovery → interpretation → quantitative probing → research design. The move, not the agent, is the productive unit.

Agents are markdown specs with a consistent shape — *Identity · When to use · Input · Operation · Output · Standards*. They pass typed artifacts down the chain.

## The four layers

| Layer | Folder | Stance | In this template |
|---|---|---|---|
| **L1 · Empirical discovery** | `empirical-discovery/` | theory-agnostic | starter templates (`case-identifier`, `evidence-constructor`); extend with process-tracer, cross-case, counterfactual, datapool-builder, scanner as your study needs |
| **L2 · Interpretation** | `interpretation/` | theory-driven | **you write these** — two generalized templates provided; one agent per move (`CLAUDE.md` §3) |
| **L3 · Quantitative probing** | `causal-assessment/` | diagnostic | field-agnostic (causal-designer, quant-prober → consistent / inconsistent / inconclusive / surprising) |
| **L4 · Research design & fieldwork** | `research-design/` | operational | field-agnostic (research-designer, field-planner) |

A worked instantiation belongs in `examples/`: one filled `CLAUDE.md` (your §2–§3) plus your Layer-2 agents, one per move. Read it as a demonstration of the pattern, not as the contribution.

## Writing your own Layer-2 agents

1. Copy a template in `interpretation/`.
2. Set the tradition and the move (`CLAUDE.md` §2–§3).
3. Point it at `interlocutors/<tradition>/` so it learns the method from the field's own texts.
4. Respect the chain — the output of one move constrains the next.

Keep agents single-purpose. If an agent is doing two operations, split it.
