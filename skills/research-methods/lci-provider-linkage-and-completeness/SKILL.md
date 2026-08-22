---
name: lci-provider-linkage-and-completeness
description: Use when technosphere exchanges, default Providers, Process connections, lifecycle-model links, reference products, or inventory completeness must be verified without inventing upstream supply relations.
---

# Verify LCI Provider Linkage and Completeness

## Core method

Audit the inventory as a typed dependency network.

1. Fix the target Process/version and enumerate its exchanges.
2. Separate elementary flows, products, wastes, inputs, outputs, and quantitative references.
3. For every technosphere input, require an exact source-bound Provider or record a typed missing relation.
4. Verify flow identity, direction, amount/unit, provider Process identity, and lifecycle-model connection separately.
5. Report covered, missing, contradictory, and non-applicable links without constructing a plausible substitute.

## Acceptance shape

Produce `provider_completeness_report` with exchange-level coverage and explicit blockers. A complete Process payload is not a complete provider network unless the official relation is present.

## Common mistakes

- Matching a Provider by name or semantic similarity.
- Storing provider links in Process notes instead of the lifecycle-model relation.
- Treating a readable exchange as a connected upstream supply.
- Filling a missing official link from model knowledge.
