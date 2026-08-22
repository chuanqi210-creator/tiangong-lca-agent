# TianGong-native LCA Research Agent

This repository contains the public source distribution of the D-18 LCA
research Agent. It accepts a research question and optional user-authorized
materials, selects the minimum relevant evidence, invokes only qualified model
or TianGong capability adapters, and exposes results that passed the local LCA
Domain Kernel and Assurance boundary.

## Authority and trust boundaries

- TianGong owns official dataset, method, provider, unit, and native-result
  identity when those capabilities are used.
- The Research Orchestrator coordinates work; it is not a scientific authority.
- Model, tool, Skill, MCP, and optional Council outputs are candidates.
- Assurance decides whether a candidate may enter the append-only Project
  Record, which is the only accepted project truth.
- Run Trace records execution evidence and is not a second project truth.

The public source tree intentionally excludes private Project Records, Run
Traces, browser captures, user materials, raw model traffic, provider secrets,
and redistribution-sensitive third-party documents.

## Local quickstart

Prerequisites: Python 3.11, Node.js 24.18.0, pnpm 11.13.0, and the platform
dependencies documented in `deploy/README.zh-CN.md`.

```bash
python3 scripts/bootstrap_service_runtime.py \
  --root . --receipt /tmp/d18-service-runtime-health.json
corepack enable
corepack prepare pnpm@11.13.0 --activate
pnpm --dir apps/web install --frozen-lockfile
pnpm --dir apps/web build
make verify-public-release
```

Copy `.env.example` to an untracked local environment file and replace only the
explicit placeholders. Never commit credentials. The public catalog preserves
the accepted private admission baseline through content-bound, sanitized
attestations; it does not include credentials, private responses, or a public
real-call replay. `active_verified` means accepted-source baseline admission only;
it does not claim current credentials, network authorization, connectivity,
quota, availability, or capacity.

## Architecture

The stable product contract is `docs/lca-agent-top-level-architecture.json`.
The seven D-18 responsibilities are Product Interface, Research Orchestrator,
Model Gateway, Capability Gateway, LCA Domain Kernel, Project Record, and the
cross-cutting Assurance Boundary. The repository does not introduce an eighth
framework module or make a model the sole product authority.

## Verification

`make verify-public-release` checks the clean release manifest, secret/identity
scan contract, redistribution ledger, focused backend contracts, OpenAPI
compatibility, and frontend tests/build/lint. Private real-provider Product
Readiness evidence is referenced only through a content-bound, sanitized
release manifest; public pull requests receive no provider secrets.

## Current limitations

- This source release does not include TianGong private datasets or proprietary
  provider access. Those capabilities require separate lawful configuration.
- A source-backed answer is limited by the authorized evidence actually
  resolved for that question. Missing, conflicting, or unverified evidence must
  lower the conclusion ceiling or produce a bounded result.
- Optional Council and historical case evaluations are not the default runtime
  and are not evidence that every LCA research task is solved.
- A passing component test suite alone is not a claim of production readiness;
  the private five-journey gate remains a separate, trusted release condition.

The `public-release-manifest.json` in a release binds this tree to one accepted
private source commit/tree and one exact Product Readiness receipt without
publishing private evidence bodies.
