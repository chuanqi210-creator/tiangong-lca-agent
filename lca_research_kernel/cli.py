"""Command-line boundary for the durable LCA research project kernel."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Optional, Sequence

from .project import ResearchProject


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _read_json_source(path: Path):
    resolved = path.resolve()
    content = resolved.read_bytes()
    return json.loads(content), {
        "source_type": "local_file",
        "path": str(resolved),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lca-research-kernel",
        description="Run and inspect a durable TianGong-native LCA research project.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("init", help="Create a research project event history.")
    initialize.add_argument("--workspace", type=Path, required=True)
    initialize.add_argument("--project-id", required=True)
    initialize.add_argument("--title", required=True)
    initialize.add_argument("--objective", required=True)

    status = commands.add_parser("status", help="Rebuild and read current projections.")
    status.add_argument("--workspace", type=Path, required=True)
    status.add_argument(
        "--format",
        choices=("human", "machine", "both"),
        default="human",
    )

    context = commands.add_parser(
        "tidas-context",
        help="Fetch and pin a native TianGong TIDAS AI context pack.",
    )
    context.add_argument("--workspace", type=Path, required=True)
    context.add_argument("--type", dest="kind", choices=sorted(ResearchProject.TIDAS_TYPES), required=True)
    context.add_argument("--revision", default="cli-0.0.25")

    plans = commands.add_parser(
        "process-plans",
        help="Compile a two-route research scope and validate native process build plans.",
    )
    plans.add_argument("--workspace", type=Path, required=True)
    plans.add_argument("--scope", type=Path, required=True)
    plans.add_argument("--revision", required=True)

    release = commands.add_parser(
        "resolve-gates",
        help="Resolve identity and performance gates, then revalidate both process plans.",
    )
    release.add_argument("--workspace", type=Path, required=True)
    release.add_argument("--scope", type=Path, required=True)
    release.add_argument("--performance-evidence", type=Path, required=True)
    release.add_argument("--search-results", type=Path, required=True)
    release.add_argument("--revision", required=True)

    materialize = commands.add_parser(
        "materialize-processes",
        help="Materialize passed process plans and run native schema/reference QA.",
    )
    materialize.add_argument("--workspace", type=Path, required=True)

    inventory = commands.add_parser(
        "inventory-plan",
        help="Compile source-bound screening exchanges and normalize their evidence.",
    )
    inventory.add_argument("--workspace", type=Path, required=True)
    inventory.add_argument("--inventory-evidence", type=Path, required=True)
    inventory.add_argument("--search-results", type=Path, required=True)

    commands.add_parser(
        "apply-inventory-scenarios",
        help="Patch screening exchanges into isolated rows and run native QA.",
    ).add_argument("--workspace", type=Path, required=True)
    identity = commands.add_parser(
        "preflight-inventory-identities",
        help="Run native flow identity preflight and record the source capability boundary.",
    )
    identity.add_argument("--workspace", type=Path, required=True)
    identity.add_argument("--remote-candidates", action="store_true")
    commands.add_parser(
        "materialize-inventory-entities",
        help="Materialize and validate local TIDAS flow/source drafts.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "integrate-inventory-references",
        help="Rewrite isolated scenarios to validated local canonical references.",
    ).add_argument("--workspace", type=Path, required=True)
    gaps = commands.add_parser(
        "rank-scientific-gaps",
        help="Rank source-bound scientific gaps and select one executable slice.",
    )
    gaps.add_argument("--workspace", type=Path, required=True)
    gaps.add_argument("--assessment", type=Path, required=True)
    commands.add_parser(
        "materialize-residual-outputs",
        help="Materialize and append the selected conservation-residual outputs.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "materialize-ton-unit-scenarios",
        help="Materialize and validate paired deterministic ton-unit scenarios.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-lineage",
        help="Compile completed-gap progress and current invalidation lineage.",
    ).add_argument("--workspace", type=Path, required=True)
    acquisition = commands.add_parser(
        "plan-evidence-acquisition",
        help="Compile lawful source routes for all remaining blocked scientific gaps.",
    )
    acquisition.add_argument("--workspace", type=Path, required=True)
    acquisition.add_argument("--assessment", type=Path, required=True)
    discovery = commands.add_parser(
        "execute-public-discovery",
        help="Execute and normalize the current manifest's public DOI routes.",
    )
    discovery.add_argument("--workspace", type=Path, required=True)
    discovery.add_argument(
        "--retry-failed",
        action="store_true",
        help="Run one new bounded attempt when the current report has failed routes.",
    )
    commands.add_parser(
        "expand-public-sources",
        help="Check official repository and research-graph sources for current DOI targets.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-public-routes",
        help="Resolve exact author and institution anchors through public routes.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "probe-record-exports",
        help="Probe exact repository records for metadata exports, OAI, and public files.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "expand-korean-sources",
        help="Govern Korean scholarly source routes and inspect exact public CRIS records.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "inspect-author-hosted-files",
        help="Classify exact-author public profiles and hosted files before attachment ingestion.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-acquisition-options",
        help="Compile typed human acquisition options and keep one internal route running.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-stoichiometric-bracket",
        help="Compile conditional elemental and stoichiometric assurance for both routes.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-claim-matrix",
        help="Compile route-scoped verified, conditional, unresolved, and prohibited claims.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-replacement-map",
        help="Map field evidence to claim replacement and exact lineage invalidation routes.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "simulate-replacement",
        help="Dry-run a synthetic evidence hash through lineage and claim invalidation.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-evidence-intake-harness",
        help="Compile and self-test fail-closed evidence candidate validation.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-candidate-ledger",
        help="Normalize current evidence observations into a policy-aware ledger.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-admission-transaction",
        help="Compile and self-test the atomic future evidence-admission boundary.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-comparison-readiness",
        help="Compile current structural, scientific, and native LCIA readiness.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "plan-native-lifecyclemodels",
        help="Plan two route-local lifecyclemodel boundaries with the pinned native CLI.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "materialize-lifecyclemodel-shells",
        help="Materialize two local lifecyclemodel shells and pass native local gates.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "build-resulting-processes",
        help="Project two local resulting Processes and pass native dry-run gates.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-model-package",
        help="Compile and native-verify the local route-model execution closure.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "replay-model-package",
        help="Replay the accepted route-model package in an isolated local tree.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "bind-portable-runtime",
        help="Bind, relocate and verify the exact native TianGong tool runtime.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-provider-network-gaps",
        help="Compile exact route input-provider requirements and network boundaries.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-provider-candidates",
        help="Resolve bounded local Provider candidates through native identity preflight.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-provider-evidence-requirements",
        help="Compile Provider Process evidence contracts and native authoring blockers.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "probe-uslci-provider-candidates",
        help="Probe official public USLCI electricity Provider candidates read-only.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-provider-normalization",
        help="Compile an unapplied USLCI-to-TianGong Provider normalization plan.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "probe-uslci-generation-closure",
        help="Read one exact USLCI generation-Provider closure layer.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "probe-uslci-unit-provider-batch",
        help="Read one bounded USLCI unit-Provider inventory batch.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "extend-uslci-unit-provider-batch-2",
        help="Read USLCI unit-Provider batch 2 with cumulative state.",
    ).add_argument("--workspace", type=Path, required=True)
    continuation = commands.add_parser(
        "extend-uslci-unit-provider-batch",
        help="Read one exact cumulative USLCI unit-Provider batch from batch 3 onward.",
    )
    continuation.add_argument("--workspace", type=Path, required=True)
    continuation.add_argument("--batch-index", type=int, required=True)
    commands.add_parser(
        "close-uslci-unit-provider-current-breadth-layer",
        help="Compile closure for the fully observed USLCI current breadth layer.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "probe-uslci-depth-4-provider-closure-batch-1",
        help="Read the exact first governed USLCI depth-4 Provider batch.",
    ).add_argument("--workspace", type=Path, required=True)
    depth_4_continuation = commands.add_parser(
        "extend-uslci-depth-4-provider-batch",
        help="Continue one exact cumulative USLCI depth-4 Provider batch.",
    )
    depth_4_continuation.add_argument("--workspace", type=Path, required=True)
    depth_4_continuation.add_argument("--batch-index", type=int, required=True)
    commands.add_parser(
        "close-uslci-depth-4-provider-layer",
        help="Compile the depth-4 closure and bounded USLCI graph handoff.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-background-inventory-integration",
        help="Compile the bounded USLCI-to-TianGong background integration plan.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-native-slice-preflight-plan",
        help="Compile bounded native preflight actions for the selected USLCI slice.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-native-slice-preflight-batch-1",
        help="Execute the exact first bounded USLCI Flow identity-preflight batch.",
    ).add_argument("--workspace", type=Path, required=True)
    for batch_index in range(2, 16):
        commands.add_parser(
            f"execute-uslci-native-slice-preflight-batch-{batch_index}",
            help=f"Execute exact planned USLCI Flow identity-preflight batch {batch_index}.",
        ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-uslci-native-reference-flow-preflight-blockers",
        help="Resolve quantitative-reference Flow identities from bound native evidence.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-native-reference-flow-authoring-plan",
        help="Compile the exact native saved plan for the two missing reference Flows.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-native-reference-flow-authoring-plan",
        help="Execute the two saved reference-Flow plans through local native gates.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "recompile-uslci-reference-flow-dependent-process-actions",
        help="Reconcile exactly the Process actions affected by staged reference Flows.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-native-slice-process-action-plan",
        help="Execute five native Process identity and report-only build-plan gates.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-native-slice-flow-identity-resolution-plan",
        help="Compile the complete bounded USLCI Flow identity-resolution work plan.",
    ).add_argument("--workspace", type=Path, required=True)
    for batch_index in range(1, 16):
        commands.add_parser(
            f"execute-uslci-flow-identity-resolution-batch-{batch_index}",
            help=f"Execute aggregate USLCI Flow identity-resolution batch {batch_index}.",
        ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-native-slice-flow-authoring-plan",
        help="Compile complete support-aware native Flow authoring batches and canaries.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-uslci-flow-authoring-canary-supports",
        help="Resolve exact TianGong classifications and support references for five Flow canaries.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-flow-authoring-canary-saved-plans",
        help="Compile five exact immutable TianGong Flow canary build plans.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-flow-authoring-canary-native-gates",
        help="Execute five immutable Flow plans through copied TianGong native gates.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-uslci-elementary-flow-native-qa-harness",
        help="Resolve typed QA applicability for the five exact Flow canaries.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-uslci-flow-authoring-bulk-batch-2-supports",
        help="Resolve exact classifications and Mass support for Flow authoring batch 2.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-uslci-flow-authoring-bulk-batch-3-supports",
        help="Continue exact classifications and Mass support into Flow authoring batch 3.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-uslci-flow-authoring-bulk-batch-4-supports",
        help="Continue exact classifications and Mass support into Flow authoring batch 4.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-uslci-flow-authoring-bulk-batch-5-supports",
        help="Continue exact classifications and Mass support into Flow authoring batch 5.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-uslci-flow-authoring-bulk-batch-6-supports",
        help="Continue exact classifications and support evidence into Flow authoring batch 6.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-uslci-flow-authoring-bulk-batch-7-supports",
        help="Continue exact classifications and support evidence into Flow authoring batch 7.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-uslci-flow-authoring-bulk-batch-8-supports",
        help="Continue exact classifications and support evidence into Flow authoring batch 8.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-uslci-flow-authoring-bulk-batch-9-supports",
        help="Continue exact classifications and support evidence into Flow authoring batch 9.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-uslci-flow-authoring-bulk-batch-10-supports",
        help="Continue exact classifications and support evidence into Flow authoring batch 10.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-uslci-flow-authoring-bulk-batch-11-supports",
        help="Continue exact classifications and support evidence into Flow authoring batch 11.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-uslci-flow-authoring-bulk-batch-12-supports",
        help="Continue exact classifications and support evidence into Flow authoring batch 12.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-uslci-flow-authoring-bulk-batch-13-supports",
        help="Continue exact classifications and support evidence into Flow authoring batch 13.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-uslci-flow-authoring-bulk-batch-14-supports",
        help="Continue exact classifications and support evidence into Flow authoring batch 14.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-uslci-flow-authoring-bulk-batch-15-supports",
        help="Continue exact classifications and support evidence into Flow authoring batch 15.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "resolve-uslci-flow-authoring-bulk-batch-16-supports",
        help="Resolve exact resource classifications and supports for the final 51 Flows.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-flow-authoring-bulk-batch-2-saved-plans",
        help="Compile 100 exact immutable TianGong Flow batch-2 build plans.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-flow-authoring-bulk-batch-3-saved-plans",
        help="Continue exact immutable TianGong Flow build plans into batch 3.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-flow-authoring-bulk-batch-4-saved-plans",
        help="Continue exact immutable TianGong Flow build plans into batch 4.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-flow-authoring-bulk-batch-5-saved-plans",
        help="Continue exact immutable TianGong Flow build plans into batch 5.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-flow-authoring-bulk-batch-6-saved-plans",
        help="Continue exact immutable TianGong Flow build plans into batch 6.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-flow-authoring-bulk-batch-7-saved-plans",
        help="Continue exact immutable TianGong Flow build plans into batch 7.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-flow-authoring-bulk-batch-8-saved-plans",
        help="Continue exact immutable TianGong Flow build plans into batch 8.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-flow-authoring-bulk-batch-9-saved-plans",
        help="Continue exact immutable TianGong Flow build plans into batch 9.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-flow-authoring-bulk-batch-10-saved-plans",
        help="Continue exact immutable TianGong Flow build plans into batch 10.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-flow-authoring-bulk-batch-11-saved-plans",
        help="Continue exact immutable TianGong Flow build plans into batch 11.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-flow-authoring-bulk-batch-12-saved-plans",
        help="Continue exact immutable TianGong Flow build plans into batch 12.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-flow-authoring-bulk-batch-13-saved-plans",
        help="Continue exact immutable TianGong Flow build plans into batch 13.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-flow-authoring-bulk-batch-14-saved-plans",
        help="Continue exact immutable TianGong Flow build plans into batch 14.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-flow-authoring-bulk-batch-15-saved-plans",
        help="Continue exact immutable TianGong Flow build plans into batch 15.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-flow-authoring-bulk-batch-16-saved-plans",
        help="Compile the final 51 exact resource Flow plans.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-flow-authoring-bulk-batch-2-native-gates",
        help="Execute 100 exact Flow plans through isolated copied-runtime native gates.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-flow-authoring-bulk-batch-3-native-gates",
        help="Continue isolated copied-runtime native gates into Flow batch 3.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-flow-authoring-bulk-batch-4-native-gates",
        help="Continue isolated copied-runtime native gates into Flow batch 4.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-flow-authoring-bulk-batch-5-native-gates",
        help="Continue isolated copied-runtime native gates into Flow batch 5.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-flow-authoring-bulk-batch-6-native-gates",
        help="Continue isolated copied-runtime native gates into Flow batch 6.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-flow-authoring-bulk-batch-7-native-gates",
        help="Continue isolated copied-runtime native gates into Flow batch 7.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-flow-authoring-bulk-batch-8-native-gates",
        help="Continue isolated copied-runtime native gates into Flow batch 8.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-flow-authoring-bulk-batch-9-native-gates",
        help="Continue isolated copied-runtime native gates into Flow batch 9.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-flow-authoring-bulk-batch-10-native-gates",
        help="Continue isolated copied-runtime native gates into Flow batch 10.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-flow-authoring-bulk-batch-11-native-gates",
        help="Continue isolated copied-runtime native gates into Flow batch 11.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-flow-authoring-bulk-batch-12-native-gates",
        help="Continue isolated copied-runtime native gates into Flow batch 12.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-flow-authoring-bulk-batch-13-native-gates",
        help="Continue isolated copied-runtime native gates into Flow batch 13.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-flow-authoring-bulk-batch-14-native-gates",
        help="Continue isolated copied-runtime native gates into Flow batch 14.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-flow-authoring-bulk-batch-15-native-gates",
        help="Execute mixed-property Flow batch 15 through copied-runtime native gates.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-flow-authoring-bulk-batch-16-native-gates",
        help="Execute the final 51 resource Flow plans through copied-runtime native gates.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "analyze-uslci-flow-authoring-corpus-closure",
        help="Analyze exact canary and batches 2-16 evidence as one finite Flow corpus.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-uslci-flow-authoring-corpus-release-plan",
        help="Compile the immutable three-lane Flow corpus release plan.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-uslci-flow-authoring-corpus-release-canary",
        help="Execute exactly three isolated local Flow release canaries and freeze corpus release.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "compile-minimum-lifepo4-comparison-graph",
        help="Compile and verify the frozen minimum LiFePO4 two-route dependency graph.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "materialize-minimum-lifepo4-comparison-model",
        help="Materialize and natively verify the exact minimum LiFePO4 comparison model.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "accept-minimum-lifepo4-comparison-model-read-only",
        help="Independently reopen and accept the minimum model in a clean read-only context.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "execute-lifepo4-comparative-lcia-sensitivity-uncertainty",
        help="Execute the accepted two-route model through GWP100, scenarios, sensitivity, and uncertainty.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "accept-lifepo4-comparative-lcia-read-only",
        help="Recompute and independently accept the comparative LCIA package in a clean context.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "assemble-lifepo4-screening-final-package",
        help="Assemble the accepted LiFePO4 graph, model, analysis, replay, risks, and projections.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "accept-lifepo4-screening-final-package-read-only",
        help="Independently rebuild and accept all final LiFePO4 screening deliverables.",
    ).add_argument("--workspace", type=Path, required=True)
    adoption = commands.add_parser(
        "adopt-tiangong-capabilities",
        help="Adopt one exact, non-secret TianGong read-only capability observation.",
    )
    adoption.add_argument("--workspace", type=Path, required=True)
    adoption.add_argument("--observation", type=Path, required=True)
    capture = commands.add_parser(
        "capture-tiangong-open-data",
        help="Capture the published Process catalog or inspect an official TIDAS export.",
    )
    capture.add_argument("--workspace", type=Path, required=True)
    capture.add_argument("--export", type=Path)
    selection = commands.add_parser(
        "select-tiangong-open-data-case",
        help="Select one materially different Open Data case through all nine gates.",
    )
    selection.add_argument("--workspace", type=Path, required=True)
    selection.add_argument("--catalog", type=Path, required=True)
    selection.add_argument("--policy", type=Path, required=True)
    binding = commands.add_parser(
        "bind-tiangong-native-case",
        help="Bind the selected case to an immutable Authority Receipt and native result.",
    )
    binding.add_argument("--workspace", type=Path, required=True)
    binding.add_argument("--case-input", type=Path, required=True)
    binding.add_argument("--result-input", type=Path, required=True)
    commands.add_parser(
        "restore-or-execute-tiangong-native-case",
        help="Restore and verify the current native case, or execute its local bind route.",
    ).add_argument("--workspace", type=Path, required=True)
    transition = commands.add_parser(
        "transition-tiangong-authority",
        help="Apply one verified TianGong authority transition input.",
    )
    transition.add_argument("--workspace", type=Path, required=True)
    transition.add_argument("--transition-input", type=Path, required=True)
    commands.add_parser(
        "assemble-tiangong-second-case-package",
        help="Assemble the TianGong authority-following second-case package.",
    ).add_argument("--workspace", type=Path, required=True)
    commands.add_parser(
        "accept-tiangong-second-case-package-read-only",
        help="Audit the final TianGong second-case package in a clean read-only context.",
    ).add_argument("--workspace", type=Path, required=True)
    director = commands.add_parser(
        "director-advance",
        help="Continuously advance one source-bound Research Director work item.",
    )
    director.add_argument("--workspace", type=Path, required=True)
    director.add_argument("--input", type=Path, required=True)
    for command, help_text in (
        ("council-compile-agenda", "Compile one source-bound Council-default agenda."),
        ("council-route", "Select the formal fast path or a bounded Decision Cell."),
        ("council-open-cell", "Open one source-bound bounded Decision Cell."),
        (
            "council-register-worker-result",
            "Register one source-bound independent worker result.",
        ),
        ("council-advance", "Classify Cell progress and validate an incumbent."),
        ("council-close", "Close a validated or bounded no-progress Cell."),
        ("council-fast-path", "Complete one deterministic formal fast-path result."),
        ("council-accept", "Accept one formal Council result into Project truth."),
        (
            "council-verify-rollback",
            "Verify the exact frozen single-Controller rollback object.",
        ),
        (
            "council-rollback",
            "Activate the verified single-Controller rollback without deleting history.",
        ),
    ):
        subparser = commands.add_parser(command, help=help_text)
        subparser.add_argument("--workspace", type=Path, required=True)
        subparser.add_argument("--input", type=Path, required=True)
    cutover = commands.add_parser(
        "council-cutover",
        help="Accept Council-default only after every exact cutover gate passes.",
    )
    cutover.add_argument("--workspace", type=Path, required=True)
    cutover.add_argument("--gate-manifest", type=Path, required=True)
    commands.add_parser(
        "council-accept-read-only",
        help="Validate an accepted formal Council state without writing Project state.",
    ).add_argument("--workspace", type=Path, required=True)
    lifecycle_bootstrap = commands.add_parser(
        "lifecycle-bootstrap",
        help="Create and bootstrap one replayable Council lifecycle control plane.",
    )
    lifecycle_bootstrap.add_argument("--workspace", type=Path, required=True)
    lifecycle_bootstrap.add_argument("--project-id", required=True)
    lifecycle_bootstrap.add_argument("--title", required=True)
    lifecycle_bootstrap.add_argument("--objective", required=True)
    lifecycle_bootstrap.add_argument("--observation", type=Path, required=True)
    lifecycle_bootstrap.add_argument("--phase", type=Path, required=True)
    lifecycle_observe = commands.add_parser(
        "lifecycle-observe-goal",
        help="Import one point-in-time Codex Goal observation.",
    )
    lifecycle_observe.add_argument("--workspace", type=Path, required=True)
    lifecycle_observe.add_argument("--observation", type=Path, required=True)
    lifecycle_external_observe = commands.add_parser(
        "lifecycle-observe-external-goal",
        help="Record one unreconciled cross-thread Codex Goal observation.",
    )
    lifecycle_external_observe.add_argument("--workspace", type=Path, required=True)
    lifecycle_external_observe.add_argument("--observation", type=Path, required=True)
    lifecycle_phase = commands.add_parser(
        "lifecycle-start-phase",
        help="Start one phase bound to the latest exact Goal observation.",
    )
    lifecycle_phase.add_argument("--workspace", type=Path, required=True)
    lifecycle_phase.add_argument("--phase", type=Path, required=True)
    commands.add_parser(
        "lifecycle-status",
        help="Rebuild and print the current lifecycle projection.",
    ).add_argument("--workspace", type=Path, required=True)
    lifecycle_finalize = commands.add_parser(
        "lifecycle-finalize",
        help="Run bounded gates and prepare one replayable ready-to-complete state.",
    )
    lifecycle_finalize.add_argument("--root", type=Path, required=True)
    lifecycle_finalize.add_argument("--workspace", type=Path, required=True)
    lifecycle_finalize.add_argument("--request", type=Path, required=True)
    lifecycle_verify = commands.add_parser(
        "lifecycle-verify-convergence",
        help="Read-only verify event, Goal observation and derived control state.",
    )
    lifecycle_verify.add_argument("--root", type=Path, required=True)
    lifecycle_verify.add_argument("--workspace", type=Path, required=True)
    qualification = commands.add_parser(
        "qualify-research-council-benchmark",
        help="Qualify the isolated TianGong Research Council benchmark.",
    )
    qualification.add_argument("--workspace", type=Path, required=True)
    qualification.add_argument("--observation", type=Path, required=True)
    qualification.add_argument("--policy", type=Path, required=True)
    for command, help_text in (
        ("freeze-research-council-experiment", "Freeze one qualified Council experiment."),
        ("advance-research-council-cell", "Advance one isolated Decision Cell."),
        ("register-research-council-trial", "Register one isolated paired-run trace."),
        ("evaluate-research-council", "Evaluate paired Council experiment evidence."),
    ):
        subparser = commands.add_parser(command, help=help_text)
        subparser.add_argument("--workspace", type=Path, required=True)
        subparser.add_argument("--input", type=Path, required=True)
    commands.add_parser(
        "accept-research-council-read-only",
        help="Accept the Research Council experiment from a read-only replay.",
    ).add_argument("--workspace", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "init":
        project = ResearchProject.create(
            args.workspace,
            project_id=args.project_id,
            title=args.title,
            objective=args.objective,
        )
        _print_json(project.materialize_views())
        return 0

    if args.command == "lifecycle-bootstrap":
        project = ResearchProject.create(
            args.workspace,
            project_id=args.project_id,
            title=args.title,
            objective=args.objective,
        )
        _print_json(
            project.bootstrap_lifecycle_control_plane(
                args.observation,
                args.phase,
            )
        )
        return 0

    project = ResearchProject.open(args.workspace)
    if args.command == "lifecycle-observe-goal":
        _print_json(project.observe_codex_goal(args.observation))
        return 0
    if args.command == "lifecycle-observe-external-goal":
        _print_json(project.observe_external_codex_goal(args.observation))
        return 0
    if args.command == "lifecycle-start-phase":
        _print_json(project.start_lifecycle_phase(args.phase))
        return 0
    if args.command == "lifecycle-status":
        _print_json(project.lifecycle_status())
        return 0
    if args.command == "lifecycle-finalize":
        _print_json(project.finalize_lifecycle_phase(args.root, args.request))
        return 0
    if args.command == "lifecycle-verify-convergence":
        from .lifecycle import verify_state_convergence

        errors = verify_state_convergence(args.root.resolve(), args.workspace.resolve())
        if errors:
            raise ValueError("lifecycle state did not converge: " + "; ".join(errors))
        _print_json({"status": "passed", "error_count": 0})
        return 0
    if args.command == "adopt-tiangong-capabilities":
        _print_json(project.adopt_tiangong_capabilities(args.observation))
        return 0
    if args.command == "capture-tiangong-open-data":
        _print_json(project.capture_tiangong_open_data(args.export))
        return 0
    if args.command == "select-tiangong-open-data-case":
        _print_json(
            project.select_tiangong_open_data_case(args.catalog, args.policy)
        )
        return 0
    if args.command == "bind-tiangong-native-case":
        _print_json(
            project.bind_tiangong_native_case(args.case_input, args.result_input)
        )
        return 0
    if args.command == "restore-or-execute-tiangong-native-case":
        _print_json(project.restore_or_execute_tiangong_native_case())
        return 0
    if args.command == "transition-tiangong-authority":
        _print_json(project.transition_tiangong_authority(args.transition_input))
        return 0
    if args.command == "assemble-tiangong-second-case-package":
        _print_json(project.assemble_tiangong_second_case_package())
        return 0
    if args.command == "accept-tiangong-second-case-package-read-only":
        _print_json(project.accept_tiangong_second_case_package_read_only())
        return 0
    if args.command == "director-advance":
        _print_json(project.advance_research_director(args.input))
        return 0
    if args.command == "council-compile-agenda":
        _print_json(project.compile_council_agenda(args.input))
        return 0
    if args.command == "council-route":
        _print_json(project.route_council_decision(args.input))
        return 0
    if args.command == "council-open-cell":
        _print_json(project.open_council_cell(args.input))
        return 0
    if args.command == "council-register-worker-result":
        _print_json(project.register_council_worker_result(args.input))
        return 0
    if args.command == "council-advance":
        _print_json(project.advance_council_cell(args.input))
        return 0
    if args.command == "council-close":
        _print_json(project.close_council_cell(args.input))
        return 0
    if args.command == "council-fast-path":
        _print_json(project.complete_council_fast_path(args.input))
        return 0
    if args.command == "council-accept":
        _print_json(project.accept_council_outcome(args.input))
        return 0
    if args.command == "council-verify-rollback":
        _print_json(project.verify_council_rollback(args.input))
        return 0
    if args.command == "council-cutover":
        _print_json(project.record_council_cutover(args.gate_manifest))
        return 0
    if args.command == "council-rollback":
        _print_json(project.activate_council_rollback(args.input))
        return 0
    if args.command == "council-accept-read-only":
        _print_json(project.accept_council_read_only())
        return 0
    if args.command == "qualify-research-council-benchmark":
        _print_json(
            project.qualify_research_council_benchmark(
                args.observation, args.policy
            )
        )
        return 0
    if args.command == "freeze-research-council-experiment":
        _print_json(project.freeze_research_council_experiment(args.input))
        return 0
    if args.command == "advance-research-council-cell":
        _print_json(project.advance_research_council_cell(args.input))
        return 0
    if args.command == "register-research-council-trial":
        _print_json(project.register_research_council_trial(args.input))
        return 0
    if args.command == "evaluate-research-council":
        _print_json(project.evaluate_research_council(args.input))
        return 0
    if args.command == "accept-research-council-read-only":
        _print_json(project.accept_research_council_read_only())
        return 0
    if args.command == "tidas-context":
        _print_json(project.fetch_tidas_context(kind=args.kind, revision=args.revision))
        return 0
    if args.command == "process-plans":
        scope, scope_ref = _read_json_source(args.scope)
        _print_json(
            project.validate_process_build_plans(
                scope,
                revision=args.revision,
                source_ref=scope_ref,
            )
        )
        return 0
    if args.command == "resolve-gates":
        scope, scope_ref = _read_json_source(args.scope)
        performance, performance_ref = _read_json_source(args.performance_evidence)
        search_results, search_results_ref = _read_json_source(args.search_results)
        _print_json(
            project.resolve_release_gates(
                scope,
                revision=args.revision,
                performance_evidence=performance,
                search_results=search_results,
                scope_source_ref=scope_ref,
                performance_source_ref=performance_ref,
                search_results_source_ref=search_results_ref,
            )
        )
        return 0
    if args.command == "materialize-processes":
        _print_json(project.materialize_processes())
        return 0
    if args.command == "inventory-plan":
        evidence, evidence_ref = _read_json_source(args.inventory_evidence)
        search_results, search_results_ref = _read_json_source(
            args.search_results
        )
        _print_json(
            project.plan_inventory_evidence(
                evidence,
                search_results,
                evidence_source_ref=evidence_ref,
                search_results_source_ref=search_results_ref,
            )
        )
        return 0
    if args.command == "apply-inventory-scenarios":
        _print_json(project.apply_inventory_scenarios())
        return 0
    if args.command == "preflight-inventory-identities":
        _print_json(
            project.preflight_inventory_identities(
                remote_candidates=args.remote_candidates
            )
        )
        return 0
    if args.command == "materialize-inventory-entities":
        _print_json(project.materialize_inventory_entities())
        return 0
    if args.command == "integrate-inventory-references":
        _print_json(project.integrate_inventory_references())
        return 0
    if args.command == "rank-scientific-gaps":
        assessment, assessment_ref = _read_json_source(args.assessment)
        _print_json(
            project.rank_scientific_gaps(
                assessment,
                source_ref=assessment_ref,
            )
        )
        return 0
    if args.command == "materialize-residual-outputs":
        _print_json(project.materialize_residual_outputs())
        return 0
    if args.command == "materialize-ton-unit-scenarios":
        _print_json(project.materialize_ton_unit_scenarios())
        return 0
    if args.command == "compile-lineage":
        _print_json(project.compile_lineage_invalidation())
        return 0
    if args.command == "plan-evidence-acquisition":
        assessment, assessment_ref = _read_json_source(args.assessment)
        _print_json(
            project.plan_evidence_acquisition(
                assessment,
                source_ref=assessment_ref,
            )
        )
        return 0
    if args.command == "execute-public-discovery":
        _print_json(
            project.execute_public_discovery(retry_failed=args.retry_failed)
        )
        return 0
    if args.command == "expand-public-sources":
        _print_json(project.execute_public_source_expansion())
        return 0
    if args.command == "resolve-public-routes":
        _print_json(project.execute_author_institution_routes())
        return 0
    if args.command == "probe-record-exports":
        _print_json(project.execute_exact_record_export_probe())
        return 0
    if args.command == "expand-korean-sources":
        _print_json(project.execute_korean_scholarly_source_routes())
        return 0
    if args.command == "inspect-author-hosted-files":
        _print_json(project.execute_author_hosted_public_file_routes())
        return 0
    if args.command == "compile-acquisition-options":
        _print_json(project.compile_responsibility_aware_acquisition_options())
        return 0
    if args.command == "compile-stoichiometric-bracket":
        _print_json(project.compile_stoichiometric_consistency())
        return 0
    if args.command == "compile-claim-matrix":
        _print_json(project.compile_route_model_assurance_claim_matrix())
        return 0
    if args.command == "compile-replacement-map":
        _print_json(project.compile_claim_evidence_replacement_map())
        return 0
    if args.command == "simulate-replacement":
        _print_json(project.simulate_high_impact_evidence_replacement())
        return 0
    if args.command == "compile-evidence-intake-harness":
        _print_json(project.compile_evidence_intake_validation_harness())
        return 0
    if args.command == "compile-candidate-ledger":
        _print_json(project.compile_current_evidence_candidate_ledger())
        return 0
    if args.command == "compile-admission-transaction":
        _print_json(project.compile_evidence_admission_transaction_boundary())
        return 0
    if args.command == "compile-comparison-readiness":
        _print_json(project.compile_current_comparison_readiness_gate())
        return 0
    if args.command == "plan-native-lifecyclemodels":
        _print_json(project.plan_native_route_lifecyclemodels())
        return 0
    if args.command == "materialize-lifecyclemodel-shells":
        _print_json(project.materialize_native_lifecyclemodel_shells())
        return 0
    if args.command == "build-resulting-processes":
        _print_json(project.build_native_resulting_process_dry_runs())
        return 0
    if args.command == "compile-model-package":
        _print_json(project.compile_native_route_model_execution_package())
        return 0
    if args.command == "replay-model-package":
        _print_json(project.replay_native_route_model_execution_package())
        return 0
    if args.command == "bind-portable-runtime":
        _print_json(project.bind_portable_native_tool_runtime_closure())
        return 0
    if args.command == "compile-provider-network-gaps":
        _print_json(project.compile_native_route_provider_network_gap_map())
        return 0
    if args.command == "resolve-provider-candidates":
        _print_json(project.resolve_native_provider_candidates())
        return 0
    if args.command == "compile-provider-evidence-requirements":
        _print_json(project.compile_provider_process_evidence_requirements())
        return 0
    if args.command == "probe-uslci-provider-candidates":
        _print_json(project.probe_uslci_electricity_provider_candidates())
        return 0
    if args.command == "compile-uslci-provider-normalization":
        _print_json(project.compile_uslci_provider_normalization_plan())
        return 0
    if args.command == "probe-uslci-generation-closure":
        _print_json(project.probe_uslci_generation_provider_closure())
        return 0
    if args.command == "probe-uslci-unit-provider-batch":
        _print_json(project.probe_uslci_unit_provider_batch())
        return 0
    if args.command == "extend-uslci-unit-provider-batch-2":
        _print_json(project.extend_uslci_unit_provider_batch_2())
        return 0
    if args.command == "extend-uslci-unit-provider-batch":
        _print_json(project.extend_uslci_unit_provider_batch(args.batch_index))
        return 0
    if args.command == "close-uslci-unit-provider-current-breadth-layer":
        _print_json(project.close_uslci_unit_provider_current_breadth_layer())
        return 0
    if args.command == "probe-uslci-depth-4-provider-closure-batch-1":
        _print_json(project.probe_uslci_depth_4_provider_batch_1())
        return 0
    if args.command == "extend-uslci-depth-4-provider-batch":
        _print_json(project.extend_uslci_depth_4_provider_batch(args.batch_index))
        return 0
    if args.command == "close-uslci-depth-4-provider-layer":
        _print_json(project.close_uslci_depth_4_provider_layer())
        return 0
    if args.command == "compile-uslci-background-inventory-integration":
        _print_json(project.compile_uslci_background_inventory_integration_plan())
        return 0
    if args.command == "compile-uslci-native-slice-preflight-plan":
        _print_json(project.compile_uslci_native_slice_preflight_plan())
        return 0
    if args.command == "execute-uslci-native-slice-preflight-batch-1":
        _print_json(project.execute_uslci_native_slice_preflight_batch_1())
        return 0
    if args.command.startswith("execute-uslci-native-slice-preflight-batch-"):
        batch_index = int(args.command.rsplit("-", 1)[1])
        _print_json(
            project.execute_uslci_native_slice_preflight_continuation(batch_index)
        )
        return 0
    if args.command == "resolve-uslci-native-reference-flow-preflight-blockers":
        _print_json(project.resolve_uslci_native_reference_flow_preflight_blockers())
        return 0
    if args.command == "compile-uslci-native-reference-flow-authoring-plan":
        _print_json(project.compile_uslci_native_reference_flow_authoring_plan())
        return 0
    if args.command == "execute-uslci-native-reference-flow-authoring-plan":
        _print_json(project.execute_uslci_native_reference_flow_authoring_plan())
        return 0
    if args.command == "recompile-uslci-reference-flow-dependent-process-actions":
        _print_json(
            project.recompile_uslci_reference_flow_dependent_process_actions()
        )
        return 0
    if args.command == "execute-uslci-native-slice-process-action-plan":
        _print_json(project.execute_uslci_native_slice_process_action_plan())
        return 0
    if args.command == "compile-uslci-native-slice-flow-identity-resolution-plan":
        _print_json(project.compile_uslci_flow_identity_resolution_plan())
        return 0
    if args.command.startswith("execute-uslci-flow-identity-resolution-batch-"):
        batch_index = int(args.command.rsplit("-", 1)[1])
        _print_json(project.execute_uslci_flow_identity_resolution_batch(batch_index))
        return 0
    if args.command == "compile-uslci-native-slice-flow-authoring-plan":
        _print_json(project.compile_uslci_flow_authoring_plan())
        return 0
    if args.command == "resolve-uslci-flow-authoring-canary-supports":
        _print_json(project.resolve_uslci_flow_authoring_canary_supports())
        return 0
    if args.command == "compile-uslci-flow-authoring-canary-saved-plans":
        _print_json(project.compile_uslci_flow_authoring_canary_saved_plans())
        return 0
    if args.command == "execute-uslci-flow-authoring-canary-native-gates":
        _print_json(project.execute_uslci_flow_authoring_canary_native_gates())
        return 0
    if args.command == "resolve-uslci-elementary-flow-native-qa-harness":
        _print_json(project.resolve_uslci_elementary_flow_native_qa_harness())
        return 0
    if args.command == "resolve-uslci-flow-authoring-bulk-batch-2-supports":
        _print_json(project.resolve_uslci_flow_authoring_bulk_batch_2_supports())
        return 0
    if args.command == "resolve-uslci-flow-authoring-bulk-batch-3-supports":
        _print_json(project.resolve_uslci_flow_authoring_bulk_batch_3_supports())
        return 0
    if args.command == "resolve-uslci-flow-authoring-bulk-batch-4-supports":
        _print_json(project.resolve_uslci_flow_authoring_bulk_batch_4_supports())
        return 0
    if args.command == "resolve-uslci-flow-authoring-bulk-batch-5-supports":
        _print_json(project.resolve_uslci_flow_authoring_bulk_batch_5_supports())
        return 0
    if args.command == "resolve-uslci-flow-authoring-bulk-batch-6-supports":
        _print_json(project.resolve_uslci_flow_authoring_bulk_batch_6_supports())
        return 0
    if args.command == "resolve-uslci-flow-authoring-bulk-batch-7-supports":
        _print_json(project.resolve_uslci_flow_authoring_bulk_batch_7_supports())
        return 0
    if args.command == "resolve-uslci-flow-authoring-bulk-batch-8-supports":
        _print_json(project.resolve_uslci_flow_authoring_bulk_batch_8_supports())
        return 0
    if args.command == "resolve-uslci-flow-authoring-bulk-batch-9-supports":
        _print_json(project.resolve_uslci_flow_authoring_bulk_batch_9_supports())
        return 0
    if args.command == "resolve-uslci-flow-authoring-bulk-batch-10-supports":
        _print_json(project.resolve_uslci_flow_authoring_bulk_batch_10_supports())
        return 0
    if args.command == "resolve-uslci-flow-authoring-bulk-batch-11-supports":
        _print_json(project.resolve_uslci_flow_authoring_bulk_batch_11_supports())
        return 0
    if args.command == "resolve-uslci-flow-authoring-bulk-batch-12-supports":
        _print_json(project.resolve_uslci_flow_authoring_bulk_batch_12_supports())
        return 0
    if args.command == "resolve-uslci-flow-authoring-bulk-batch-13-supports":
        _print_json(project.resolve_uslci_flow_authoring_bulk_batch_13_supports())
        return 0
    if args.command == "resolve-uslci-flow-authoring-bulk-batch-14-supports":
        _print_json(project.resolve_uslci_flow_authoring_bulk_batch_14_supports())
        return 0
    if args.command == "resolve-uslci-flow-authoring-bulk-batch-15-supports":
        _print_json(project.resolve_uslci_flow_authoring_bulk_batch_15_supports())
        return 0
    if args.command == "resolve-uslci-flow-authoring-bulk-batch-16-supports":
        _print_json(project.resolve_uslci_flow_authoring_bulk_batch_16_supports())
        return 0
    if args.command == "compile-uslci-flow-authoring-bulk-batch-2-saved-plans":
        _print_json(project.compile_uslci_flow_authoring_bulk_batch_2_saved_plans())
        return 0
    if args.command == "compile-uslci-flow-authoring-bulk-batch-3-saved-plans":
        _print_json(project.compile_uslci_flow_authoring_bulk_batch_3_saved_plans())
        return 0
    if args.command == "compile-uslci-flow-authoring-bulk-batch-4-saved-plans":
        _print_json(project.compile_uslci_flow_authoring_bulk_batch_4_saved_plans())
        return 0
    if args.command == "compile-uslci-flow-authoring-bulk-batch-5-saved-plans":
        _print_json(project.compile_uslci_flow_authoring_bulk_batch_5_saved_plans())
        return 0
    if args.command == "compile-uslci-flow-authoring-bulk-batch-6-saved-plans":
        _print_json(project.compile_uslci_flow_authoring_bulk_batch_6_saved_plans())
        return 0
    if args.command == "compile-uslci-flow-authoring-bulk-batch-7-saved-plans":
        _print_json(project.compile_uslci_flow_authoring_bulk_batch_7_saved_plans())
        return 0
    if args.command == "compile-uslci-flow-authoring-bulk-batch-8-saved-plans":
        _print_json(project.compile_uslci_flow_authoring_bulk_batch_8_saved_plans())
        return 0
    if args.command == "compile-uslci-flow-authoring-bulk-batch-9-saved-plans":
        _print_json(project.compile_uslci_flow_authoring_bulk_batch_9_saved_plans())
        return 0
    if args.command == "compile-uslci-flow-authoring-bulk-batch-10-saved-plans":
        _print_json(project.compile_uslci_flow_authoring_bulk_batch_10_saved_plans())
        return 0
    if args.command == "compile-uslci-flow-authoring-bulk-batch-11-saved-plans":
        _print_json(project.compile_uslci_flow_authoring_bulk_batch_11_saved_plans())
        return 0
    if args.command == "compile-uslci-flow-authoring-bulk-batch-12-saved-plans":
        _print_json(project.compile_uslci_flow_authoring_bulk_batch_12_saved_plans())
        return 0
    if args.command == "compile-uslci-flow-authoring-bulk-batch-13-saved-plans":
        _print_json(project.compile_uslci_flow_authoring_bulk_batch_13_saved_plans())
        return 0
    if args.command == "compile-uslci-flow-authoring-bulk-batch-14-saved-plans":
        _print_json(project.compile_uslci_flow_authoring_bulk_batch_14_saved_plans())
        return 0
    if args.command == "compile-uslci-flow-authoring-bulk-batch-15-saved-plans":
        _print_json(project.compile_uslci_flow_authoring_bulk_batch_15_saved_plans())
        return 0
    if args.command == "compile-uslci-flow-authoring-bulk-batch-16-saved-plans":
        _print_json(project.compile_uslci_flow_authoring_bulk_batch_16_saved_plans())
        return 0
    if args.command == "execute-uslci-flow-authoring-bulk-batch-2-native-gates":
        _print_json(
            project.execute_uslci_flow_authoring_bulk_batch_2_native_gates()
        )
        return 0
    if args.command == "execute-uslci-flow-authoring-bulk-batch-3-native-gates":
        _print_json(
            project.execute_uslci_flow_authoring_bulk_batch_3_native_gates()
        )
        return 0
    if args.command == "execute-uslci-flow-authoring-bulk-batch-4-native-gates":
        _print_json(
            project.execute_uslci_flow_authoring_bulk_batch_4_native_gates()
        )
        return 0
    if args.command == "execute-uslci-flow-authoring-bulk-batch-5-native-gates":
        _print_json(
            project.execute_uslci_flow_authoring_bulk_batch_5_native_gates()
        )
        return 0
    if args.command == "execute-uslci-flow-authoring-bulk-batch-6-native-gates":
        _print_json(
            project.execute_uslci_flow_authoring_bulk_batch_6_native_gates()
        )
        return 0
    if args.command == "execute-uslci-flow-authoring-bulk-batch-7-native-gates":
        _print_json(
            project.execute_uslci_flow_authoring_bulk_batch_7_native_gates()
        )
        return 0
    if args.command == "execute-uslci-flow-authoring-bulk-batch-8-native-gates":
        _print_json(
            project.execute_uslci_flow_authoring_bulk_batch_8_native_gates()
        )
        return 0
    if args.command == "execute-uslci-flow-authoring-bulk-batch-9-native-gates":
        _print_json(
            project.execute_uslci_flow_authoring_bulk_batch_9_native_gates()
        )
        return 0
    if args.command == "execute-uslci-flow-authoring-bulk-batch-10-native-gates":
        _print_json(
            project.execute_uslci_flow_authoring_bulk_batch_10_native_gates()
        )
        return 0
    if args.command == "execute-uslci-flow-authoring-bulk-batch-11-native-gates":
        _print_json(
            project.execute_uslci_flow_authoring_bulk_batch_11_native_gates()
        )
        return 0
    if args.command == "execute-uslci-flow-authoring-bulk-batch-12-native-gates":
        _print_json(
            project.execute_uslci_flow_authoring_bulk_batch_12_native_gates()
        )
        return 0
    if args.command == "execute-uslci-flow-authoring-bulk-batch-13-native-gates":
        _print_json(
            project.execute_uslci_flow_authoring_bulk_batch_13_native_gates()
        )
        return 0
    if args.command == "execute-uslci-flow-authoring-bulk-batch-14-native-gates":
        _print_json(
            project.execute_uslci_flow_authoring_bulk_batch_14_native_gates()
        )
        return 0
    if args.command == "execute-uslci-flow-authoring-bulk-batch-15-native-gates":
        _print_json(
            project.execute_uslci_flow_authoring_bulk_batch_15_native_gates()
        )
        return 0
    if args.command == "execute-uslci-flow-authoring-bulk-batch-16-native-gates":
        _print_json(
            project.execute_uslci_flow_authoring_bulk_batch_16_native_gates()
        )
        return 0
    if args.command == "analyze-uslci-flow-authoring-corpus-closure":
        _print_json(project.analyze_uslci_flow_authoring_corpus_closure())
        return 0
    if args.command == "compile-uslci-flow-authoring-corpus-release-plan":
        _print_json(project.compile_uslci_flow_authoring_corpus_release_plan())
        return 0
    if args.command == "execute-uslci-flow-authoring-corpus-release-canary":
        _print_json(project.execute_uslci_flow_authoring_corpus_release_canary())
        return 0
    if args.command == "compile-minimum-lifepo4-comparison-graph":
        _print_json(project.compile_minimum_lifepo4_comparison_graph())
        return 0
    if args.command == "materialize-minimum-lifepo4-comparison-model":
        _print_json(project.materialize_minimum_lifepo4_comparison_model())
        return 0
    if args.command == "accept-minimum-lifepo4-comparison-model-read-only":
        _print_json(project.accept_minimum_lifepo4_comparison_model_read_only())
        return 0
    if args.command == "execute-lifepo4-comparative-lcia-sensitivity-uncertainty":
        _print_json(
            project.execute_lifepo4_comparative_lcia_sensitivity_uncertainty()
        )
        return 0
    if args.command == "accept-lifepo4-comparative-lcia-read-only":
        _print_json(project.accept_lifepo4_comparative_lcia_read_only())
        return 0
    if args.command == "assemble-lifepo4-screening-final-package":
        _print_json(project.assemble_lifepo4_screening_final_package())
        return 0
    if args.command == "accept-lifepo4-screening-final-package-read-only":
        _print_json(project.accept_lifepo4_screening_final_package_read_only())
        return 0

    project.materialize_views()
    human = (project.workspace / project.HUMAN_FILE).read_text(encoding="utf-8")
    machine = json.loads(
        (project.workspace / project.MACHINE_FILE).read_text(encoding="utf-8")
    )
    if args.format == "human":
        print(human, end="")
    elif args.format == "machine":
        _print_json(machine)
    else:
        _print_json({"human": human, "machine": machine})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
