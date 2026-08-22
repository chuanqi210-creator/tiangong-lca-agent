---
name: lca-data-gap-and-assumption-resolution
description: Use when an LCA input, mapping, parameter, source, allocation choice, scenario value, or uncertainty field is missing and must be requested, sourced, estimated transparently, or left as a conclusion-limiting gap.
---

# Resolve LCA Data Gaps and Assumptions

## Core method

Treat each gap according to ownership and scientific consequence.

1. Name the missing role, affected artifact, and conclusion it blocks.
2. Decide whether the fact is user-owned, TianGong-confirmable, externally researchable, deterministically derivable, or not recoverable.
3. Use the highest-authority permitted source; preserve original value/unit and every transformation.
4. Mark estimates and scenarios as assumptions, with range, rationale, sensitivity need, and expiry condition.
5. Stop at a bounded result when the gap would change the functional unit, system boundary, inventory, method, or claim.

## Acceptance shape

Produce `data_gap_resolution` with one disposition per missing role: resolved candidate, precise user question, explicit assumption candidate, or retained blocker.

## Common mistakes

- Asking the user for public official data the Agent can retrieve.
- Relabeling a declared scenario as TianGong official data.
- Hiding a missing value inside a default or prose estimate.
- Resolving a gap without recording its effect on conclusion strength.
