---
name: run-lca-research-project
description: Enter or resume the TianGong-native LCA Research Agent through its single D-18 product boundary.
---

# Run TianGong-native LCA research project

## Scope

Use this Skill only to enter or resume one TianGong-native LCA research
Project. It is a thin, stateless product adapter. It does not own workflow
selection, scientific policy, project state, tool routing, retries, or
acceptance.

The stable product contract is
`agent/lca-project-controller.md`. Dynamic state is read from the Project
Record through `ProductInterface`, never reconstructed from this file, chat
recency, file timestamps, or historical outputs.

## Mandatory entry

Every invocation must:

1. Construct `ProductInterface`, which loads
   `lca_research_kernel/contracts/agent-startup-manifest-v2.json`.
2. Require the validated `agent_startup_manifest/v1` binding before any public
   action.
3. Open or create exactly one Project Record.
4. Submit a source-bound intent or factual response through the public product
   operation.
5. Return the bounded status or accepted result exposed by the product.

Do not call a Kernel method, adapter, command-line operation, Skill, MCP tool,
model provider, or native tool as the caller-facing path. Those are internal
diagnostic or recovery ports.

## D-18 invariants

- TianGong remains first authority for official identity, version, TIDAS
  semantics, Provider relationships, LCIA methods, native tools, and native
  results.
- The Project Record is the sole accepted project truth.
- The Research Orchestrator chooses the bounded internal activity.
- The Capability Gateway is the sole tool-execution boundary.
- The Assurance Boundary authorizes effects and accepts or rejects results.
- Model, Skill, MCP, adapter, and native-tool outputs are proposals or
  `sourced_result_not_fact`, not accepted facts.
- Durable state, event idempotency, artifact registration, projection, replay,
  and recovery remain in the Kernel.

## Caller contract

The caller supplies only:

- a project identity or request to create one;
- a source-bound research intent or factual response;
- source references and local-write intent when available;
- a request to read bounded status or an accepted result.

The caller does not choose an internal capability, adapter, command, workflow,
model, route, retry, or worker topology.

## Lifecycle convergence routing

Kernel owns lifecycle observation, replay, convergence, guarded finalization,
and recovery. A Codex Goal observation is a point-in-time external fact, never
an implied live connection. `ProductInterface` invokes the accepted internal
lifecycle adapter; this Skill does not expose lifecycle command names, append a
ready-to-complete event, complete a platform Goal, or edit `CONTEXT.md`.

## Capability boundary

Internal execution must follow:

`catalog lookup -> Assurance authorization -> adapter execution -> result normalization -> Assurance recording -> Project Record`

The adopted catalog and source lock use exact identities. A missing contract,
floating version, hash drift, unsupported effect, schema drift, or permission
violation fails closed. Conditional or human-bound capabilities may be
described in the catalog without being executed.

## Responsibility boundary

The Agent may autonomously perform bounded local work, scientific and
engineering judgment, public credential-free reads, deterministic tests and
builds, replay, recovery, and reversible local checkpoints.

Human action is required for credentials or login, payment, non-standard
licenses or contracts, public release or public voice, external or shared
writes and messages, irreversible actions, main-Goal changes, and unsupported
strong public claims. At such a boundary the Agent prepares and registers a
content-addressed handoff; it does not perform the external effect.

## Statelessness

This Skill must not contain:

- project event or projection mutation logic;
- case identifiers or case-specific scientific routes;
- stored project status or manual routing labels;
- credentials, online identities, secret values, or raw sensitive output;
- low-level tool commands presented as the default user interface.

Earlier Skills and low-level commands remain available only through immutable
legacy pointers or explicit diagnostic recovery. They do not define the active
product surface.

## Stable references

- `agent/lca-project-controller.md`
- `lca_research_kernel/contracts/agent-startup-manifest-v2.json`
- `lca_research_kernel/contracts/tiangong-capability-catalog.json`
- `lca_research_kernel/contracts/tiangong-source-lock.json`
- `docs/lca-agent-capability-infrastructure.json`
