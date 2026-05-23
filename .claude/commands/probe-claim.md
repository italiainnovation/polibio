---
description: Probe a qualitative claim against data (causal-designer → evidence-constructor → quant-prober)
argument-hint: [the claim to probe]
---
Run the quantitative-probe pipeline for this claim:

$ARGUMENTS

1. **causal-designer** — formalise it into a testable prediction; identify the natural experiment (treatment, control, timing); specify the dataset; state *diagnostic* falsification criteria (what would be inconsistent with the mechanism, not what would "prove" it).
2. **evidence-constructor** — assemble the specified dataset into a provenance-tracked DataPool (`tools/datapool/`).
3. **quant-prober** — run the test and return a diagnosis: consistent / inconsistent / inconclusive / surprising — never a verdict.

The probe checks whether the mechanism left a visible trace; the claim rests on the process-tracing evidence. Report the design, the dataset provenance, and the diagnosis.
