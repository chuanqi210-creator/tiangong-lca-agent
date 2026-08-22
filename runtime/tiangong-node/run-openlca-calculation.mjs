import fs from "node:fs/promises";


function argumentsOf(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) {
      throw new Error("openLCA bridge arguments are invalid");
    }
    result[key.slice(2)] = value;
  }
  for (const required of ["endpoint", "module", "request"]) {
    if (!result[required]) {
      throw new Error(`openLCA bridge argument is absent: ${required}`);
    }
  }
  return result;
}


function indexBy(rows, key) {
  return new Map(rows.map((row) => [key(row), row]));
}


function fixed(entity, row) {
  entity.id = row.id;
  entity.name = row.name;
  entity.version = row.version ?? "01.00.000";
  entity.lastChange = "2026-07-24T00:00:00Z";
  return entity;
}


const args = argumentsOf(process.argv.slice(2));
const o = await import(args.module);
const request = JSON.parse(await fs.readFile(args.request, "utf8"));
if (request.contract !== "lca_research_kernel.openlca_isolated_model/v1") {
  throw new Error("openLCA isolated model contract is unsupported");
}

const client = o.IpcClient.on(args.endpoint);
const propertyRows = new Map();
for (const flow of request.flows) {
  if (!propertyRows.has(flow.flow_property_id)) {
    propertyRows.set(flow.flow_property_id, flow);
  }
}
const properties = new Map();
for (const [propertyId, row] of [...propertyRows.entries()].sort()) {
  const unit = o.createUnit(row.reference_unit);
  unit.id = row.unit_id ?? `${propertyId}-unit`;
  const group = o.createUnitGroup(
    `${row.flow_property_name} units`,
    unit,
  );
  group.id = row.unit_group_id ?? `${propertyId}-group`;
  group.version = "01.00.000";
  group.lastChange = "2026-07-24T00:00:00Z";
  await client.put(group);
  const property = o.createFlowProperty(row.flow_property_name, group);
  property.id = propertyId;
  property.version = "01.00.000";
  property.lastChange = "2026-07-24T00:00:00Z";
  await client.put(property);
  properties.set(propertyId, { property, unit });
}

const flows = new Map();
for (const row of request.flows) {
  const property = properties.get(row.flow_property_id)?.property;
  if (!property) {
    throw new Error(`flow property is absent: ${row.flow_property_id}`);
  }
  let flow;
  if (row.flow_type === "PRODUCT_FLOW") {
    flow = o.createProduct(row.name, property);
  } else if (row.flow_type === "WASTE_FLOW") {
    flow = o.createWaste(row.name, property);
  } else if (row.flow_type === "ELEMENTARY_FLOW") {
    flow = o.createElementaryFlow(row.name, property);
  } else {
    throw new Error(`flow type is unsupported: ${row.flow_type}`);
  }
  fixed(flow, row);
  await client.put(flow);
  flows.set(`${row.id}@${row.version}`, flow);
}

const processRows = indexBy(request.processes, (row) => row.id);
const processes = new Map();
const providerAssignments = [];
for (const row of request.processes) {
  processes.set(row.id, fixed(o.createProcess(row.name), row));
}
for (const row of request.processes) {
  const process = processes.get(row.id);
  for (const exchangeRow of row.exchanges) {
    const flow = flows.get(
      `${exchangeRow.flow_id}@${exchangeRow.flow_version}`,
    );
    if (!flow) {
      throw new Error(`exchange flow is absent: ${exchangeRow.flow_id}`);
    }
    const property = properties.get(flow.flowProperties[0].flowProperty.id);
    const exchange = exchangeRow.is_input
      ? o.createInput(process, flow, exchangeRow.amount, property?.unit)
      : o.createOutput(process, flow, exchangeRow.amount, property?.unit);
    exchange.internalId = exchangeRow.internal_id;
    exchange.isQuantitativeReference =
      exchangeRow.is_quantitative_reference === true;
    if (exchangeRow.default_provider_id) {
      if (!processes.has(exchangeRow.default_provider_id)
        || !processRows.has(exchangeRow.default_provider_id)) {
        throw new Error(
          `default provider is absent: ${exchangeRow.default_provider_id}`,
        );
      }
      providerAssignments.push({
        process,
        exchange,
        providerId: exchangeRow.default_provider_id,
      });
    }
  }
  process.lastInternalId = Math.max(
    0,
    ...row.exchanges.map((exchange) => exchange.internal_id),
  );
  await client.put(process);
}
for (const assignment of providerAssignments) {
  assignment.exchange.defaultProvider =
    processes.get(assignment.providerId).toRef();
}
for (const row of request.processes) {
  await client.put(processes.get(row.id));
}
const processReadback = [];
for (const row of request.processes) {
  const stored = await client.get(o.RefType.Process, row.id);
  processReadback.push({
    process_id: row.id,
    default_provider_edges: (stored?.exchanges ?? [])
      .filter((exchange) => exchange.defaultProvider)
      .map((exchange) => ({
        flow_id: exchange.flow?.id,
        provider_process_id: exchange.defaultProvider?.id,
        exchange_internal_id: exchange.internalId,
      })),
  });
}

