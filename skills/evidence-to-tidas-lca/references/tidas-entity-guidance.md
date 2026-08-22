# TIDAS Entity Guidance

This skill creates candidate entities for review. It should not imply strict schema compliance until a real TIDAS validator passes.

## Minimum MVP Entities

- `source`: citation, URL, report title, paper title, table/page reference.
- `flow`: material, energy, product, emission, waste, or service flow.
- `process`: activity node with input/output exchanges and source evidence.

## Optional Entities

- `contact`: author, data provider, lab, or institution.
- `unitGroup`: required when a unit cannot be matched to an existing group.
- `flowProperty`: mass, energy, item count, volume, area, or other measured property.
- `lifeCycleModel`: network-level model connecting process nodes.

## Mapping Heuristics

- Keep source evidence attached to every flow and process.
- Preserve original units before normalization.
- Mark guessed mappings as assumptions.
- Use TianGong/TIDAS flow or process search for canonical matching before publication.
- Put unmatched or ambiguous items into validation issues or review notes.
