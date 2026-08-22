---
name: lca-unit-flow-and-dimensional-check
description: Use when LCA flows, flow properties, units, unit groups, quantities, exchange directions, reference amounts, or conversions must be checked before inventory aggregation or impact calculation.
---

# Check LCA Units, Flows, and Dimensions

## Core method

Validate identity and dimension before arithmetic.

1. Bind each amount to an exact Flow, FlowProperty, UnitGroup, Unit, direction, and source value.
2. Confirm that conversions stay within one compatible quantity dimension and use a source-bound factor.
3. Normalize only in a calculation artifact; preserve original value/unit and transformation chain.
4. Check reference-flow scaling, signs, elementary-flow compartment identity, and aggregation compatibility.
5. Reject arithmetic across incompatible properties or ambiguous flow identities.

## Acceptance shape

Produce `unit_flow_validation` with per-value pass, transformed candidate, conflict, or missing status. It may authorize a later calculation candidate; it cannot make an invalid flow comparable by renaming it.

## Common mistakes

- Converting units while ignoring the FlowProperty.
- Combining mass, energy, volume, and item quantities.
- Losing the original value after normalization.
- Treating two similarly named elementary flows as identical.
