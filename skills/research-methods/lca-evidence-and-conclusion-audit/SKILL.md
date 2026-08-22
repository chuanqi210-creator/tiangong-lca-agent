---
name: lca-evidence-and-conclusion-audit
description: Use when an LCA answer, report, result, recommendation, limitation, or public claim needs a final audit of source identity, calculations, units, scope, version, provenance, permissions, and conclusion strength.
---

# Audit LCA Evidence and Conclusion Boundaries

## Core method

Audit claims as a graph from statement to evidence and method.

1. Enumerate material factual, numeric, methodological, comparative, and recommendation claims.
2. Bind each claim to exact accepted facts, calculations, source spans, versions, units, and applicable domain rules.
3. Check contradictions, missing dependencies, temporal validity, permissions, and whether evidence supports the claim's modality and breadth.
4. Separate confirmed content, candidate interpretation, assumption, calculation, and limitation.
5. Reduce or reject claims whose evidence path or method acceptance is incomplete.

## Acceptance shape

Produce `evidence_conclusion_audit` with claim-level status, source paths, failure codes, corrected ceiling, and release decision candidate. The audit never repairs evidence by inventing a link.

## Common mistakes

- Citing a document that does not support the exact claim.
- Using a source hash without preserving the relevant value or span.
- Treating tool output as accepted fact before Assurance.
- Auditing style while ignoring scope, units, and scientific validity.
