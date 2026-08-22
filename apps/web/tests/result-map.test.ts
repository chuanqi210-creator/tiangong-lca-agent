import assert from "node:assert/strict";
import test from "node:test";

import { failedNoticeFromJob, phaseFromJob, resultLayers } from "../src/lib/d18/map.ts";
import type { ResultView } from "../src/lib/d18/types.ts";

function view(result: ResultView["result"]): ResultView {
  return { status: "accepted", result };
}

test("normal accepted candidate uses the backend summary without promotion", () => {
  const projected = resultLayers(
    view({
      status: "candidate_only",
      claim_ceiling: "candidate_only",
      summary: "这是后端已接纳的候选摘要。",
    }),
  );

  assert.equal(projected.limited, false);
  assert.deepEqual(projected.layers[0].body, ["这是后端已接纳的候选摘要。"]);
  assert.match(projected.layers[1].body.join("\n"), /候选结果/);
  assert.doesNotMatch(projected.layers.flatMap((layer) => layer.body).join("\n"), /正式事实已确认/);
});

test("source-bound material result renders the accepted answer when summary is absent", () => {
  const projected = resultLayers(
    view({
      status: "formal_result_accepted",
      claim_ceiling: "source_bounded_interpretation",
      answer: "这是一个带精确来源的 ILCD 过程数据集。",
      bounded_conclusion: "这是一个带精确来源的 ILCD 过程数据集。",
    }),
  );

  assert.deepEqual(projected.layers[0].body, ["这是一个带精确来源的 ILCD 过程数据集。"]);
});

test("bounded result uses only the bounded conclusion and keeps the boundary visible", () => {
  const projected = resultLayers(
    view({
      status: "candidate_only",
      claim_ceiling: "bounded_missing_data_conclusion",
      summary: "不应展示的普通摘要。",
      bounded_conclusion: "缺少功能单位，当前只能形成受限结论。",
    }),
  );

  assert.equal(projected.limited, true);
  assert.match(projected.layers[0].body[0], /^缺少功能单位/);
  assert.match(projected.layers[0].body[0], /受限结论/);
  assert.doesNotMatch(projected.layers[0].body[0], /不应展示/);
});

test("formal missing-data result is rendered as limited with every blocking field", () => {
  const projected = resultLayers(
    view({
      status: "formal_limited_result_accepted",
      claim_ceiling: "calculation_structure_only",
      bounded_conclusion: "关键输入不可获得，当前不能计算碳足迹。",
      missing_fields: ["functional_unit", "inventory_data"],
      limitations: ["缺少用户专有输入。"],
    }),
  );

  const visible = projected.layers.flatMap((layer) => layer.body).join("\n");
  assert.equal(projected.limited, true);
  assert.match(visible, /关键输入不可获得/);
  assert.match(visible, /functional_unit/);
  assert.match(visible, /inventory_data/);
  assert.match(visible, /受限结论/);
});

test("TianGong-native bounded result renders exact authority and science boundaries", () => {
  const projected = resultLayers(
    view({
      status: "formal_limited_result_accepted",
      claim_ceiling: "capability_contract_only",
      bounded_conclusion: "已确认锁定的 TianGong TIDAS SDK 合同。",
      nativeAuthority: {
        provider: "TianGong",
        capabilityId: "tiangong.native.tidas-sdk",
        sourceIdentity: "@tiangong-lca/tidas-sdk",
        sourceVersion: "0.1.45",
        sourceIntegrity: "sha512-locked",
        contentSha256: "a".repeat(64),
      },
      scientificBoundary: {
        lcaMethod: "not_selected",
        exchangeProvider: "not_selected",
        quantityUnit: "not_evaluated",
        scientificResultCreated: false,
      },
    }),
  );

  const visible = projected.layers.flatMap((layer) => layer.body).join("\n");
  assert.equal(projected.limited, true);
  assert.match(visible, /@tiangong-lca\/tidas-sdk/);
  assert.match(visible, /0\.1\.45/);
  assert.match(visible, /tiangong\.native\.tidas-sdk/);
  assert.match(visible, /LCA 方法：未选择/);
  assert.match(visible, /Provider：未选择/);
  assert.match(visible, /单位：未评价/);
  assert.match(visible, /未生成科学计算结果/);
});

test("missing backend summary produces a fixed non-scientific notice", () => {
  const projected = resultLayers(
    view({ status: "candidate_only", claim_ceiling: "candidate_only" }),
  );

  assert.equal(projected.layers[0].body.length, 1);
  assert.notEqual(projected.layers[0].body[0].trim(), "");
  assert.match(projected.layers[0].body[0], /摘要/);
  assert.match(projected.layers[0].body[0], /项目档案/);
});

