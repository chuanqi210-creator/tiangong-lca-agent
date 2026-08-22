---
name: evidence-to-tidas-lca
description: Use when turning LCA research evidence such as papers, lab notes, BOMs, process descriptions, spreadsheets, or TIDAS/ILCD exports into auditable TianGong/TIDAS-compatible model drafts, validation artifacts, assumptions, and report notes.
---

# Evidence To TIDAS LCA

## Overview

Use this skill to build an auditable LCA model draft from research evidence while reusing the TianGong LCA ecosystem. The skill produces candidate TIDAS entities, evidence links, assumptions, local validation artifacts, and report notes for human review before any platform write.

## Workflow

1. Freeze the research question:
   - research object
   - functional unit
   - system boundary
   - geography and time period
   - allocation rule or "not decided"
2. Collect input evidence:
   - papers, reports, lab notes, BOMs, CSV/Excel tables, process descriptions, or existing TIDAS/ILCD/openLCA exports
   - preserve source titles, citations, URLs, pages, and file paths
3. Extract candidate LCA entities:
   - flows: materials, energy, emissions, products
   - processes: operations and exchange lists
   - sources: citations and evidence records
   - optional: contacts, unit groups, flow properties, lifecycle models
4. Match against TianGong/TIDAS:
   - prefer TianGong LCA MCP flow/process/lifecycle model search when credentials are available
   - use local TIDAS/TIDAS Tools validation when online services are unavailable
   - record unmatched items as review tasks instead of silently inventing canonical data
5. Generate artifacts:
   - TIDAS candidate JSON files
   - LCI table
   - assumptions and missing-data table
   - validation report
   - short model report for the agent to continue reasoning from
6. Stop before remote writes unless the user explicitly requests publication and a TianGong publish path is configured.

## Quick Demo

Run the bundled local demo when the task needs a reproducible MVP without TianGong service credentials:

```bash
python3 skills/evidence-to-tidas-lca/scripts/build_lca_package.py \
  --input skills/evidence-to-tidas-lca/resources/demo-case.json \
  --out-dir outputs/lifepo4-demo \
  --json
```

Use the generated `validation-report.json` before presenting the model as usable. A `blocked` status means the skill created review artifacts but did not produce a trustworthy candidate package.

## Resource Routing

- Read `references/input-case-schema.md` when preparing or reviewing an input JSON case.
- Read `references/tiangong-integration.md` when deciding whether to use TianGong MCP, TIDAS Tools, or local fallback artifacts.
- Read `references/tidas-entity-guidance.md` when mapping extracted evidence into flow, process, source, and lifecycle model candidates.
- Use `templates/` to draft goal-and-scope, LCA report, and data-quality notes.
- Use `resources/demo-case.json` as the minimum competition demo fixture.

## Stop Rules

- Do not claim full TIDAS or ILCD compliance unless a real schema/tool validation passed.
- Do not publish or mutate a TianGong account without explicit user approval and configured credentials.
- Do not replace missing evidence with model guesses. Emit a blocker or assumption.
- Do not collapse source evidence into a final number without keeping citations and assumptions.
- Do not present placeholder demo values as reviewed LCA data.

## Expected Outputs

- `tidas-candidate/flows/*.json`
- `tidas-candidate/processes/*.json`
- `tidas-candidate/sources/*.json`
- `lci-table.csv`
- `assumptions.md`
- `validation-report.json`
- `report.md`

**Appropriate for:** Templates, boilerplate code, document templates, images, icons, fonts, or any files meant to be copied or used in the final output.

---

**Not every skill requires all three types of resources.**
