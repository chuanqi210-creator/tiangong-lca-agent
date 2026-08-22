---
name: lca-goal-scope-and-functional-unit
description: Use when an LCA study lacks a fixed intended use, decision context, functional unit, reference flow, system boundary, geography, time scope, or allocation and cutoff basis.
---

# Establish LCA Goal, Scope, and Functional Unit

## Core method

Build a scope contract before inventory construction or comparison.

1. State the intended use, audience, decision, and permitted claim strength.
2. Define the function delivered, its quantified functional unit, and the reference flow that realizes it.
3. Fix product-system boundary, geography, technology, time, lifecycle stages, allocation, cutoff, and data-quality requirements.
4. Separate user-owned choices from TianGong-confirmable identities and from unresolved scientific choices.
5. Return a structured scope candidate with explicit missing roles and a conclusion ceiling.

## Acceptance shape

Produce `research_scope_contract` containing source references, units, versions, assumptions, conflicts, and completion checks. If a required choice is absent, return a bounded scope with one precise question; do not silently select a functional unit or boundary.

## Common mistakes

- Treating a mass of product as the function without explaining the service represented.
- Mixing reference flow, functional unit, declared unit, and study output unit.
- Starting inventory work while comparative equivalence or intended use is unresolved.
- Presenting a candidate scope as accepted Project Record truth.
