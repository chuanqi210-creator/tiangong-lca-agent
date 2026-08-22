---
name: lcia-method-selection-and-calculation
description: Use when an accepted inventory needs a compatible LCIA method, impact category, characterization mapping, calculation run, or calculation receipt, including cases where the required method or runtime is unavailable.
---

# Select an LCIA Method and Run a Controlled Calculation

## Core method

Separate method choice from deterministic calculation.

1. Confirm intended use, inventory scope, elementary-flow identities, geography, time, and requested indicators.
2. Enumerate source-bound available LCIA methods; preserve exact method/version/category identity.
3. Justify compatibility and expose uncharacterized flows or method gaps.
4. Submit a content-addressed calculation request through the permitted TianGong/OpenLCA capability.
5. Return values, units, method identity, inventory hash, tool receipt, coverage, and limitations as a calculation candidate.

## Acceptance shape

Produce `lcia_calculation_result` only after deterministic tool completion. If method compatibility, runtime, or characterization is missing, return a bounded calculation plan; never let the model fabricate numeric LCIA results.

## Common mistakes

- Selecting a method by familiar name without checking availability and version.
- Treating uncharacterized inventory as zero impact.
- Mixing categories or units across method versions.
- Reporting a model estimate as a tool calculation.