const methodRow = request.impact_method;
const category = fixed(
  o.createImpactCategory(methodRow.name, methodRow.reference_unit),
  {
    id: methodRow.impact_category_id,
    name: methodRow.name,
    version: methodRow.version,
  },
);
for (const factorRow of methodRow.factors) {
  const flow = flows.get(`${factorRow.flow_id}@${factorRow.flow_version}`);
  if (!flow) {
    throw new Error(`impact factor flow is absent: ${factorRow.flow_id}`);
  }
  o.createImpactFactor(category, flow, factorRow.value);
}
await client.put(category);
const method = fixed(o.createImpactMethod(methodRow.name, [category]), methodRow);
await client.put(method);

const root = processes.get(request.root_process.id);
if (!root) {
  throw new Error("root process is absent");
}
const system = await client.createProductSystem(
  root,
  o.LinkingConfig.of({
    preferUnitProcesses: true,
    providerLinking: o.ProviderLinking.ONLY_DEFAULTS,
  }),
);
if (!system) {
  throw new Error("product system creation failed");
}
const systemModel = await client.get(o.RefType.ProductSystem, system.id);
if (!systemModel) {
  throw new Error("product system read-back failed");
}
const rootFlow = flows.get(
  `${request.root_process.flow_id}@${request.processes
    .find((row) => row.id === request.root_process.id)
    .exchanges.find((exchange) => exchange.is_quantitative_reference)
    .flow_version}`,
);
const rootProperty = properties.get(rootFlow.flowProperties[0].flowProperty.id);
const setup = o.CalculationSetup.of({
  target: system,
  amount: request.root_process.amount,
  unit: rootProperty.unit.toRef(),
  flowProperty: rootProperty.property.toRef(),
  impactMethod: method.toRef(),
});
const calculation = await client.calculate(setup);
try {
  const state = await calculation.untilReady(100);
  if (state.error) {
    throw new Error(`openLCA calculation error: ${state.error}`);
  }
  const totalFlows = await calculation.getTotalFlows();
  const totalImpacts = await calculation.getTotalImpacts();
  const inventory = totalFlows
    .map((row) => ({
      flow_id: row.enviFlow?.flow?.id,
      direction: row.enviFlow?.isInput ? "INPUT" : "OUTPUT",
      amount: row.amount,
    }))
    .sort((left, right) =>
      `${left.flow_id}:${left.direction}`.localeCompare(
        `${right.flow_id}:${right.direction}`,
      ),
    );
  const impacts = totalImpacts
    .map((row) => ({
      impact_category_id: row.impactCategory?.id,
      amount: row.amount,
      unit: methodRow.reference_unit,
    }))
    .sort((left, right) =>
      String(left.impact_category_id).localeCompare(
        String(right.impact_category_id),
      ),
    );
  const result = {
    contract: "lca_research_kernel.openlca_isolated_result/v1",
    calculation_id: calculation.id,
    method: {
      identity: methodRow.id,
      version: methodRow.version,
    },
    input_fact_ids: ["research-source-material"],
    result: {
      value: String(impacts[0]?.amount),
      unit: methodRow.reference_unit,
    },
    unit_groups: {
      [methodRow.reference_unit]: [methodRow.id],
    },
    replay_command: "execute exact source-bound OpenLCA IPC request",
    inventory,
    impacts,
    runtime: {
      openlca: "2.6.2",
      olca_ipc: "2.6.2",
      transport: "loopback_json_rpc",
    },
    product_system: {
      process_ids: (systemModel.processes ?? [])
        .map((row) => row.id)
        .sort(),
      links: (systemModel.processLinks ?? []).map((row) => ({
        consumer_process_id: row.process?.id,
        provider_process_id: row.provider?.id,
        flow_id: row.flow?.id,
        exchange_internal_id: row.exchange?.internalId,
      })),
    },
    process_readback: processReadback,
    calculations: 1,
    executed_external_writes: 0,
  };
  process.stdout.write(`${JSON.stringify(result)}\n`);
} finally {
  await calculation.dispose();
}
