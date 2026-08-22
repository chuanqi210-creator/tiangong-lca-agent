# Contributing

Contributions should preserve the D-18 authority boundaries and solve a
case-neutral capability. Do not add a case-name route, keyword-only scientific
decision, second Project Record, default Council, thick Skill, or hidden chain
of thought.

Before submitting a change:

1. Add the smallest falsifying regression for the affected contract.
2. Preserve source location, value, unit, version, transformations, missing
   data, and validation status.
3. Run `make verify-public-release` from the generated clean tree.
4. Do not add user data, raw model traces, downloaded source bodies, credentials,
   local absolute paths, or redistribution-unclear fixtures.
5. Explain which D-18 state change the contribution enables and its bounded
   failure behavior.

Public tests may use only fully synthetic fixtures or content whose
redistribution status is explicit.