test("failed jobs preserve the typed backend runtime boundary", () => {
  assert.equal(
    phaseFromJob({
      job_id: "temporary",
      status: "failed",
      runtime_boundary: {
        category: "temporarily_unavailable",
        boundary: "runtime_failure",
        failure_code: "provider_failure",
        retryable: true,
        recovery_route: "retry-after-provider-recovery",
      },
    }),
    "temporarily-unavailable",
  );
  assert.equal(
    phaseFromJob({
      job_id: "invalid",
      status: "failed",
      runtime_boundary: {
        category: "invalid_input",
        boundary: "runtime_failure",
        failure_code: "invalid_result_or_contract",
        retryable: false,
        recovery_route: "inspect-input-contract",
      },
    }),
    "invalid-input",
  );
  assert.equal(
    phaseFromJob({
      job_id: "unknown",
      status: "failed",
      runtime_boundary: {
        category: "unexpected_failure",
        boundary: "runtime_failure",
        failure_code: "unclassified",
        retryable: false,
        recovery_route: "inspect-runtime-failure",
      },
    }),
    "unexpected-failure",
  );
});

test("TianGong credential boundary explains the actual blocked capability", () => {
  const notice = failedNoticeFromJob({
    job_id: "tiangong-search",
    status: "failed",
    runtime_boundary: {
      category: "temporarily_unavailable",
      boundary: "capability_unavailable",
      failure_code: "tiangong_credential_required",
      retryable: false,
      recovery_route: "connect-tiangong-read-identity",
    },
  });

  assert.match(notice.title, /TianGong/);
  assert.match(notice.body, /只读身份/);
  assert.match(notice.body, /不需要补充功能单位或系统边界/);
});

test("accepted result mapping preserves only server-projected citations and source-use decisions", () => {
  const projected = resultLayers(
    view({
      status: "formal_result_accepted",
      claim_ceiling: "source_bounded_interpretation",
      bounded_conclusion: "结论附有精确来源。",
      citations: [{
        citationId: "cit_0123456789abcdef01234567",
        materialId: "material-a",
        revisionId: "revision-a",
        displayName: "inventory.csv",
        locator: { kind: "spreadsheet_range", sheet_name: "Table1", cell_range: "A2:C2" },
        evidenceUrl: "/v1/research/project-a/evidence/locator-a",
        sourceAuthority: "user_supplied_project_input",
        processingStatus: "ready",
      }],
      sourceUse: [{
        materialId: "material-a",
        revisionId: "revision-a",
        displayName: "inventory.csv",
        status: "used",
        reason: "cited_in_accepted_result",
        sourceAuthority: "user_supplied_project_input",
        processingStatus: "ready",
        selectedLocatorCount: 1,
        citedLocatorCount: 1,
      }],
      materialLimitations: [],
      officialSources: [{
        sourceId: "eu-ir-2025-2547",
        authority: "official_public_source",
        sourceAuthority: "Official Journal and EUR-Lex",
        requestedUrl: "https://eur-lex.europa.eu/source",
        finalUrl: "https://publications.europa.eu/source.pdf",
        accessedAt: "2026-08-17T08:00:00Z",
        bodySha256: "a".repeat(64),
        receiptSha256: "b".repeat(64),
        parseLocation: "functional unit",
        sourceVersion: "2025/2547",
        snapshotVersion: `sha256:${"a".repeat(64)}`,
      }],
      officialClaimBindings: [{
        claimId: "claim_official_a",
        statement: "The functional unit is defined by the official regulation.",
        modality: "definitional",
        sourceIds: ["eu-ir-2025-2547"],
        sourceSha256s: ["a".repeat(64)],
      }],
      route: "deliver-accepted-result",
      methods: ["strict-delimited-table@1.0.0"],
      completedStateChanges: ["selected_material_evidence", "accepted_research_result"],
    }),
  );
  assert.equal(projected.citations.length, 1);
  assert.equal(projected.sourceUse[0]?.status, "used");
  assert.deepEqual(projected.materialLimitations, []);
  assert.equal(projected.officialSources.length, 1);
  assert.equal(
    projected.officialSources[0]?.finalUrl,
    "https://publications.europa.eu/source.pdf",
  );
  assert.match(projected.layers[1].body.join("\n"), /deliver-accepted-result/);
  assert.match(projected.layers[1].body.join("\n"), /strict-delimited-table@1\.0\.0/);
  assert.match(projected.layers[1].body.join("\n"), /已接纳研究结果/);
  assert.match(projected.layers[1].body.join("\n"), /functional unit/);
  assert.match(projected.layers[1].body.join("\n"), /访问时间 2026-08-17/);
  assert.match(projected.layers[1].body.join("\n"), /2025\/2547/);
  assert.match(projected.layers[1].body.join("\n"), /functional unit is defined/);
  assert.match(projected.layers[1].body.join("\n"), /eu-ir-2025-2547/);
  assert.doesNotMatch(
    projected.layers[1].body.join("\n"),
    /\]\(https:\/\/publications\.europa\.eu\/source\.pdf\)/,
  );
});
