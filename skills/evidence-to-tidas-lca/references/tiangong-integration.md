# TianGong Integration Notes

Use TianGong as the LCA substrate, not as a branding footnote.

## Reused Open-Source Substrate

- `linancn/tiangong-lca-next`: LCA data platform and private deployment path.
- `tiangong-lca/tidas-tools`: TIDAS/eILCD conversion, import, validation, export.
- `linancn/tiangong-lca-mcp`: MCP tools for flow/process/lifecycle model search, TIDAS validation, GLAD search, and openLCA-oriented guidance.
- `tiangong-lca/skills`: CLI-backed TianGong LCA workflow skills.
- `linancn/TianGong-AI-Unstructure`: document parsing direction for papers, reports, slides, and office files.

## Selection Rule

- Use MCP search when online TianGong credentials and endpoints are available.
- Use `tidas-tools` or local structural validation when service access is unavailable.
- Use the local builder only for candidate package generation and demo reproducibility.

## Attribution Rule

In proposals and demos, say:

> This project is built on the TianGong open-source LCA ecosystem. Our contribution is the research-modeling agent workflow and Evidence-to-TIDAS skill layer.
