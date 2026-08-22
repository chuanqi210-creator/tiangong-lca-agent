# TianGong 一体化 LCA 科研 Agent：Research Kernel 架构基线

status: accepted_and_in_implementation
updated_at: 2026-07-16 Asia/Shanghai

## 产品结论

第一用户是具备 LCA 基础、但需要跨资料、模型、工具和审查持续推进研究的科研人员。产品不是 TianGong 外面的聊天补丁，而是 TianGong 科研工作流的智能控制面：TIDAS、数据、CLI、MCP、builder、QA 与发布交接共同构成其知识和执行底座。

成功结果不是“回答了问题”，而是从研究问题生成一个可执行、可审查、可重放的研究模型包，并能解释关键结论如何由证据、假设和模型选择得到。

## 已确认的十二项设计决定

| ID | 决定 |
| --- | --- |
| D-01 | 第一用户为 LCA 科研人员；薄闭环是研究问题到可审计、可执行的模型包。 |
| D-02 | 核心失败是跨步骤的科研语义连续性丢失，而不只是缺少单个工具。 |
| D-03 | 使用持久 Research Project/Research Spine；TIDAS 仍是权威数据与模型语义。 |
| D-04 | AI 默认自主完成科学、统计、因果和工程判断；记录决定、来源和高风险项，外部责任动作才停。 |
| D-05 | 建立薄 Research Kernel；复用外部工程机制，不复制第二套平台。 |
| D-06 | 第一纵向原型是实际双路线比较 LCA，并覆盖重放、幂等、失效和保证测试。 |
| D-07 | 一个持久 Controller、按需短期 specialist、薄 Skills/CLI、共享 Kernel 真相。 |
| D-08 | 状态分成人看渐进解释与机器看紧凑结构两种投影。 |
| D-09 | 活跃上下文允许有损编译；事件和工件历史无损保留，并用版本、哈希和来源指针恢复。 |
| D-10 | 执行与数据边界按策略混合，而不是强制全部本地或全部远端。 |
| D-11 | 默认使用最聪明且高效的模型；预算按持续产出和成果节拍管理。 |
| D-12 | 以动态连续节拍交付可运行、可验证能力；节拍大小随实际不确定性调整。 |

## 当前方向重基线（D-13，2026-07-21）

| ID | 决定 |
| --- | --- |
| D-13 | **Council-default 已通过守护式切换，成为正式运行架构。** Research Director 是唯一入口和整合者，确定性低争议工作仍可走单 Controller fast path；Git `22d0576` 保留为不删除新历史的精确回滚。优越性 A/B、盲评和正向比较簇仍只是可选优化，并非上线前置门槛，也没有证据证明 Council 优于单 Agent。Freshwater 只证明单案例端到端功能，不是双路线 comparative LCA benchmark。 |

D-13 改变未来目标方向，不改写 D-01–D-12 的历史实现证据、TianGong 第一权威、Research Kernel 持久真相或责任/副作用边界。详细处置与口径上限见 `docs/agent-project-rebaseline-2026-07-21.json`。

## 生命周期状态收敛（D-14）

| ID | 决定 |
| --- | --- |
| D-14 | **Council-default 的项目管理控制面由同一 Kernel 内追加式 `research.lifecycle.*` 驱动。** Codex Goal 只作为带来源与观察时间的外部事实进入；`CONTEXT.md`、活跃模块、声明边界、进度和唯一路线均由事件确定性投影。Git 提交与平台 Goal 完成不原子，因此采用 ready-to-complete → 提交无自引用证据 → 无剩余工作后完成平台 Goal → 后续恢复观察并调和的协议。该决定不改变 Council-default 科研路由、TianGong 第一权威、既有验收字节、fast path 或精确回滚。 |

D-14 把动态状态从稳定产品合同和不可变验收记录中移出，不改写 D-01–D-13 的历史语义。机器合同见 `docs/council-lifecycle-convergence-contract.json`，稳定产品规则见 `docs/council-default-product-architecture.json`。

## 持续自主 Research Director（D-15）

| ID | 决定 |
| --- | --- |
| D-15 | **Research Director 是科研项目的单一产品入口，并对边界内工作承担持续推进责任。** 它从项目原生 work item 编译来源绑定的 Council 路由证据，自动执行 fast path 或最小 Decision Cell，连续消费内部结果，并可自主重试、重规划、可逆回滚或停放失速的依赖分支。只有结果已验证完成、所有安全路线确实耗尽、主 Goal 改变，或触及凭证/登录、付款、合同与非标准许可、发布与公共口径、外部或共享写入、不可逆动作及无证据的强公开结论等外部责任边界时才停止。到边界前必须完成所有安全准备并登记零外部副作用的内容寻址交接。 |

D-15 不建立新的状态真相：Kernel 事件史、Artifact Bus、内容寻址、幂等、重放、恢复和既有精确回滚仍然承重；独立 worker 只能返回工件，不能接受项目事实。TianGong 继续拥有官方身份、版本、TIDAS、Provider、方法、工具和原生结果权威，多数意见不能覆盖官方事实。本决定定义默认控制流，不声称 Council 已被证明优于单 Controller。

## 稳定科研流程目录（D-16）

| ID | 决定 |
| --- | --- |
| D-16 | **重复且已验收的科研流程进入 Research Director 的机器可验目录。** 调用方只提交来源绑定的研究结果目标；Director 自行选择唯一适用流程，按白名单复用既有 Kernel 方法，逐步验收并以内容寻址收据恢复。首个迁移案例只覆盖已验收 LiFePO4 筛选比较闭环所需的七项稳定能力；旧方法和 CLI 保留为诊断/恢复端口，不再是默认用户路径。 |

D-16 不建立第二套状态机，也不把迁移包装成新的 LiFePO4 科学结论。流程收据只证明对现有事件与 Artifact Bus 工件的确定性选择、验收和恢复；TianGong 第一权威、Council-default、外部责任边界、历史字节和精确回滚均保持不变。当前证据只支持 LiFePO4 一个迁移案例，不能声称已经跨项目泛化。

## 真实任务驱动的能力泛化（D-17）

| ID | 决定 |
| --- | --- |
| D-17 | **以真实任务暴露缺口，再把通过的最小机制固化为稳定流程，并把结果评价内嵌在交付路径中。** 本阶段从一份 TianGong 原生 LCIA 结果出发，形成来源绑定的科学解读、机器包和独立验收，再用第二份精确结果做无代码迁移。外部工程模式的采用状态为 `implementation_inspected`，本地流程迁移为 `validated_in_project`；一次跨结果迁移不代表广泛成熟，也不支持 Provider 热点、跨单位大小排序或 TianGong 结果被独立重算等更强结论。 |

D-17 扩展 D-16 的目录，但不扩展用户入口：调用者仍只提交结果目标和权威来源，Research Director 内部选择流程、传递有界上下文并评价 outcome 与 trajectory。稳定目录现在精确支持 LiFePO4 七步闭环和 native-LCIA 三步解释，共十项白名单能力；它不是任意插件系统，也不建立第二套状态真相。

## LCA 科研 Agent 顶层架构重基线（D-18，2026-07-23）

| ID | 决定 |
| --- | --- |
| D-18 | **正式目标架构采用“单一研究控制主线 + 可替换模型与工具接口 + 独立 LCA 专业规则 + 唯一项目事实记录 + 横切质量与权限边界”。** `project_record` 是唯一被接纳的项目事实来源；模型对话、Council 讨论、工具原始返回和临时记忆只能提供候选或证据输入。Council 降为 Research Orchestrator 内部按需启用的策略，MCP 降为 Capability Gateway 下的一种连接协议，Skills 只承载专业方法或薄适配，具体案例只作为评测夹具。 |

D-18 是面向未来实现的产品与职责基线，不改写 D-01–D-17 的历史实现证据和验收记录。它对 D-13 的前向目标作出重基线：Council-default 仍是最后一个已经验收的运行基线和可恢复历史，但不再是目标产品的默认组织结构。现有 Research Kernel、Director/Controller、Council、Skills、TianGong 接入和案例代码都只是迁移候选，必须经过模块映射和新合同验收后才能进入目标架构；不得为了复用旧代码而改变 D-18 的边界。

正式的人看架构见 `docs/lca-agent-top-level-architecture.md`，机器合同见 `docs/lca-agent-top-level-architecture.json`。批准本决定不等于现有实现已经迁移完成，也不授权恢复暂停中的 NEV 或其他案例路线。当前 `CONTEXT.md` 仍是上一个完成 Goal 的追加事件投影；在新的迁移 Goal 真实激活并由生命周期事件重建之前，不手工伪造活动状态。

## 首拍工程结构

```text
Project Controller Agent
        |
        v
run-lca-research-project Skill     # 薄路由与公共命令
        |
        v
lca_research_kernel                # 状态、活动、幂等、投影
        |
        +--> TianGong CLI 0.0.25   # 原生 TIDAS context-pack
        |
        +--> outputs/<project>/    # 事件、投影、哈希工件
```

Kernel 的四个持久层：

- `research-events.jsonl`：追加式、无损事件历史；
- `project-snapshot.json`：可重建的完整当前态；
- `machine-context.json`：AI 路由所需的紧凑状态；
- `human-status.md`：目标、阶段、下一步、风险和术语的渐进解释。

命令执行以 Activity 为边界。稳定 `activity_id` 是幂等键；完成后登记每个预期工件的路径、字节数和 SHA-256。重复执行同一已完成 Activity 直接返回原结果，不增长事件历史。

机器投影对 artifact/decision 引用各保留最近 20 条，同时记录总数、完整 snapshot 指针和事件历史 SHA-256。任务增长只增加冷历史，不会线性膨胀活跃上下文；需要旧依据时再沿指针读取。

每个 Activity 必须声明 `local_read`、`local_write`、`external_read`、`external_write` 或 `irreversible` 影响类型。Kernel 在启动子进程前拦截后两类动作并生成授权请求；只有在人写入的、晚于请求且与 Activity/影响类型绑定的 `authorization.granted` 事件存在时才执行，同一授权不能跨 Activity 复用。风险记录本身不触发拦截。

## 外部优秀工程的机制迁移

| 来源 | 借鉴的工程机制/哲学 | 在本项目中的具体落点 | 有意不迁移 |
| --- | --- | --- | --- |
| [HumanLayer 12-Factor Agents](https://github.com/humanlayer/12-factor-agents) | Agent 应拥有控制流、上下文和暂停恢复；确定性软件包围有限模型判断 | `ResearchProject`、公共 CLI、机器投影与明确授权边界 | 不引入其应用栈或另一套运行时 |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 持久状态、可恢复执行、可审查的人机中断 | 追加事件、可重建投影、Activity 完成态和授权事件合同 | 首拍不再部署图运行时；TianGong 已有 LangGraph，可在服务化时对接 |
| [Anthropic Skills](https://github.com/anthropics/skills) 与 [Vercel Skills](https://github.com/vercel-labs/skills) | Skill 是渐进披露、可组合的薄能力合同 | `run-lca-research-project` 只路由 Kernel 公共入口；领域逻辑留在 Kernel/CLI | 不把运行状态和领域实现塞进 Markdown Skill |
| [TianGong LCA Skills](https://github.com/tiangong-lca/skills) | Skills 薄封装统一 CLI；权威 Schema/方法/规则由 context-pack 提供 | 调用 `@tiangong-lca/cli@0.0.25 dataset context-pack`，保存七个工件并固定合同内哈希 | 不重写 TIDAS Schema、方法学或运行规则 |
| [ARIS](https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep) 与 [Orchestra Research Skills](https://github.com/Orchestra-Research/AI-Research-SKILLs) | 研究管理与严谨性审查分责、claim/evidence 可独立追溯 | Controller 与未来短期 reviewer 分离；决定、风险、工件进入持久状态 | 不整包安装面向 ML 论文的多 Skill 工作台 |

这里的“迁移”是机制级实现，不是术语替换：每一项都对应代码中的状态结构、命令边界、哈希或路由责任；没有对应模块的外部做法不会被写成“已实现”。

## 首拍验收事实

- 删除三种投影后可以仅由事件历史重建出相同状态；
- 同一完成 Activity 重放不再次执行命令；
- 真实调用 TianGong CLI 0.0.25 生成 process context-pack 的七个工件；
- 固定 Schema、methodology 和 runtime ruleset 的合同哈希；
- 人看状态解释研究目的和术语，机器看状态只保留下一决策所需引用；
- 机器投影在 25 个 artifact/decision 下保持 20 条引用上限，而完整 snapshot 无损保留；
- 标记为 external-write 的 Activity 在授权前不启动子进程，授权后才完成；
- 已实现双路线 process build-plan、原生身份解析、过程物化和本地 schema/reference QA；尚未实现完整双路线清单、LCIA 和发布。

## 唯一下一纵向路线

第二拍已把原始研究范围文件、规范化变换和 SHA-256 来源绑定一起保存，再将 `1 kg 电池级磷酸铁锂正极活性材料`、从摇篮到工厂大门边界、中国地域和固相/水热两条路线编译为 TianGong 原生 process build plan。两份计划共享功能单位和 reference-flow identity，保留不同 technology route，并使用内容哈希作为 Activity/工件版本；计划重放会先核对已登记工件哈希，不重写输入，也不增长事件历史。

这一步迁移的是 TianGong `process-automated-builder` 的真实责任拆分：Agent/Kernel 做功能单位、边界、路线和 scaling basis 等科研语义决定，原生 CLI 只做确定性合同检查。本项目没有复制 CLI gate。CLI 0.0.25 对两份计划均确认 7/7 结构字段存在，同时返回 `identity_decision_missing` 和 `unit_of_analysis_not_automatic`；这是身份预检和性能/缩放证据尚未完成的真实边界，不是失败的实现，也没有被粉饰成 `passed`。

第三拍已实现一个组合但可重放的 release-gate resolution 活动：TianGong `dataset evidence-search` 规范化原始论文检索记录，Kernel 先把批准范围编译为属性/单位/协议/阈值/动作 machine policy，再对 147 与 135 mAh/g 做对称相对差计算（8.51%）；TianGong `identity-preflight` 为两条路线给出 medium-confidence `create_new`，随后两份内容寻址计划均由原生 build-plan gate 返回 `passed`、7/7、零 blocker。若证据显示不等效，Kernel 会记录 kg/1 kAh 路线缩放并继续到 service-unit plan，而不是停止。

这不是把原始范围悄悄改成通过。论文公开摘要没有 25°C、XRD 杂相和具体碳包覆条件，且只支持实验室候选；因此决定名为 `equivalent_with_protocol_deviation`。Kernel 无损保留原始“电池级”范围，并派生“1 kg 实验室碳包覆磷酸铁锂候选”的 `provisional_local_only` 有效范围。该 amendment 是独立内容寻址工件，并生成不同于原始范围的 process/reference-flow UUID 命名空间；被改写的功能单位与 technology route 逐字段绑定 amendment 和性能来源。两条 identity 决定也明确记录零候选来源；AI 可继续本地建模，但发布前必须用凭证重跑 TianGong 远程去重。

第四拍继续迁移 TianGong `process-automated-builder` 与 `lifecycleinventory-review` 的具体工程模块，而不是只借用“物化”“QA”这些词：Kernel 逐条调用 `process build-plan materialize`，严格回绑 process UUID、路线、名称、地域、参考年和定量参考 flow；再把两条 payload 合成内容寻址 JSONL，调用 `dataset validate --type process` 和 `qa process --logic-version v2.1`。原生报告、valid/invalid rows、规则发现、ruleset gate 和中英文 QA 文档全部登记 SHA-256，命令完成与 Kernel 语义验收仍分离。独立 ruleset-gate 工件必须与报告内嵌 gate 完全一致；清单完整性逐 process UUID 判断正值输入与输出，禁止用聚合总量掩盖单条空路线。

原生 materializer 会把计划中的 authoring 文本标成 `@xml:lang=en`，所以直接传入中文名称会产生六个语言 schema 问题；`Unknown` 也不是 TIDAS 的合法数据派生枚举。Kernel 的 v4 plan compiler 由此增加了受控英文 authoring copy 和 `Unknown derivation`，但无损中文 scope/amendment 仍是上游科研真相。物化后的首个数据源改为已经绑定 DOI 的性能论文；本地 scope、amendment、identity report 和 Controller assumptions 继续留在 evidence manifest，不再被错误写成 TIDAS 的 placeholder 数据源。

这一路径已真实生成两条 process，原生 materialize 均为 `passed`、零 schema issue，合并 `dataset validate` 为 2/2 valid。首次正式运行还发现一个跨组件问题：向 materializer 传绝对计划路径，会把本地路径写进 `common:generalComment` 并被 dataset validator 判为 placeholder。修复不是删除失败记录或后处理 payload，而是把 CLI 输入改为 workspace-relative path，并把 Activity 合同升级为 `v2-relative-input`；失败 Activity 留在事件史，新的内容版本独立通过。

`qa process` 当前给出两条 `process_missing_functional_unit` warning、零 blocker、`needs_review`。这是原生工具的真实合同差异：materializer 为 `Reference flow(s)` 写入 `referenceToReferenceFlow`，而 QA 同时检查 `functionalUnitOrOther`。Kernel 不伪造字段、不把 warning 隐藏成 passed，也不因此暂停；更关键的 QA 事实是原材料输入为 0、产品输出为 2，说明当前只是 schema-valid 骨架，不能计算 LCIA。

第五拍没有把“补清单”简化成几句估算。Kernel 新增了独立 inventory compiler：它校验来源身份、反应计量、kg 单位、路线覆盖、共产品和分配，再输出带 `flow_key`、方向、exchange 类型、数值、单位、source ID、推导方法、allocation basis、uncertainty status 和证据状态的候选边。固相路线得到 Li2CO3、Fe3O4、(NH4)2HPO4 和电力四条候选；水热路线得到 LiOH、H3PO4、FeSO4、Li2SO4、天然气热代理和过程水代理六条候选。22 g/L 先推导总反应混合液 43.767876301 kg，再扣除单列的 LiOH、H3PO4 与 FeSO4 共 2.039467829 kg 溶质，得到过程水代理 41.728408472 kg；计算全程使用未舍入计量值，并用回归测试保证水和所有溶质重新合计为溶液总量。

水热计划统一选择“未分配联合过程/1 kg LFP physical reference”基准：完整计量投入、0.696879122 kg Li2SO4 共产品和 0.589317169 LFP 质量分配因子同时保留，但分配尚未应用。Argonne 的 31 mmBtu/ton LFP 是质量分配后的 resulting energy，按短吨假设为 36.053 MJ/kg LFP；compiler 先除以质量分配因子，得到联合过程能源 61.177582978 MJ，防止把已分配能源混入未分配过程。报告摘录没有明确定义 ton，故同时保留 metric-tonne 敏感性而不把短吨解释包装成来源事实。固相反应的未量化气体产物被编译为 `not_quantified_no_cutoff_decision` 缺口和独立高风险项，不能被“已有正值输入/输出”自动清除。

科学上，这一计划只被命名为 `route-core-screening-proxy`。Argonne 的固相前驱体与 Hong 2012 性能实验不同；Argonne 水热模型是 200°C、5 h，而性能路线是 400°C、25 MPa 连续超临界水。公开记录把性能点表述为 6 wt.% carbon content，却未在本拍可访问证据中消除“相对 LFP”还是“相对最终复合物”的基准歧义；在 100% 碳保留这一理想假设下，两种解释分别给出 0.142493131 和 0.151588438 kg/kg 的蔗糖化学下界。因为基准、碳收率、吹扫气量、包覆能耗、排放和确切样品条件未绑定，二者都没有被写成 exchange。由此 `screening_exchange_patch=ready_with_high_risk_proxies`，同时 `performance_aligned_inventory=not_ready_source_gaps`、`comparative_lcia=prohibited`。

这拍对外部高星项目做的是模块迁移。openLCA 的 flow-first/add-exchange 命令边界被迁移为“先治理 flow identity，再写 process edge”；Brightway 的 production/technosphere/biosphere/substitution typed edge 模型被迁移为候选 exchange 类型与有序结构；Activity Browser 的可检查 exchange 表被迁移到人看投影。它们不替代 TianGong：最终确定性写入仍走原生 `dataset patch apply`，TIDAS 继续拥有数据合同。

清单来源同时经过 TianGong `dataset evidence-search run`。请求被明确绑定到 `processDataSet.exchanges.exchange` 与 `deep` profile；证据合同显式声明必须覆盖 Argonne LCI、Hong 2012 碳包覆比较、Hong 2011 连续超临界条件和 RSC 能源模型，Kernel 要求规范化 JSONL 对每一项保留精确 URL、标题和非空摘要，并核对报告 normalized/authoritative 计数。第一次真实回放暴露了幂等合同缺陷：仅以编译计划和搜索结果作为 Activity identity，会在 evidence-search 请求结构从字符串 field 升级为 TIDAS field object 后复用旧报告。Kernel 没有接受旧成功状态，而是记录 semantic rejection；修复后 identity 同时包含请求哈希、CLI 0.0.25 和 controller contract version，新 Activity 返回 field-bound/deep 报告并自动解决该风险。真实项目在 695 个事件上重放仍为 695。

第六拍把上述设计真正落到了 TianGong 数据对象，而不是由 Kernel 自己改 JSON。纯编译模块先读取两条已登记主 process 与已接受 inventory plan，生成两个新的、确定性 UUIDv5 场景 process；主 process 的路径和 SHA-256 被重新核对且不被改写。场景名称、功能单位、technology、completeness 和 reference product 明确写成“1 kg 未包覆路线核心筛查代理”，避免代理数据继承“性能对齐碳包覆候选”的语义。

在添加 process edge 前，compiler 先物化一张 11-flow/4-source 注册表。每个 flow/source 都有稳定本地 UUID、名称、单位、角色、来源和 `provisional_local_uuidv5_pending_native_identity_preflight` 状态。这是 openLCA flow-first 哲学的工程迁移，但还不是正式身份治理：下一拍仍必须让 TianGong 原生 identity-preflight 决定复用或新建。随后生成两个 patch set，共 32 个 `test/add/replace` 操作；28 个改变状态的操作都携带 `basis`、编译计划路径/哈希、原生证据报告路径/哈希、source IDs、推导、allocation basis、uncertainty gap 和 resolution context。TianGong `dataset patch apply` 负责确定性应用并另写逐操作 evidence JSONL，Kernel 只做目标行、操作计数、证据回绑和期望场景语义验收。

真实运行留下了两个有价值的失败 attempt。第一版水热 exchange comment 超过 TIDAS 500 字符上限；修复为短合同标识，把完整证据留在 patch-evidence，而不是删除证据。第二版给 `functionalUnitOrOther` 写了普通字符串，但 Schema 要求 `StringMultiLang`；修复为英文多语言数组后 2/2 通过，同时清除了两条 `process_missing_functional_unit` finding。随后对 TianGong `process-qa` 分类器做源码级核对，发现未带 kind tag 的化学品会进入 `other_input`，固相零 finding 因而是假象。Compiler 现在对 exchange type/direction/unit 使用显式白名单，并写成短 `tg_io_kind_tag`/`tg_io_uom_tag`，而不依赖英文名称猜测；未知组合 fail closed。Kernel 同时把原生 QA materialization summary 回绑到唯一场景 ID/version/input，并把 raw/product/energy totals 与 balance finding 回绑到 compiler edges；实际 command prefix 也进入 Activity identity，命令失败或语义失败均推进新 attempt。最终逐路线 QA 中，固相为四个正值输入、一个正值输出、原料代理 1.56050289 kg 对 1 kg 产品并保留一条 material-balance warning；水热为五个正值输入、两个正值输出、原料代理 43.767876302 kg 对 1.696879122 kg 产品+副产品并保留一条 material-balance warning。两条路线均为零 blocker。Warnings 不通过改数量消除：它们如实暴露固相气态产物与水热水/废物流尚未建模。

唯一下一纵向路线：对 11 个 provisional flow 和四个 source 运行内容寻址的 TianGong 原生身份预检，把 create/reuse 决定、候选覆盖和置信度回绑注册表，只修改隔离场景中的 identity references，再重跑 2/2 validation 与逐路线 QA。随后优先补固相气体与水热水/废物输出，并由同一 Controller 对碳包覆、排放、路线条件匹配、ton 单位和不确定性缺口做“对最终比较影响”的排序；排序是下一步选择，不是另一套状态真相。性能对齐主模型和 comparative LCIA 继续保持关闭。

第七拍把“身份预检”拆成了 TianGong 真正具备的模块，而不是用一个相似词覆盖两种实体。Kernel 对 11 个 provisional flow 各生成一个精确的 `flow identity-preflight` 请求，显式绑定 UUID、名称、`Product flow`、flow property 与 reference unit；请求、CLI 调用前缀和合同版本共同进入 Activity identity。每个原生报告都再次核对目标字段、绝对输入路径、候选源工件和 report hash，命令成功与语义接受继续分离。真实结果为 11 个 medium-confidence `create_new`、零 candidate、零 candidate source、零 blocker。由于本轮没有远程凭证，也没有本地候选集，该结果只允许下一拍构建本地 canonical flow row，不能被解释为全球唯一、远程去重或正式入库就绪。

同一版本的 TianGong CLI 0.0.25 没有 `source identity-preflight`。本项目没有把 flow 命令强行类比过去，而是保留一个可执行、可替换的能力缺口：先把四个来源规范化为三个 DOI 和一个 URL 主键，检查标识碰撞并保留确定性 TIDAS UUID；下一拍只组合已经存在的 `dataset contract get --type source`、`dataset validate --type source`、`dataset curation-queue --type support` 与 `dataset save-draft --type source`。在上游提供 source 原生消歧前，这条路径必须叫 exact-identifier binding，而不能叫 native source deduplication。

外部工程迁移也落在具体模块上。高星实体解析项目 [dedupe](https://github.com/dedupeio/dedupe) 与 [Splink](https://github.com/moj-analytical-services/splink) 都说明，记录链接不应从无约束的全量模糊比较开始；本项目迁移其 identifier-first blocking 哲学：DOI/规范 URL 先作为高信息量阻塞键，只有缺稳定标识或出现冲突的对象才进入概率/模糊候选解析，并保留 score、reason 与 candidate provenance。当前只有四个高质量来源，因此有意不引入其机器学习依赖或训练工作流。

独立审查后，预检合同增加了四类 fail-closed 约束：任何写入前限制 `flow_key` 为安全 slug 并再次检查 artifact root；flow 到 source 的引用必须闭合且 source 标识唯一；scenario rows 必须在运行前重新读取并核对 SHA-256；decision report、candidates JSONL、candidate-sources JSON 与 report.files 三条路径必须相互一致。远程请求只有在每个原生报告都带 `kind=remote_search` 的 candidate source 时才会被记为覆盖完成。原生 `manual_review/needs_review` 会被接受为真实结果并路由 AI 候选解析，但不会进入 canonical materialization；旧 remote/blocker 风险在后续干净结果中显式 resolve，且风险投影只保存计数和集合 hash。

这一拍还验证了双投影的 context 工程边界。完整 11-flow/four-source 决定仍留在事件和 snapshot 中；machine context 只保存决定计数、覆盖计数、source 标识类型计数及 flow report-set hash，使活跃上下文从约 76 KB 降到 64,997 bytes，而不是随实体明细线性增长。真实项目状态为 1042，重放 1042→1042；flow report-set SHA-256 为 `d693bb62407dc2e831a1a84db6d64eacf3749a3ea10a722d4565e4a0d9782524`，scenario rows SHA-256 仍为 `bf2f69fbc9f934f5f6713612638b7b58f6396639f78975bf5bbc80fc32e036bc`。

唯一下一纵向路线：把 11 个已接受的 `create_new` 决定编译为 canonical TIDAS flow build plan，经过原生 materialize/validate/QA 后取得受治理的 flow references；并通过上述 source 合同/校验/curation 薄组合生成可验证 source rows。随后只改写隔离场景的 identity reference，重复 2/2 validation 与逐路线 QA。远程候选覆盖、比较所需科学缺口、invalidation 和 assurance 继续作为后续节拍，不因本地身份预检通过而提前开放 LCIA。

第八拍已经把“本地 canonical row”落成 TianGong 可运行的数据对象。Kernel 没有自己写 Flow payload：它把 11 个已接受的 `create_new` 决定编译成带 exact-source evidence binding 的原生 Flow Build Plan，再逐条调用 `flow build-plan materialize`。物化结果由 Kernel 反向核对 UUID、名称、类型、版本、时间戳、定量参考和 Flow Property；随后 `dataset validate --type flow` 为 11/11 valid，`qa flow` 为 passed、零 finding、零 blocker。质量 Flow 使用 CLI 内建的 Mass UUID；Energy Flow 保留原生确定性占位引用并标记远程解析未完成，而不是虚构权威 UUID。

Source 路径同样遵守实际能力边界。CLI 0.0.25 的 `dataset contract get --type source` 只返回 Schema，没有 methodology/ruleset；Kernel 因而用三个 DOI 和一个 canonical URL 编译四个 TIDAS Source 草稿，并把缺失合同能力写入结果。四行全部通过 `dataset validate --type source`。Flow/Source 的 `dataset save-draft --dry-run` 分别 prepared 11/4、failed 0，没有发起外部写入；`dataset curation-queue build` 形成 4 source → 11 flow → 2 process 的 17 个任务，零 blocker。这个队列证明本地对象依赖闭合，不证明远程 support reference 或全球 duplicate coverage 闭合。

真实执行留下三类失败证据。第一版 source contract 活动错误假设所有 dataset 类型都有 methodology/ruleset；修正后合同显式允许 native source capability gap，但仍强制 Schema。第一版 Flow 物化把事件时间 `+00:00` 和六位微秒直接交给 native Schema，得到 `Invalid ISO datetime`；编译器现在统一生成 UTC 毫秒 `Z` 时间。后续合同加固还发现 manifest 的 Schema digest 不含 artifact 的展示换行；Kernel 现在用 manifest `bytes + sha256` 精确验证完整内容或去掉单个末尾展示换行后的内容。失败 attempt 保持不可变，新合同/新输入产生独立 activity identity。这正是 Runtime Harness 的职责：把组件差异固化成测试和合同，而不是在 prompt 中掩盖。

第八拍仍没有改写两条 scenario process，原始 scenario rows 哈希保持 `bf2f69fbc9f934f5f6713612638b7b58f6396639f78975bf5bbc80fc32e036bc`。本地 authoring 通过不等于允许远程保存：format source、owner contact、Energy flow property 和远程候选覆盖仍未闭合；也不等于科学清单充分，碳包覆、排放、路线条件匹配、不确定性与缺失输出仍保持开放。

唯一下一纵向路线：只把两条隔离场景中的 provisional flow/source UUID、version 和 URI 改写为已经验证的 `00.00.001` canonical refs，用 evidence-bearing TianGong patch 执行并证明 exchange 数量、来源依据和两条主 process 未改变；随后重复 2/2 validation 与逐路线 Process QA。通过后再按“能否改变路线比较结论”排序并实现最高影响的科学证据缺口。

第九拍完成了引用治理，而不是再次复制 process。纯编译模块读取已接受的两条场景、11 个 canonical Flow、四个 canonical Source 及其验证证据，解析每个现有 source reference 的 UUID、数字文件 URI 和标题，只有唯一匹配才允许编译。它为 12 个 Flow reference、12 个 exchange-level Source reference 和两个 process-level Source reference生成 122 个 `test/replace` 操作；仅 40 个真实不同的 UUID/version/URI 被替换，避免用 no-op 写入制造进展。每个 replace 都绑定 entity validation、Flow QA、curation 证据以及 local-only/remote-save-false resolution。

TianGong `dataset patch apply` 实际应用 40 个操作并生成 40 条逐操作证据。首个真实 attempt 因编译 payload 漏掉原生要求的 `patch_status=completed` 被 CLI 正确阻断；合同补齐后使用新 content identity 成功，失败记录保留。集成行哈希为 `d0c614e56d760bdf98bdf411b83af1896d4b98d9cec65ca023b4cc1aa68a1cee`。把三个允许变化的 reference subtree 替换成占位符后，前后完整场景哈希均为 `93d3cf50f0febbd769c0d51c37bb97a6c210bda9a3161bee6c3310693e1423bc`；因此数值、方向、分配、证据 comment、功能单位、场景身份及其他过程字段没有漂移。两条性能对齐主 process 的登记哈希也再次核对未变。

改写后 `dataset validate --type process` 再次 2/2 valid。逐路线 QA 精确复现原结果：固相和水热各一条 material-balance warning、零 blocker，raw/product/energy totals 与引用治理前一致。Warnings 继续说明缺失输出，而不是引用错误。旧的 provisional identity 风险因此关闭；remote duplicate coverage、format/owner/Energy support references 和 remote save 继续关闭。

唯一下一纵向路线：把碳包覆、排放、固相气体、水热水/废物流、路线条件匹配、ton 单位和不确定性等缺口做成 source-bound impact register；用“改变比较结论的能力 × 模型覆盖范围 × 证据可得性 × 可证伪性”选择一个最高影响且可执行的切片，完成证据/模型升级和受影响 validation/QA。排序是 Controller 的路由输入，不是新的平行真相。

第十拍把这条路由变成了可执行合同。`scientific_gap_impact/v1` 不把“最重要”和“现在最该做”混成一个分数：科学影响由结论影响 45%、模型覆盖 30%、可证伪/失效信息 25% 组成；执行优先再乘以 ready/partial/blocked 可执行因子，并加入证据可得性。两个分数都是透明的 1–5 序数输入经固定公式得到，只服务 Controller 排序，不代表因果效应量、概率、置信度或 LCIA 结果。输入 assessment 必须绑定当前 unresolved risk、canonical scenario rows、两条原生 QA、source-bound inventory plan 与 performance evidence；未知 risk/evidence/pattern、带 blocker 的 ready ticket、无可执行首项都会 fail closed。

真实 register 保留七个 ticket。科学影响最高的 route-aligned primary inventory、carbon coating 与 elementary emissions 都是 100/100，但证据阻塞使执行优先只有 24–28；它们仍出现在 `blocked_high_impact`，不会被“做不了”从科学事实中抹掉。当前选择 `mass-conserving-uncharacterized-outputs`：科学影响 91、执行优先 92.8。第二个 ready ticket 是 ton-unit sensitivity（72）；敏感性与不确定性整体仍禁止，因为当前没有 source-bound 参数范围、先验、似然或 comparison-ready response model。

外部迁移同样落到模块合同。采集日 2026-07-16，[SALib](https://github.com/SALib/SALib) 为 994 stars，[PyMC](https://github.com/pymc-devs/pymc) 为 9676 stars。SALib 的 ProblemSpec 与 sample/evaluate/analyze 分层落点是未来的 parameter registry、model evaluator 和 method-specific analysis artifacts；PyMC 的显式 prior/likelihood/observed/posterior/predictive 分层落点是 source-bound probabilistic model artifact。当前只迁移 `readiness_contract`，不运行算法：缺证据时拒绝执行本身就是工程迁移的一部分，而不是只把术语换成 LCA 名称。

首次真实活动还暴露了工作目录边界：Kernel activity 在项目 workspace 中执行，直接使用 `python -m lca_research_kernel` 无法解析仓库模块。失败执行报告以 SHA-256 固定且未覆盖；修复后调用仓库内编译器的绝对路径，并在独立 attempt 目录成功。相同输入重放保持 1291→1291，证明失败证据与成功产物都可恢复。

唯一下一纵向路线：对两个 canonical-reference 场景编译守恒残差输出，只把可由现有量化清单严格推出的 residual mass 写成 `uncharacterized` 输出；不得把它命名成具体气体、废水污染物或可表征 elementary emission。用 TianGong patch apply 写入后重跑 2/2 validation 与逐路线 Process QA，并把日后 species-resolved evidence 到来时的替换/失效路径留在 patch evidence 中。

第十一拍完成了这项模型升级。纯编译器只识别显式 QA tag、定量参考和 kg/MJ 口径，把已接受的 raw-material kg 减去已接受的产品/副产品 kg；mean/resulting amount、场景与 QA 总量、过程 ID 和 exchange internal ID 任一漂移都会 fail closed。得到的两个残差为：固相 `1.56050289 - 1 = 0.56050289 kg`，水热 `43.767876302 - 1.696879122 = 42.07099718 kg`。能源 2.94239/61.177582978 MJ 保持单列，不进入质量守恒。

这两个数量没有直接写进 process。Kernel 先为 route-specific residual Flow 生成确定性 UUID，请 TianGong `flow identity-preflight` 决定身份；两者均为 medium-confidence local `create_new`、零 candidate/source/blocker。随后原生 `flow build-plan materialize` 生成 `00.00.001` Product Flow，2/2 dataset validation 和 Flow QA 均 passed、零 finding/blocker。`Product flow` 在这里是临时 technosphere carrier，避免虚构 elementary compartment 或未知的 treatment/disposal 去向；名称、general comment、generation manifest 和 exchange resolution 都声明 composition/phase/disposition unknown，不能把它称为真实产品、共同产品、废物流或排放。

场景 patch 只包含每条路线三个 `test` 和一个 append，共八个操作、两个真实写入、两条逐操作证据。新增 exchange 绑定 gap register、旧 QA、守恒公式、新 Flow validation/QA、unknown-composition 状态与 `species_resolved_primary_or_validated_engineering_evidence` 替换触发器。新 rows SHA-256 为 `f056c2deffc2604073ba30c7332064feaaa6a1ce236d90e641b31be7d0f13dd3`；移除仅由本合同标记的两个 append 后，前后保护哈希均为 `6f3dc63d605f042bf8c1b289cd64dcfc3dcbfe8b81a753fd32378b31a581e158`，所以所有旧字段和两条主 process 保持不变。

TianGong process validation 为 2/2。固相 QA 现在 raw/output 均为 1.56050289 kg，水热均为 43.767876302 kg；两条路线均 passed、零 finding/blocker，能源仍按原值排除。这个 QA pass 只证明“当前代理模型中的 kg 输入有显式去向”，不证明物种守恒、元素守恒、排放完整或 LCIA 可计算。`inventory-unquantified-output` 因而没有关闭，而是更新为“质量已量化、组成/相态/去向/characterization 仍未知”。

首个 patch attempt 的原生报告和输出其实都正确；Kernel 误把 residual compiler 的紧凑 canonical JSON hash 与 Project pretty JSON hash 跨序列化实现直接比较，导致 harness false rejection。失败报告 SHA-256 `388d347b6dbe83f3af9c190a018f1626f192279277aa52ac8446f43d945ff182` 保留。修复改为比较同一 residual contract 生成的 `protected_scenario_sha256_before`，没有放宽任何字段保护或证据检查；attempt 2 成功。相同命令重放保持 1403→1403。

唯一下一纵向路线：使用 inventory plan 已同时保留的 short-ton 与 metric-tonne 数值构建一对隔离能源情景，只改变受 `ton` 解释影响的 energy exchange，计算并报告逐路线能源差值/比例与排序稳定性，不运行 LCIA，也不把这一个确定性 bracket 冒充全局敏感性分析。

第十二拍完成了这个确定性解释区间。Kernel 从 source-bound inventory plan 重新读取两条路线的原值、公吨端点、共同换算常数、来源和 allocation basis，并要求两个 route 使用同一 `907.18474/1000 = 0.90718474` 换算比例。当前 residual-output 场景还必须各自存在且只存在一个带显式 QA tag 的 MJ energy input；mean/resulting amount 任一漂移都会 fail closed。

短吨端点是 test-only patch：每条路线测试 process UUID、version 和两个 energy amount，共八个原生操作、零写入、零证据；输出 SHA-256 仍为 `f056c2deffc2604073ba30c7332064feaaa6a1ce236d90e641b31be7d0f13dd3`。公吨端点包含同样八个测试，再把两条路线的 energy mean/resulting amount 各替换一次，共十二个操作、四个写入、四条逐操作证据；输出 SHA-256 为 `a5adaf11b03a7cea3f197b1eb90199aab7f868cf569106537d17c2e9eff451ea`。两端都通过 TianGong 2/2 Process validation。

固相从 2.94239 降至 2.669291307 MJ，差值 -0.273098693 MJ；水热从 61.177582978 降至 55.499369707 MJ，差值 -5.678213271 MJ。两者都是 -9.281526%，所以按 process energy 降序的 `hydrothermal > solid-state` 在两个端点保持不变。这里的水热数值继续保持 source-bound 的 unallocated joint-process basis，不能擅自与已 mass-allocated 的报告值混用。

保护投影把且只把两个 energy exchange 的 mean/resulting amount 置为占位符；原场景、短吨端点和公吨端点的保护哈希均为 `084650c229442309c766b5222ab42f4c4fd0b89f1ad498d971ca2a0b8dc97886`。因此残差 Flow、其他 exchange、引用、证据、功能单位和主 process 未改变。首个真实 attempt 还验证了 TianGong 的 root-level `patch_status=completed` 前置合同：缺失时原生工具以 blocker `ai_patch_status_not_completed`、零 patch/零写入拒绝，报告 SHA-256 `303e03c7101fddb41fcd40edd7c7abc4910092bd052f7368cca33566cfdc62d7` 保留；回归测试固化后新 content version 成功。

这个完成状态叫 `deterministic_one_factor_interpretation_bracket`。它量化了一个来源单位歧义的两个离散端点，不提供参数分布、全局敏感度指数、概率区间、因果效应或任何 LCIA 结果。唯一下一纵向路线：把 residual 与 ton-unit 两个已完成切片写回 gap register，再为当前双场景 lineage 编译 source/artifact 变更到下游失效范围的机器合同。

第十三拍完成了 gap progress overlay 与轻量失效传播。原始 `scientific_gap_impact/v1` 作为不可变科学排序继续保留；新合同不改写它，而是把两条 `scientific_gap.executable_slice_completed` 决策和各自 completion artifact 叠加进去。`mass-conserving-uncharacterized-outputs` 与 `ton-unit-sensitivity` 因而成为 completed；剩余五个 ticket 全部保留原科学影响、执行分和 blocker，`ready_scientific_gap_count=0`、`selected_scientific_gap=null`。这样 Controller 不会因旧 register 仍含 ready 字段而重复选择已完成工作，也不会把阻塞项伪装成可执行。

当前 content-addressed lineage 有 11 个节点、10 条有向依赖边：base gap register、inventory plan、canonical-reference scenarios、residual Flow validation/QA、residual scenarios、ton-unit contract、两个 ton 场景及各自 Process validation。每个节点只保留 path/hash、current 状态和一个 rebuild action；历史产物不复制到 Hot State，也不会在失效时删除。产物 SHA-256 为 `cf5815221dfa1ffd77008111acd324df5de1c5eff35750050b9cd7f5dbf380bb`。

受控模拟把 `ton-unit-contract` 标记为 changed，只传播到 `ton-scenario:metric-tonne`、`ton-scenario:reported-us-short-ton` 及两份对应 validation；`residual-scenarios`、canonical-reference scenarios 和上游 plan 历史不受反向连坐。合同还声明 inventory-plan、canonical-process、species-resolved residual evidence 与 reported-ton interpretation 四类 change route，运行时可用同一个 BFS 产生精确 stale/rebuild 队列。

这层不是重型审计：它不持续复制 context，不为每次读取写事件，也不把风险变成暂停门；只有内容哈希实际变化时才计算传递闭包。唯一下一纵向路线：为 100/100 科学影响但 evidence-blocked 的 primary inventory、carbon coating 与 elementary emissions，以及相关 performance full-text，编译合法来源获取路线；在新证据到来前保持比较、LCIA 和概率不确定性关闭。

第十四拍把上述路线变成了 `evidence_acquisition_manifest/v1`，而不是停留在“继续找论文”。五个剩余缺口必须全部落到直接来源目标或 dependency-only 路线；当前两个 DOI 目标共定义 15 个字段验收合同，覆盖 headline capacity 到样品行映射、测试温度、XRD/相纯度、碳含量基准、蔗糖投入与产率、保护气、包覆热过程与排放，以及两条工艺的材料、能量、水、产率和 species-resolved output。每个字段只接受带页码/表格/跨度的报告值或保留原值、单位、公式和假设的 source-bound 推导，缺失不会变成零。

获取链被拆成五种不同状态：来源 item、合法 attachment 与访问依据、版本化 parse report、page/span-bound extraction、accepted/rejected/missing field decision。这个对象模型迁移自 Zotero 的 item/attachment/annotation 分离；GROBID 只作为 attachment SHA → TEI/layout/parse-report 的延迟适配器；PaperQA 只迁移 manifest/index reuse 与 citation-aware passage contract。按 2026-07-16 的 GitHub 元数据，三者分别观察到 14,710、4,996、8,883 stars；桌面应用、完整 agent 栈和 parser 服务均未被虚报为已部署。

当前可自动继续的是 Crossref DOI metadata、OpenAlex 全 locations、Semantic Scholar OA check 与 exact-title repository search 四条 public-read 路线。两篇目标论文在 OpenAlex 均为 closed、无 repository full text/best OA location，Semantic Scholar 也未给出 OA PDF，因此登记四条 dated no-hit。`no-hit` 的机器语义是 route-and-date-bounded discovery result，不是科学不存在证明。GROBID 与 passage extraction 分别等待合法 attachment 和 parse report；凭证/付款/许可与作者联系属于人承担责任的路线，但 `pause_internal_work=false`。

每个来源目标同时绑定 inventory-plan、canonical scenarios、residual Flow validation/QA 等 changed nodes，并通过现有 DAG 预计算 stale descendants；新证据真正验收前不触发重建。该拍产物 SHA-256 为 `7b5a573965c035c377283316eafb02b2e675af5625409cf492ba2c2f76470f79`。唯一下一纵向路线：把四条 public discovery 变成可重放、标准化、内容寻址的执行活动，避免 case 文件中的手工观察成为不可复现事实。

第十五拍实现 `public_evidence_discovery/v1`。Kernel 从已登记 manifest 构造且只构造 public route 请求；每个 target/route 都保存 request identity、URL、时间、HTTP 状态、attempt count、原始 response SHA 和 JSON payload，即使网络失败也留下 error envelope。规范化层独立验证 requested/returned DOI 与 title，分类 publisher metadata link、OA location、repository location 和 OA PDF candidate；没有任何科学字段从 metadata 自动进入模型。

真实活动共执行八个请求。两条 DOI 的 Crossref、OpenAlex single-work 和 OpenAlex exact-title search 均成功，合计 6/8；Semantic Scholar 在两条 DOI 上均连续三次返回 HTTP 429，所以标记为 degraded route，而不是伪造 no-hit 或停止项目。成功路线对两篇论文都确认 identity，产生四条 dated no-hit 和零个 lawful attachment candidate。第二次 bounded retry 仍为 429，因而当前 next route 是扩展其他 public repository/author-hosted sources，同时保留 Semantic Scholar retry queue。报告 SHA-256 为 `72c33f611a589d40b1c93d2c5c339c256fd65b3163baef882a386a2a11f73deb`。

默认 `execute-public-discovery` 会重放当前报告，不增长状态；只有显式 `--retry-failed` 才产生新的 bounded attempt。这既保留幂等，又避免一次限流把 Controller 困在无限重试里。credential/payment/contact 路线在本拍完全没有执行，`scientific_field_evidence_admitted=false`、comparison/LCIA 仍关闭。唯一下一纵向路线：加入新的公开、合法、无需凭证的来源发现类，并把任何候选 URL 送入独立 attachment ingestion gate，而不是直接解析。

第十六拍没有改写第十五拍，而是新增 `public_source_expansion/v1`。它把 manifest 与首轮 report 都作为 path/hash 输入，分别执行 OpenAIRE Graph v4、Europe PMC 和 HAL exact-DOI route。这样首轮 DOI 元数据/OA 检查与第二层官方仓储聚合保持独立重放，新增来源不会改变旧 activity 的语义。

真实运行的六个请求全部 HTTP 200。OpenAIRE 为两篇论文各返回一个 DOI/title 精确记录，但 access right/instances 只指向 closed publisher/DOI 位置；Europe PMC 与 HAL 对两篇均没有 exact record。因此六个 route 都是“当前路线没有公开附件/位置”的 dated no-hit，直接 attachment 和 public location candidate 都为 0。OpenAIRE abstract 只登记 SHA-256 与 `admitted_as_field_evidence=false`，没有进入 15 个字段。

精确记录仍带来可执行的新信息：五个 target-bound ORCID anchor（Jaehoon Kim、Kyung Yoon Chung 在两篇重复，加 Byung-Won Cho；三位 distinct people）和六个 target-bound institution anchor（四个 distinct organizations）。这些锚点只允许下一拍进行公开 ORCID/机构仓储发现，不构成 contact authorization。第一次真实 activity 还暴露了 harness 问题：`run_activity` cwd 是 project workspace，`python -m lca_research_kernel...` 无法找到源码包；失败证据保留，回归测试固定非仓库 cwd 后改回既有 absolute-script boundary，attempt 2 成功。报告 SHA-256 为 `42a0158d28a6dd8efbb7693118eeef0b66f8f8d33854ee4f8bec83ed2fbf936d`。

唯一下一纵向路线：把精确 ORCID 与机构名称编译成新的 public-only source targets，查询 ORCID public works/links 和机构公开仓储，同时继续拒绝 citation-only、同名误匹配与未验证附件。

第十七拍新增 `author_institution_public_routes/v1`，没有把多跳路线塞回 Beat 16。它把五个 target-bound ORCID anchor 去重成三位作者、把六个机构 anchor 去重成四个组织，同时保留每一条论文绑定。由此编译九个 ORCID `person`/`works`/`employments` 读取和四个 target-bound 机构仓储搜索；学会组织没有可验证机构仓储，因此明确登记为 unresolved mapping，而不是编造搜索端点。route manifest SHA-256 为 `0f87052056a498e5e14588bcbb9c35ed98a66e5b30da9728e4510b752c744131`。

真实活动共执行 15 个 public reads（13 个 primary、2 个精确结果 detail），成功 14、失败 1。三位作者的 ORCID 公共姓名都与 exact anchor 一致；两篇目标论文分别出现在相应作者的 public works 中，形成五个 author-target bindings。标题规范化只接受已观察到的化学式展示差异：`LiFePO4`、`LiFePO(4)` 与 `LiFePO<inf>4</inf>`；DOI 仍必须精确。ORCID employment 与 identity 分开：只有 Jaehoon Kim 有一条 current/undated Sungkyunkwan University，不能反推 2011/2012 目标期隶属。

KIST official DSpace 为两篇都返回 exact DOI/title detail：`https://pubs.kist.re.kr/handle/201004/130758` 与 `https://pubs.kist.re.kr/handle/201004/128984`。两页都有元数据/摘要，但没有 public bitstream 或 `citation_pdf_url`，所以 exact record=2、attachment candidate=0。Korea University exact search 为 no-hit。SNU 在一次 public JavaScript cookie challenge retry 后仍返回 challenge，因此是 degraded route，不是 no-hit。前三个 immutable attempts 分别暴露空导航链接、化学式下标表示和 `javascript:` 导航链接三个适配缺陷，均由失败回归样本固定；attempt 4 接受。报告 SHA-256 为 `cddafe8e7843da24053307a116a96fd4744fde484458baf9dbc3cf5afdd6bf95`。

下一纵向路线不直接解析摘要，也不把 KIST handle 当全文，而是对两个 exact handles 编译受控 export/OAI/bitstream probe；只有 DOI/title 仍精确且公开文件链接属于同一记录时才进入 attachment byte/hash gate。SNU challenge retry 保留，但不压过已经出现的前向精确记录路线。

第十八拍新增 `exact_repository_record_export_probe/v1`，把 Beat 17 报告作为 path/hash 输入，没有回写或扩大作者/机构模块。它只选择两个已接受的 KIST 精确 handle，先验证 `mode=full` 页的 DOI/标题，再要求页面上的 DC/RIS/XLS 三个导出链接同 host 且共享一个数字 `item_id`；2011 记录解析为 `132241`，2012 记录解析为 `130467`。这迁移了高星 DSpace 工程中 item metadata、OAI 与 bitstream 分离的真实模块边界，而不是把“仓储导出”语义改名成“论文附件”。

真实活动的 12 个 public reads 全部成功。两条记录各有 DC XML、RIS 与旧式 Excel 元数据工作簿，共六个 export；DC/RIS 的 DOI/标题连续性通过，XLS 的复合文件签名通过，但三者都没有正文媒体、页码或表图定位，因此只登记为 metadata export。两个 handle-derived 和两个 internal-item-derived OAI `GetRecord` 都是 HTTP 200，但 XML 均含 `idDoesNotExist`。Kernel 按 OAI-PMH 规范将其计为四个 semantic error、零 OAI record success，固定了“传输成功不等于协议成功”的回归合同。

两个 full record 都没有 public bitstream 或同记录 PDF 链接，所以 metadata-only record=2、attachment candidate=0。SNU 的一个挑战失败作为独立 retained retry 保留，本拍没有反复请求它，也没有让它覆盖已经完成的 KIST 探测。Manifest SHA-256 为 `528b293720526ceed9c132aa2931bbb6f3731dab1981d48784db2d615fd04b3e`，report SHA-256 为 `b3a391fbfd99eb1dec648df8f22ba9d23b3a7fef4f8df887d437416e33522c31`。下一纵向路线是把两个精确 DOI/标题编译为受控的韩国国家级学术来源路线；只有新出现的同一论文公开文件才进入附件验收，摘要和书目数据仍不得进入 15 个科学字段。

第十九拍新增 `korean_scholarly_source_routes/v1`。它没有把“韩国来源”简化成对所有网站做相同关键词请求，而是先编译六条 reviewed capability：ScienceON 论文 API 和 RISS 海外论文 API 与目标相关，但稳定机器调用都要求申请并绑定 key，因此登记为 `credential_required`；KCI 与 KoreaScience 的内容范围不对应这两篇 Elsevier 外刊，因此登记为 `scope_inapplicable`，不制造无意义 no-hit；SKKU Pure 的 2012 精确记录和 Korea University Pure 的 2011 精确记录才编译成 public GET。

两个 Pure 读取都成功，`citation_doi` 与 `citation_title` 精确匹配。适配器把 DOI、Scopus、fingerprint、引用格式、作者页和摘要保留为 metadata/related link，只接受 Pure 明示的 `citation_pdf_url`、`document-link` 或 file representation。真实页面均没有这些文件表示，因此 exact CRIS record=2、metadata-only=2、attachment candidate=0。摘要只登记 SHA-256，没有进入字段合同；SNU 的降级路线继续独立保留。

该拍把来源覆盖、访问能力和文件语义拆成了可执行注册表，而不是审计式阻塞：需要 key 的两条路线仍可由人日后授权，但 `pause_internal_work=false`；不适用路线不会进入 retry；公开路线默认重放不再请求。Manifest SHA-256 为 `5c00709a4c37a2509ccd116edbbf1c439c7ae68be6a5f8ae4cffc7a2618068b8`，report SHA-256 为 `5aea25fed7d4740db0b5b829a20bb06aaaacab79b9f426983b94648b8a25d884`。下一纵向路线转向精确作者的公开 profile/hosted-file 表面，但不得联系作者，也不得把 CV、引用论文或同名文件误当目标附件。

第二十拍新增 `author_hosted_public_file_routes/v1`，同时把第十七拍的三位精确作者身份和第十九拍的当前目标状态作为 path/hash 输入。它没有把搜索结果变成无边界爬虫，只编译四条经过审查的公开路线：金在勋的 SKKU Pure 精确作者页、该页链接的 GEMP 官方实验室入口、实验室域名上的金在勋公开 CV，以及 KIST 官网的 2009 研究人员专题 PDF。每条路线都固定作者 ORCID、目标绑定、主机/重定向 allowlist、预期媒体类型、预期文档角色、发现依据与公开访问依据；没有联系人字段、登录或外部写入。

真实活动四次读取全部成功。两个 HTML 页面保持在审查主机并包含预期身份/实验室链。两个 PDF 分别是 280,295 字节的金在勋 CV（SHA-256 `2220154b7af462378f85dec99b05a6537dc577144795e2e91f1cb08c5b7a916c`）和 2,511,054 字节的 KISToday 机构专题（SHA-256 `18e0477cf6a4001503ff6984d2ffd61636acb399bdb128b59e35dffcfe84a588`）。二进制文件写入独立 immutable artifact，JSON envelope 只保留请求、重定向、媒体、哈希、长度、角色和访问状态；机器 context 不复制 PDF 或 HTML 正文。

这一拍用真实反例固化“文档角色先于字符串命中”。独立 PDF 文字与视觉检查确认金在勋 CV 的发表列表同时包含两篇目标论文标题，但它的父路线和文档结构已经证明 `document_role=author_cv`。因此两个命中被记录为 `citation_or_listing_only`，不能进入 target-paper attachment；KISToday PDF 展示 Kyung Yoon Chung 与 Byung Won Cho 的机构介绍，但不含目标标题，也不能被记成全文 no-hit。四个观察都为 verified non-target document，citation-only occurrence=2，attachment candidate=0，scientific absence 仍未证明。

工程实现迁移了三个高星项目的具体边界：Zotero 的 bibliographic parent/child attachment 和独立 content type/file state 变成本地 parent route + hosted-file observation；GROBID 的 document segmentation 思路变成“只有已通过 target-paper role gate 的 PDF 才能进入后续解析”；DSpace 的 item/bitstream 分离变成 binary/envelope 双工件。迁移落在模块、route manifest、二进制布局、回归测试、事件和双投影中，没有部署或虚报完整外部应用。

Manifest SHA-256 为 `24fb99136ff70d18250bc6143035420635f6fc5d3baec38417ffd9bcda6b4833`，report SHA-256 为 `22deb21b3a4877012dd2dfe6a79a916a42426f0acf7de5ebd4296c91a57ead11`。ScienceON/RISS 凭证路线、SNU 降级路线和 publisher/institutional access/optional contact 人工责任路线都保持原状态，没有执行。唯一下一纵向路线是把剩余凭证、许可访问和可选联系路径编译成责任感知的 acquisition option package，同时继续寻找不依赖全文的内部模型加固工作；编译选项不等于申请凭证、付费、接受许可或联系任何人。

第二十一拍新增 `responsibility_aware_acquisition_options/v1`。它把此前分散在来源注册表和责任边界里的动作编译为五个真正可恢复的选项：ScienceON key、RISS key、机构图书馆访问、出版方购买/许可、经人批准的作者或数据提供方联系。每个选项都含 effect type、前提、目标论文、可覆盖字段、风险、provided route、deferred/declined fallback 和类型化 resume schema。凭证路线只增加 discovery 能力，直接字段收益固定为 0；全文/联系候选虽然绑定全部 15 个字段，也必须再过附件或回复验收门。模板禁止项目状态出现 API key、token、cookie、密码等值，只允许 opaque secret reference 或 path/hash/bytes/version/access-basis 工件绑定。

这不是一组新的审批门。五个选项均为 `available_human_action_not_started`，`external_action_executed_count=0`，且 `human_options_wait_independently=true`、`pause_internal_work=false`。Kernel 不代人申请凭证、操作登录会话、付款、接受许可或发送消息；人以后提供合规输入时，可用对应 template 恢复某一支，不影响其他支和当前内部路线。

同一合同从 hash 固定的双路线 residual scenario 检查出 2 条场景、14 个正数量交换和 9 个化学式候选，自动选择 `compile-elemental-stoichiometric-consistency-bracket`。这条路线能够在没有目标全文时检查 Li/Fe/P 等元素与反应计量，但必须保留原料纯度、磷酸溶液浓度、LiOH/Li2SO4 水合态、FeSO4 水合态与氧化态风险；`residual_species_reclassification_allowed=false`、`model_mutation_allowed=false`，因此不能用看似配平的反应把 composition-unknown residual 改成具体物种。报告 SHA-256 为 `31c39c83825d4aad7877a03b8e84dd2b5cc86fff2f3e25a868ff5d098431d231`。

工程机制参考不是语义改名：LangGraph 的 checkpoint/thread resume 变成 package hash + typed resume input；Prefect 的 pause/run-input stable key 变成 option ID、provided/deferred/declined 与幂等恢复分支；Temporal 的 workflow/activity/effect 分离变成 Kernel 编译本地合同而把 credential/payment/license/contact 留在外部责任 effect；pymatgen 的 composition 表示思路变成下一拍的化学式→元素计数边界。外部项目没有被部署成产品依赖，迁移结果落在模块、CLI、事件、模板工件、测试和双投影中。

第二十二拍新增 `elemental_stoichiometric_consistency/v1`。Kernel 从已接受 option package 重新验证 residual scenario hash，解析 9 个化学式候选，把 kg 按显式原子量表转换成 kmol，并分别计算两条路线的 input minus known-output 元素余额。固相路线检查 Li/Fe/P，水热路线再加入 S；二者的纯物质/无水/名称即化学式基准最大核心元素失配均小于 `1e-7 kmol`，元素余额折算质量与已登记 residual 的差绝对值均小于 `1e-12 kg`。这说明当初的 screening amounts 在其生成公式下算术自洽，也揭示它们极可能由同一配平方程派生；它不是新的来源证据。

水热路线又运行 LiOH 单水、FeSO4 单水、FeSO4 七水和 Li2SO4 单水四个离散结构探针，四个都触发 core-element mismatch。因为没有原料纯度、磷酸溶液浓度、水合态和 Fe 氧化态的来源区间，报告拒绝把这些点包装成上下界，登记 `unbounded_pending_formula_basis_evidence`。即使基准余额的 H/O 质量恰好等于 residual，也不得把 composition-unknown residual 重命名为水；元素质量闭合不证明物种、相态、环境区室或反应机理。

实现把 pymatgen 的 Composition 思路迁成 formula→element/count 的确定性边界，把 Pint 的单位边界迁成显式 kg、g/mol、kmol 转换，把 Great Expectations 的 expectation/result 分离迁成带观察值、容差、状态和诊断的 route checks。没有添加外部运行依赖。第一次真实 activity 因绝对脚本中的相对 import 失败并被不可变保留；模块改为 standalone contract constant 且增加直接脚本回归测试后，attempt 2 通过。接受报告 SHA-256 为 `79bf469c0658e6d850f0c7c02d89e10a27815d7f97cbf02ca4f6696beaf90607`，下一路线是汇总 route-scoped model-assurance claim matrix。

第二十三拍新增 `route_model_assurance_claim_matrix/v1`。Kernel 先把 residual scenario/validation/route QA、ton contract、lineage、acquisition package 和 stoichiometric report 编译成一个 8 项 path/SHA-256 source registry，再生成 26 条稳定论断：8 条 `verified_executable_fact`、4 条 `conditional_screening_fact`、8 条 `unresolved_evidence_dependency`、6 条 `prohibited_claim`。每条路线适用 4/2/4/6 条；共享禁止项同时作用于两条路线。矩阵没有 readiness score，也没有用通过原生校验的事实抵消缺失科学证据。

每条 claim 都有 route、statement、plain-language meaning、evidence refs、invalidation triggers 和 rebuild action；条件事实另有 assumptions，缺口与禁止项另有 release conditions。因而“场景 2/2 原生有效”“Process QA 零 blocker”“composition-unknown 质量闭合”“lineage 可寻址”能与“ton 解释”“纯/无水化学式闭合”明确分开，后两者不能升级为身份或比较证据。物种化残余、已表征 elementary emissions、完整清单优劣、比较 LCIA、全局/概率敏感性和发表就绪六类论断继续禁止。

截至 2026-07-17 的 GitHub API 检查记录 dbt-core 13,461 星、Great Expectations 11,648 星（当前仓库重定向到 `fivetran/great_expectations`）、OpenLineage 2,544 星。OpenLineage 的 run/job/dataset facet 分离迁成 claim/evidence/invalidation 分离；Great Expectations 的 expectation/result 边界迁成稳定论断定义与当前观察结果；dbt manifest/depends-on 迁成 claim ID、依赖、释放条件和重建动作。没有添加外部运行依赖。接受报告 SHA-256 为 `f79786f1fdcab47d518d008851bbf5194a7046980a5780c14cb4a8959459cdb4`，下一路线是 claim-to-evidence replacement map。

第二十四拍新增 `claim_evidence_replacement_map/v1`。它同时读入 26-claim matrix、原始 15-field acquisition manifest、五条责任感知 acquisition options 和 11-node/10-edge lineage DAG。每个字段都形成一条双向 route：accepted evidence forms、可直接覆盖它的 option、受影响 claim、changed nodes、由 DAG 算出的 stale descendants、补集 preserved nodes，以及按顺序执行但尚未执行的 evidence registration、node rebuild 和 claim-matrix recompile actions。

15/15 字段都有当前精确节点入口，18 条 conditional/unresolved/prohibited claim 都至少反向链接一个字段；8 条 verified claim 单独保留历史与复验触发字段。ScienceON/RISS credential 的 `directly_addressable_field_ids=[]` 被编译器强制验证，因此不会借“有 key”虚假释放科学字段。机构附件、出版方附件、可归因回复虽可进入路线，仍保持 evidence_admitted=false、claim_released=false、model_mutation_executed=false。

当前首个干运行字段是 `candidate-specific-route-inventory`，沿用 scientific-gap register 已有的 100 impact / 28 execution priority，不制造新分数。其 changed nodes 是 `inventory-plan` 和 `canonical-reference-scenarios`；图遍历得到 8 个 stale nodes、3 个 preserved nodes，history_deleted=false。GitHub API 同日检查记录 Bazel 25,629 星、DVC 15,754 星（重定向到 `treeverse/dvc`）、OpenRewrite 3,596 星：Bazel 的 affected target、DVC 的 deps/outs selective repro、OpenRewrite 的 explicit recipe 分别迁成 changed/stale traversal、preserved history/ordered rebuild、stable action ID。接受报告 SHA-256 为 `59730f87a52019b749f81bea03f546f4e248ac007dbcaa5980d852ba328b78bd`。

第二十五拍新增 `evidence_replacement_dry_run/v1`。它只接受 replacement map、claim matrix 和 lineage report 的内容寻址绑定，并由合同版本、scope、field 与上游哈希确定性生成 `non_evidence_test_vector`；输入不含字段值、附件、凭证或回复，因此合成哈希不能被科学字段误用。

编译器从 11-node/10-edge DAG 重新计算，而不是信任上一拍的数量摘要。结果精确匹配两个 changed nodes、八个 stale nodes 和三个 preserved nodes，三集合关系覆盖全部当前节点且历史不删除。重建顺序经过拓扑排序，保证 `ton-unit-contract` 先于两个 ton scenario、scenario 先于对应 validation。任何节点身份漂移、预期集合漂移或字段选择漂移都会 fail closed。

十条受影响 claim 只生成未来意图：两条 conditional 要复验，四条 unresolved 仅在真实证据准入和重建后才可进入释放审查，四条 prohibited 在全部释放条件通过前继续禁止。八条 verified fact 全部登记未来复验，但当前 class 和状态不变。外部动作、证据准入、模型修改与论断迁移均为 0；比较、LCIA 和发表门继续关闭。报告 SHA-256 为 `d8caefaf5e8a1552af563f01359884815dea4fedd4877ea8c49d0fe6e6810f19`。

截至 2026-07-17 的 GitHub API 检查记录 Kubernetes 123,765 星、Terraform 49,216 星、in-toto 1,016 星。迁移不是语义转述：Kubernetes dry-run 落为“计算副作用但不持久化”的全零 effect contract；Terraform saved plan/apply 分离落为 hash-bound simulation plan 与未来 apply 必须一致；in-toto layout/link 思路落为三份输入绑定、输出报告哈希和 non-evidence 标记。下一纵向路线是 `compile-evidence-intake-validation-harness`：为未来合法附件或可归因回复建立 target/document/field/provenance 的 fail-closed 准入门，但不要求当前就有证据，也不执行人类责任动作。

第二十六拍新增 `evidence_intake_validation_harness/v1`。它把未来输入限定为两个 discriminated candidate kind：合法取得的目标论文附件，或带发送者归因、权限状态与消息定位的 primary response。八道顺序门分别验证合同/secret hygiene、真实 bytes/SHA/media、candidate kind/document role、access/attribution、manifest target DOI/title、target-bound unresolved field 与 replacement route、页码/消息 span，以及 original value/unit/transformations/assumptions/missing-data 的无损保留。

工具链不是自动接收器。真实候选即使八门全过，也只能得到 `quarantined_candidate_ready_for_scientific_review`；测试 fixture 只能得到 `allow_for_contract_test_only`。编译活动运行八个确定性 fixture：一个可归因回复测试向量结构通过但不准入；bibliographic metadata、author CV、credential reference、hash mismatch、wrong target、missing locator 和 Beat 25 synthetic hash 七类按预期拒绝。8/8 expectation match，覆盖 2 targets、15 fields、15 replacement routes，报告 SHA-256 为 `5a06e55ccd9f4d85bf48b4828edd41d39fda8a73eb84c36db289d2990b499c73`。

截至 2026-07-17 的 GitHub API 检查记录 Pydantic 28,297 星、Open Policy Agent 11,987 星、Sigstore Cosign 6,137 星。Pydantic typed/core-schema 迁成 attachment/response 严格联合合同；OPA input/data/policy/decision 迁成候选观察、manifest 数据、顺序 gate 与 reason-coded decision；Cosign digest-first 迁成目标/字段判断之前的真实 byte/hash/media identity。全部落在 stdlib 模块、测试、活动、事件和双投影，没有部署外部 runtime。唯一下一纵向路线是把当前已内容寻址的 metadata、repository exports、Pure records、CV/机构 PDF 与 citation-only observations 统一送入同一 policy，生成零网络、零准入的 candidate ledger。

第二十七拍新增 `current_evidence_candidate_ledger/v1`。它不重新访问网络，而是逐一核对 manifest、intake harness 和六份来源报告的 path/SHA-256，再由 public discovery、official-source expansion、author/institution、record export/OAI、Korean sources、author-hosted files 六个显式 adapter 发出 occurrence。每条记录都有稳定 entry ID、semantic group fingerprint、来源报告与 JSON pointer、嵌套响应/文件 hash、目标与文档角色、route state、policy disposition、reason code，以及全零的准入/变更事实。

真实总账含 38 条 occurrence 和 38 个当前 group：14 条 DOI/标题连续的书目、repository export 或 CRIS metadata，4 条已验证但不是目标论文的 profile/CV/机构文档，20 条路线状态。20 条路线状态继续细分为 10 个 dated no-hit、2 个 degraded、4 个 OAI semantic error、2 个 credential required 和 2 个 scope inapplicable；不能把它们合并成“证据不存在”。两份真实 PDF、CV 中两次目标标题出现及一个汇总的 SNU retry 都可追溯，但候选隔离、证据准入、网络读取、凭证使用、外部动作、lineage rebuild、model mutation 和 claim transition 均为 0。版本为 `96c2b4a46c6cb4ff`，报告 SHA-256 为 `41cdab87acb7f7340b50de078317c3e2c66d0e554a09019bf88a26dd9f8a912d`。

截至 2026-07-17 的 GitHub API 检查记录 Sentry 44,307 星、OpenMetadata 14,495 星、DataHub 12,280 星。工程迁移分别落为 Sentry fingerprint/occurrence 的“同组但不丢来源 occurrence”、OpenMetadata connector/run status 的六 adapter + route summary、DataHub entity/aspect 的 observation identity + integrity/role/route/policy aspects；没有安装或声称运行这些完整平台。唯一下一纵向路线是编译 `evidence-admission transaction boundary`：定义未来真实候选从 quarantine、科学复核、字段接受到 stale marking 和受控 rebuild 的原子事务及失败回滚，但当前不得执行准入或模型变更。

第二十八拍新增 `evidence_admission_transaction_boundary/v1`。它重新核对 manifest、intake harness、candidate ledger、replacement map、replacement dry-run、current lineage 和 claim matrix 七份工件的 path/SHA-256，并把未来真实候选的生命周期编译为 11 个状态：observed、quarantined、AI scientifically reviewed、field accepted、prepared、rebuilt staged、validated staged、committed，以及 rejected、aborted、commit conflict 三个非提交终态。前七步只能写 staging namespace；唯一 committed 状态必须同时 compare expected state version、11 个当前 node hash 和新 snapshot fingerprint，不能逐文件改当前真相。

六个 `non_evidence_contract_fixture` 分别到达 would-commit contract-only、intake rejected、scientific-review rejected、staged-validation aborted、optimistic conflict 和 replayed terminal result。全部 expectation match，覆盖 2 changed、8 stale、3 preserved 节点和 10 条受影响论断；transaction ID 稳定，重放先查终态，staged failure 保留旧 current snapshot 与诊断工件。真实结果 version 为 `9646f0b41087652b`，报告 SHA-256 为 `281d44230d721c95f1d9166d3d823880236067b4ab4aa0efcc8b31c94e2dd3ed`；当前 evidence admission、pointer update、lineage rebuild、model mutation、claim transition、partial commit 均为 0。

截至 2026-07-17 的 GitHub API 检查记录 Git 62,059 星、Temporal 21,682 星、Apache Iceberg 9,053 星。Git `update-ref` 的 start/prepare/commit/abort 与 old-object verify 迁成状态机和 expected-current precondition；Iceberg immutable staged snapshot、optimistic validation、atomic current-metadata swap 与 retained history 迁成 staging + compare-and-swap snapshot；Temporal durable history、retry/replay 与 status recovery 迁成 transaction-ID 幂等和终态查询。没有安装三者作为科研运行依赖。唯一下一纵向路线是编译当前 TianGong-native comparison-readiness gate，把可执行事实、科学证据缺口、清单完整性、分配、不确定性与原生校验边界统一成可机器判定的关闭/释放条件，但不运行 comparative LCIA。

第二十九拍新增 `current_comparison_readiness_gate/v1`。Kernel 先逐一重算当前 residual scenario、2/2 Process validation、两条零 finding/blocker route QA、11 Flow/4 Source validation 的 SHA-256，再生成 `native_current_model_register/v1`；随后与 inventory plan、11-node lineage、15-field manifest、stoichiometric report、26-claim matrix 和 admission transaction 共七份输入编译 12 项门条件。状态只有 satisfied、ready internal activity、blocked，没有总分，也不允许已满足项目抵消 blocked 项。

真实结果为 2 satisfied、1 ready、9 blocked。两项 satisfied 是 TIDAS Process 2/2 validation 和双路线 Process QA；ready 是本地 lifecyclemodel planning。九项 blocked 分别覆盖功能单位/有效范围、route-aligned primary inventory、碳包覆阶段、elementary-flow 物种/区室/characterization、分配一致性、remote identity/support refs、source-bound uncertainty、native LCIA method/calculation surface 和 comparative claim release。五个科学 gap、15 个 unresolved field、8 个 unresolved claim、6 个 prohibited claim 全部保留。版本 `d848521ac3de46c1`，报告 SHA-256 `05e07f3dc301a419dd31b6e7a1cd90806c603d86bcdd472da6333d16252dc015`；comparison、LCIA、network read、external action、remote write、model mutation、claim transition 均为 0。

原生能力不是猜测：对固定 `@tiangong-lca/cli@0.0.25` 的 help 审计确认 lifecyclemodel `auto-build/validate-build/graph/qa/build-resulting-process/orchestrate/save-draft` 和 lifecyclemodel/lciamethod contract 可用，但没有 LCIA calculation 或 multi-variant comparison command。外部工程上，截至 2026-07-17，Argo CD 23,636 星、Dagster 15,855 星、openLCA app 262 星；直接领域项目虽星数较低仍因相关性保留。Argo CD health/sync 分离迁成 native structural/scientific readiness 分离，Dagster blocking check 迁成 blocked 与 ready-internal 不同下游行为，openLCA product system/reference amount/same functional unit/allocation/LCIA method setup 迁成独立门。唯一下一路线是 `plan-native-route-lifecyclemodel-assembly`，不得把模型壳搭建说成比较或 LCIA。

第三十拍新增 `native_route_lifecyclemodel_assembly_plan/v1`。进一步检查固定 npm 包的 `dist/src/lib/lifecyclemodel-orchestrate.js` 后，Kernel 选择原生 `root.kind=process + reuse_existing_process`，而没有把更早的 pre-patch Process build run 冒充当前 `auto-build local_runs`。当前 residual JSONL 的两条原始行分别按原字节拆分并计算 SHA-256；完整 TIDAS 行装入只增加 `id/version/route_id/source hash` 的根 envelope，没有改写 Process 语义。

每条路线的请求都固定 collapsed depth 0、禁止 Process/submodel builder、关闭全部 remote candidate source、dry-run 且不准备 publication payload。`npm --offline` 调用固定 0.0.25 的 `lifecyclemodel orchestrate plan` 后，两条路线各自产生 request-normalized、assembly、graph、lineage 和 boundary 五份原生工件。Kernel 逐层复核 Process UUID/version、被选 candidate、共同的定量参考 Flow `c36b1ab6-5561-5f0a-b2bf-179fdc9d90a6@00.00.001`、1 kg Output、screening-use 声明和 `collapsed_at_existing_process`。真实总计为 2 node、0 edge、2 boundary、0 invocation、0 unresolved；bridge version `8436edc390bc2c06`，报告 SHA-256 `aabf247a2974befc26fd3a9266f59c50a6683fd1d8ea6c1c650cdbe9c890565d`。

九项 comparison blocker、五个科学 gap、15 个 unresolved field、8 个 unresolved 和 6 个 prohibited claim 原样保留；Process build、lifecyclemodel build、comparison、LCIA、network read、external action、remote write、model mutation、claim transition 全为 0。这个结果是原生根/边界计划，不是 lifecyclemodel 数据集成品。下一路线是 `materialize-native-route-lifecyclemodel-shells`：必须在保持 exact current Process 根的前提下找到并验证语义忠实的原生壳生成路径，仍不得比较、LCIA 或远程保存。

第三十一拍新增 `native_route_lifecyclemodel_shell_materialization/v1`。进一步审计固定包的 `lifecyclemodel auto-build` 发现，它只接受带 `cache/process_from_flow_state.json` 与 `exports/processes/` 的真实 `process-automated-builder` 运行目录，并会把该来源写入 provenance。当前权威 Process 是后续补丁与复验后的两条 residual 行，没有对应 builder run；因此 Kernel 没有伪造目录，也没有退回较旧的 pre-patch export，而是按原生 lifecyclemodel contract 构造最小确定性 TIDAS 壳，再把结构权威交回原生校验链。

每个壳只有一个当前 Process instance、multiplication factor 1、指向内部 Process 1 的 quantitative reference，以及一个 UUIDv5 预留但明确 `resulting_process_materialized=false` 的 resulting-Process 身份。名称、四级分类、commissioner/data-entry/ownership/license 与 compliance 均从绑定的当前 Process 精确复制；结构化 provenance 绑定 Process UUID/version、route-row、Beat 30 plan/request 和参考 Flow。随后离线执行固定 0.0.25 的 lifecyclemodel contract、dataset validation、graph、QA 与 save-draft dry-run。真实结果为 2/2 valid、2 models/2 nodes/0 edges/0 findings、0 QA error/0 warning、2/2 prepared/0 failed/0 executed；shell version `7b22f3465aa7edda`，报告 SHA-256 `289e84d97db3b3efa319b638c1e980688572e5ffa5e0888563152438d5990750`。

工程机制不是语义类比：Helm 的 render → lint → local dry-run 被拆成 Kernel shell render → native contract/schema/graph/QA → native save-draft dry-run；Ajv 的 schema compile/data validate 与结构化 errors 被迁成固定 contract hash、独立 dataset/prewrite validation 和保留 findings。Kernel 自己的 shape check 只是前置条件，不能替代 TianGong schema。九项 blocker、五个科学 gap、15 个 unresolved field、8 个 unresolved 和 6 个 prohibited claim 继续保留；comparison、LCIA、network read、external action、remote write、model mutation、claim transition 全为 0。下一路线唯一是 `build-native-route-resulting-process-dry-run`，且仍不允许发布、比较或 LCIA。

外部迁移同样落到模块而不是名称。2026-07-17 GitHub API 记录 Apache Airflow 46,143 星、Argo Workflows 16,827 星。Airflow serialized DAG 的“解析源与调度 JSON 分离”落为完整 TIDAS row + compact versioned bridge + native plan；dynamic DAG stable order 落为稳定 route 排序、node/request ID 和 content-derived version。Argo 显式 artifact/parameter 与 DAG binding 落为 scenario row、request、assembly、graph、lineage、boundary 的逐项 path/hash。没有安装 Airflow 或 Argo runtime。

第三十二拍新增 `native_route_resulting_process_dry_run/v1`。Kernel 重新核对当前 residual Process JSONL、Beat 31 报告和两份 shell payload 的 path/SHA-256，为每条路线生成 primary-only 本地请求：source model 指向精确 shell，Process source 只有一个明确文件，`allow_remote_lookup=false`，publish intent 是 dry-run。固定 0.0.25 的 `lifecyclemodel build-resulting-process` 各产生一份 raw bundle、relation、projection signature 和一个以 shell UUID 为身份的新 Process。Kernel 不复刻原生聚合算法，只核对一节点/零边、本地 resolution、included-Process UUID/version、交换语义 multiset、参考 Flow/version/direction/amount 和允许变化的 lineage/identity/metadata 路径。

真实预执行同时发现固定版本的兼容缺陷：投影器把所有 exchange `meanAmount` 与 `resultingAmount` 序列化为 JSON number，而同包 ProcessSchema 要求 string；默认 `partly terminated system` 也不是 schema 的大小写精确枚举。请求因此显式传入 `Partly terminated system`。raw bundle 和 raw JSONL 保持不可变；Kernel 另行编译带 UUID/version test 的 patch，只允许数字 amount 替换为 canonical decimal string，并交给原生 `dataset patch apply`。两条路线分别替换 16 与 12 个字段，共 28 个 replacement、60 个含 test 的 operation 和 28 条 patch evidence；逐字段 Decimal、交换集合及参考流 tuple 全部相等，其他路径零变化。这是序列化适配，不是科学修正或 Kernel 自建 exchange 聚合。

归一化后的两个 Process 通过原生 2/2 dataset validation、零 finding/零 blocker Process QA 和 2/2 prepared、0 executed、0 failed 的 save-draft dry-run。hydrothermal resulting Process SHA-256 为 `c69e9ff99dfefc1d4d058bbe9cfc51eab91235f297d4bf398baf8ae91d431c1f`，solid-state 为 `caf051e0eaadca0e8a133b53646b9d03bf244a5fda9298334ae82859ab1d3843`；projection version `425ac09653f55212`，最终报告 SHA-256 `91ddd65676a8b1a10c76696828e2bed2d1f9a334d16449b923a725d2c2566800`。重放事件保持 1862→1862，机器投影只增加 compact route/hash/count，大小为 116,935 bytes。

外部工程迁移继续要求具体落点。2026-07-17 观察到 OpenTofu 约 28.8k GitHub stars、Apache Beam 8,629 stars。OpenTofu plan/apply 分离落为 raw projection + hash-bound normalization patch + 原生 apply report，而不是直接覆盖投影；Apache Beam 的输入不可变、transform 产生新输出落为 raw/normalized 两套工件和语义等价验证。对应实现分别进入 patch compiler、native driver、artifact bindings、gate verifier、reducer、双投影和测试，并未安装两个外部 runtime。九项 blocker、五个科学 gap、15 个 unresolved field、8 个 unresolved 和 6 个 prohibited claim 原样保留；comparison、LCIA、network read、external action、remote write、publication、model mutation 和 claim transition 全为 0。下一唯一路线是 `compile-native-route-model-execution-package`。

第三十三拍新增 `native_route_model_execution_package/v1`。固定 `@tiangong-lca/cli@0.0.25` 没有通用的 model-package 命令；相邻的 `publish-resulting-process` 只生成发布交接，并会复制尚未归一化的 raw Process，`lifecyclemodel orchestrate publish` 则要求真实 orchestrator run。当前路线没有这种 run，Kernel 因而没有伪造 provenance，也没有把发布包冒充执行闭包。TianGong 继续拥有六份 TIDAS 数据集、投影请求、原生关系、schema/graph/QA 和未来投影执行；Kernel 只拥有跨工件 manifest、内容绑定和重放顺序。

执行闭包含两个精确 current Process、两个 lifecyclemodel 壳、两个归一化 resulting Process，以及每条路线的请求、原始/归一化期望结果、关系，另含无损归一化补丁、九项比较 blocker 和 pinned offline replay DAG，共 20 个内容绑定文件。package version `5e6b1ddbe2d651dc` 由合同、固定工具、四份上游绑定和全部文件哈希导出。逐文件重算得到 20/20 完整、286,024 bytes、零 missing/hash/semantic mismatch；current→shell→resulting 身份、参考 Flow tuple、raw/normalized Decimal 与交换集合全部闭合。

复制后的闭包再次交给 TianGong 原生结构门：四个 current/resulting Process 为 4/4 valid、Process QA 零 blocker；两个 lifecyclemodel 为 2/2 valid、2 nodes/0 edges/0 findings、QA 零 error。最终 manifest 与报告 SHA-256 均为 `ddb3e83434aa1a9d6484c8e8dbc831d7770788c664f88f324b5fb34cb73553a1`。重放尚未执行，publication、comparison、LCIA、network read、remote write、current-model mutation 与 claim transition 也全为 0；九项 blocker、五个科学 gap、15 个 unresolved field、8 个 unresolved 和 6 个 prohibited claim 继续保留。

外部机制迁移仍是模块级而不是语义改名：MLflow 的 root metadata/signature/files/environment 落为根 manifest、typed route identity、内容文件和固定 TianGong 工具；Nix 的 hash-addressed immutable closure 落为 content-derived package version 与只复制运行时依赖；OCI image-spec 的 manifest/config/layer 分离落为根 manifest、replay plan 与 payload 文件。2026-07-17 观察到 MLflow 约 26.5k、Nix 约 16.9k、OCI Image Spec 约 4.3k GitHub stars。它们没有成为运行依赖。具体落点包括 compiler、writer、integrity/semantic verifier、native gate driver、Project activity、精确上游失效集合、双状态投影和测试。下一唯一路线是 `replay-native-route-model-execution-package`：必须在隔离本地目录按 DAG 重建 raw projection、应用同一补丁并要求最终内容哈希、projection signature、交换与参考流语义完全一致；仍不得发布、比较或运行 LCIA。

第三十四拍新增 `native_route_model_execution_replay/v1`。Kernel 不把包内命令当作可信脚本，而是先逐项重算 manifest 与 20 个文件的大小和 SHA-256，要求固定两条路线、精确七步无环依赖、三个允许的原生命令形状、全部相对安全路径、预期 raw/final hash、projection signature 和全零副作用策略。任意附加命令、路径逃逸、依赖环、内容漂移或开放 publication/comparison/LCIA/network policy 都会在原生进程启动前拒绝。

通过预检后，执行器把 20 个输入逐字节复制到新的 `sandbox/package/`，只在该目录运行固定 `@tiangong-lca/cli@0.0.25`：两次 `lifecyclemodel build-resulting-process`、Kernel 的两次 raw 语义核验与稳定合并、一次原生 `dataset patch apply`、一次最终核验。真实重放版本为 `ef8161260e364fec`，verifier logic 为 `2026-07-17.2`，七步全部完成；raw 合并 SHA-256 为 `d1b3da82d75c10889a3e3363ad708df40d9b190779a8346e684ce052cbe94537`，最终 JSONL SHA-256 为 `77569d76d73b3ff6487055c6519ff6127b5c857bb557c70e4a9f1a21822d0acc`。两条 raw Process 分别命中 `c5bd9e...e330c` 与 `a148a5...02a49`；UUID/version、relation、projection signature、Decimal 交换 multiset 与参考 Flow tuple 全部相等。原生补丁再次报告 60 个 operation、28 个实际 replacement、28 条 evidence、零 blocker 和零 scientific-value change。原包 manifest 与 20 个文件前后树哈希一致；Project 重复调用会重验复制输入与 trace products，并保持事件数 `2060→2060`。接受报告 SHA-256 为 `be6ecdfcc8607278bccd8b6e7ae18c309dcdf0ec8321bfa8dc9000d362d8b846`。

边界没有被结果掩盖：这是文件系统隔离的本机重放，不是容器级 hermetic execution。报告披露 Python 3.9.6、Node v24.18.0、npm 11.16.0 与固定 TianGong 包，强制 npm offline；但 Node/npm/npm cache 没有嵌入闭包，`os_network_sandbox=false`、`tool_runtime_embedded=false`、`cross_host_portability_proven=false`。Bazel 的 hermetic input/tool identity 落为 copied input tree 与 pre/post source hash；DVC 的 pipeline/repro 落为七步依赖 trace 和 material/product binding；BuildKit LLB 的 content-addressed graph/explicit context 落为命令 allowlist、显式本地 context 与输出树。这三者没有被安装成运行依赖。九项 comparison blocker、五个科学 gap、15 个 unresolved field、8 个 unresolved 与 6 个 prohibited claim 原样保留；comparison、LCIA、publication、remote write、model mutation 全为零。唯一下一路线是 `bind-portable-native-tool-runtime-closure`，先闭合工具运行时再讨论跨主机可移植性。

第三十五拍新增 `portable_native_tool_runtime_closure/v1`。一次只读 bootstrap 以 npm offline 模式定位缓存中的固定 `@tiangong-lca/cli@0.0.25`，随后把真实 Node v24.18.0 Darwin/arm64/Mach-O 可执行文件、Node LICENSE、根 package/lock、完整已安装 `node_modules`、安全相对符号链接、包清单和许可证清单编译为文件级闭包。每个文件绑定路径、SHA-256、字节数和 mode；符号链接同时绑定 target 与解析后闭包内路径。锁文件中未安装的 `fsevents@2.3.3` 只有在 `optional=true` 时允许，并被单列为平台解析证据；任何缺失的非可选锁项仍然 fail closed。

真实闭包版本为 `c33216d70a0e1cae`，tree SHA-256 为 `8c3b716dd0a4436fc21ffd66af127dcb7cf04677c94ec5e4ec2696afc5a4db05`。闭包含 7,877 个文件、11 个安全符号链接、129 个实际安装包和零缺失许可证声明，共核验 205,850,320 bytes。排序路径、零 mtime/uid/gid 和规范 owner 的 USTAR 两次构建逐字节一致；213,760,000-byte 归档 SHA-256 为 `9181c854dc623b46ad84fd171ab9e64c9006bf430fe53c5c73cd02bc0ada0c94`。Project 只登记 manifest、归档、清单、报告和关键重放产物，不把近八千个文件写成近八千个事件。

安全解包器拒绝绝对路径、`..`、重复成员、特殊文件、自引用或逃逸链接，并在启动进程前重算全部绑定。搬迁后的命令只允许把第 34 拍已经接受的 npm 前缀替换为闭包内绝对 `bin/node` + TianGong JS 入口；科学命令后缀、20 个包输入、七步 DAG 和期望产品不变。新目录中没有调用 npm，也没有读取 npm cache；两条 raw projection、28 条 patch evidence 和最终 SHA-256 `77569d76d73b3ff6487055c6519ff6127b5c857bb557c70e4a9f1a21822d0acc` 再次完全一致，源包、归档和解包运行时前后均未变化。接受报告 SHA-256 为 `f8196e7070054481522b92fb9b71314d83c1cfd8d7fb7858aef3ca6d9c7d84b0`；重复 Project 调用重验当前闭包而保持事件数 `2077→2077`。

结论精确限定为 `runtime_content_closed=true` 与 `same_platform_relocation_proven=true`。Node 仍动态依赖 CoreFoundation、Security、libc++ 和 libSystem，因此 `system_runtime_closed=false`、`cross_platform_portability_proven=false`；没有 OS 网络沙箱。所有包虽有声明，分发前仍需人工许可证复核，故 `distribution_allowed=false`。Bun 的单一显式执行入口落为 direct launcher，但没有替换 Node；pnpm 的内容寻址 store/lock 思想落为完整文件树与锁完整性；Nix 的 closure/requisite transfer 落为单一确定性归档和未闭合系统依赖披露；Node SEA 因活动开发状态、模块树打包和注入/签名要求只保留为未来选项。没有安装这些外部 runtime。基础设施闭包通过后停止横向扩张，九项 blocker 与全部科学缺口继续保留，唯一下一路线回到科学集成：`compile-native-route-provider-network-gap-map`。

第三十六拍新增 `native_route_provider_network_gap_map/v1`。Kernel 重新核对执行包中的两条当前 Process、两个 lifecyclemodel、13 个本地 Flow 源文件和科学缺口，再直接使用第 35 拍复制的 Node/TianGong 入口运行 `lifecyclemodel graph --check-connections`。原生图只验证已经声明的边：真实结果为 2 个节点、0 条声明边、0 个 finding，所以 `declared_connection_integrity=true`；它不会从 Process 输入自动推断供应商，不能据此声称产品系统完整。

派生编译器逐交换建立 Process→Flow 输出、Flow→Process 输入和 lifecyclemodel→Process 包含关系。14 条交换全部精确绑定本地 Flow UUID/version：9 条输入、2 条功能参考输出、1 条未分配副产品和2 条组成未知残差。当前 Process 集没有任何一个为这 9 条输入输出相同 Flow，故生成九项 Provider requirement，background coverage 为 0/9，`provider_network_complete=false`。requirements 保留消费方、exchange internal ID、原金额/单位/注释和 Flow 来源；下一拍的 identity-preflight target 只带已知 Flow ID/name 与 provider role，不发明名称、地点或技术。当前 0 条 elementary-emission exchange 不会被解释为零排放，两条路线的排放表征缺口继续显式保留。

真实 input version 为 `753a99b6e34aefac`，map version 为 `6d8d33886fe9c503`，报告 SHA-256 为 `770ad36dc3866d304d61f853e026ac1a28709ab75a55ae19ae9f3374abd4ba6e`；重复 Project 调用重验七个产物而保持 `2097→2097`。Backstage descriptor/derived-relation 分离落为权威 TIDAS 行与可重建派生边；NetworkX 有向图机制落为稳定 node/edge JSONL；Brightway production-exchange 语义落为候选必须实际 Output 同一 Flow，且拒绝 heuristic production guessing。没有安装三套外部系统。比较、LCIA、网络读取、远程写入、发布和当前模型修改仍为零；唯一下一路线是 `resolve-native-provider-candidates`。

第三十七拍新增 `native_provider_candidate_resolution/v1`。复制的 Node/TianGong 入口对九项要求逐一执行本地 `process identity-preflight`，唯一 candidate input 是模型包已绑定的两条当前前景 Process，不递归吸收 workspace 历史。真实运行完成 9 次 preflight 和 18 次 requirement-candidate 评估；原生决定均为 `create_new`，其语义只是不与候选形成重复 Process 身份，不是 Provider 新建许可。

Kernel 随后独立解析 candidate canonical exchanges，要求 Flow UUID、version 和 `Output` direction 同时相等，并拒绝消费过程自供。两个前景 Process 都不输出九种输入，因此 exact Output match、eligible candidate 和 accepted candidate 均为 0；九项结果都是只覆盖两个 Process 的 bounded no-hit。TianGong public `process_hybrid_search` 需要 API/session credential，本拍没有越过责任边界，九条 remote route 单列为 `credential_required_not_executed`，`global_provider_absence_proven=false`。

真实 resolution version 为 `7b8c23ccd0a8849f`，报告 SHA-256 为 `7c75216d7b320ad5eac9eefe11dd326ceba4950e90b45d5719e2b002f9fe074d`。首次 activity 因 Project/driver canonical JSON 版本算法分叉而在原生命令成功后被 harness 拒绝；共享 `provider_preflight_version()` 修复后 attempt 2 接受 46 个工件，重复调用保持 `2156→2156`。OpenRefine 的 candidate/judgment 分离、dedupe 的 blocking/matching 分离和 Brightway suitable production exchange 规则分别落为 native observation、exact Output eligibility、route ledger、Project/CLI/reducer/双投影和测试；没有安装外部系统。模型连接、mutation、remote write、comparison 与 LCIA 均为零；唯一下一路线是 `compile-provider-process-evidence-requirements`。

第三十八拍新增 `provider_process_evidence_requirements/v1`。纯编译器先精确联结 candidate resolution、九项 unresolved batch、原 source-route ledger 和完整 Provider requirements；缺项、重复项、Flow/consumer/route 漂移、凭证状态折叠或把消费者 CN 地理提升为 Provider 地理都会 fail closed。每项 requirement 编译 17 个可验证 aspect，覆盖目标身份、参考产品/定量基准、Provider 身份、地理、时间、技术、系统边界、完整 inventory、exchange 单位与方向、Flow 映射、分配、数据源、转换、质量、数值不确定性和许可。真实结果是 9 套、153 项：18 known、36 partial、99 unresolved；它们是 expectation suite，不是待填空壳。

复制的 Node/TianGong CLI 0.0.25 随后逐项运行 `process build-plan validate --report-only`。九个 schema 尚不适用的部分计划都按预期阻断；每个探针已有 target、identity decision、unit-of-analysis object 和 reference Flow ID 四项，缺 name、Provider geography 和 technology route 三项，并保留 not-automatic unit decision、reference amount、flow property、declared/functional basis 和上述三项缺失共七个 blocker。合计 36 present、27 missing、63 blockers，Process materialization/creation 为零。原生最低建模门只覆盖七项 authoring prerequisites；更广的 inventory、时间、边界、分配、不确定性和许可仍由科学合同管理。

路线层为每项 requirement 独立登记 TianGong public search、Federal LCA Commons API、官方 USLCI release、GLAD metadata 和 openLCA Nexus catalog，共 45 行。九条 TianGong 路线仍需凭证；九条官方 USLCI 路线无需凭证且可执行。唯一选择 `provider-requirement:solid-state:d3f744d8e4a4d0b7` 的 electricity public probe，是因为官方发布路线当前可执行，而不是完成了影响优先级判断；美国数据对 CN 消费场景的地理/技术/时间代表性和外部 Flow UUID 映射继续是 blocker。

真实 input version 为 `c0ec5d07e6003b8f`，requirements version 为 `85cf88bd0e674b76`，报告 SHA-256 为 `9fd7148148cf1c054a9f189014325d775eee27466df5b877752764e6219e664e`。首次 activity 只因固定 CLI 的真实恢复动作是 `fix_build_plan` 而测试期 fixture 写成 `fix_blockers` 被拒绝；修正枚举后 attempt 2 接受 28 个工件，重复调用重验全部工件并保持 `2192→2192`。Pydantic 的 strict model/error aggregation、Great Expectations 的 expectation suite/checkpoint、DataHub 的 aspect/URN/provenance 分离分别迁移为本地 strict join、逐项 expectation、原生 probe、稳定 requirement ID、content-addressed lineage、Project/CLI/reducer/双投影和测试；没有安装三套外部系统。USLCI、Federal LCA Commons 和 ElectricityLCI 作为领域权威数据路线，而非通用 Agent 框架。Provider build authorization、Process creation、connection/mutation、remote write、comparison、LCIA、地理提升和消费量→Provider 参考量推断均为零；唯一下一路线是 `probe-uslci-electricity-provider-candidates`。

第三十九拍新增 `uslci_electricity_provider_probe/v1`。Kernel 先重算第 38 拍报告、153 项 expectation 和 selected slice 的 SHA-256，再只构造三个固定公开请求：GitHub latest release、Federal LCA Commons repository descriptor 和限定 repository/type/location/page 的 electricity search。检索结果必须恰好是当前四个美国全国 Process；只有唯一的 non-residual `at user; consumption mix` 可被选择。随后从实际 `defaultProvider` 关系派生两次 browse 请求，因而总公开读取固定为五次，且不需要 API key、登录或远程写入。

真实来源是 USLCI `v1.2026-06.0` 与 Federal LCA Commons repository `1.2026-06.0`，描述明确对应 2023 electricity baseline。四个候选中选中 `75d4be66-12a7-30b3-bc57-fa724c941b0e`，它有一条 1 MWh `Electricity, AC, 120 V` 定量参考 Output 和一条 1.037799503256141 MWh at-grid Input；该 Input 的默认 Provider 是 `5c13cc4e-b1eb-3fca-a291-b2891bd253a0`。依赖 Process 有 68 条交换，其中 67 条 Input、1 条 1 MWh `Electricity, AC, 2300-7650 V` 参考 Output。两者 version 都是 `03.00.000`，有效期为 2023-01-01 至 2023-12-31，来源记录包含 ElectricityLCI；源字段显示 copyright false / No Restrictions，但这些事实仍只作为 source observation 保留。

openLCA JSON-LD 解析器使用真实 `@id`、`isInput`、`isQuantitativeReference`、`defaultProvider`、`flow`、`unit` 和 `flowProperty` 字段，拒绝 reference Output、Process identity、位置、时间或 dependency 漂移。本地目标仍是 `afc21f75-af0f-528a-8aa7-f31adefe0cd7@00.00.001` 的 `Electricity, process`，参考单位 MJ；外部 120 V/2300–7650 V Flow UUID 和 exchange unit MWh 均保持原样，状态是 `explicit_mapping_required`。美国 Provider 对中国消费者的地理、时间、技术、系统边界适用性全部继续 blocked；没有做 UUID 重写或 MWh→MJ 换算。

真实 input version 为 `fbb69eb80f616369`，probe version 为 `eb776bd4ff939c2e`，报告 SHA-256 为 `a63a66327e549144d36960696371d94fc003087fbf17741f175147bff4efe4ad`。Activity 注册三个输入 binding、五个 raw public envelope 和五个编译输出，共 13 个工件；第二次调用逐项重验并保持事件数 `2211→2211`。DVC 的 source version/content hash/impacted rerun 落为 response binding、input/probe version 和精确失效；OpenLineage 的 run/job/dataset/facet 分离落为 public read、candidate/dependency Process、mapping gap 和 artifact lineage；GreenDelta openLCA schema 的实体与交换语义落为严格 JSON-LD parser。没有安装这些完整系统。Provider build authorization、Process creation、model connection/mutation、external write、comparison 与 LCIA 仍全为零；唯一下一路线是 `compile-uslci-electricity-provider-normalization-plan`。

第四十拍新增 `uslci_provider_normalization_plan/v1`。编译器严格回绑第 39 拍 report、四条 candidate observation、两条 Process observation、Flow mapping gap 和 selected slice；任何 binding、Process/Flow identity、exchange count/direction、MWh/MJ 语义、default Provider 或权限位漂移都会 fail closed。两个 Process 的 70 条交换生成 32 条可逆 openLCA→TIDAS 字段映射和 70 条独立单位转换提案。每条提案保留原金额、精确 Decimal 因子 `3600`、目标金额、Energy 维度和 `proposed_not_applied` 状态；at-user Input 为 `1.037799503256141 MWh → 3736.0782117221076 MJ`，at-grid 67 条 Input 合计 `1.00000000000000005412 MWh → 3600.000000000000194832 MJ`。换算只证明单位算术，不改变 Flow 身份、地理或模型。

TianGong Process schema没有 openLCA exchange-level `defaultProvider` 字段，而 lifecyclemodel 才拥有 Process 连接。因此编译器拒绝把 Provider ID 塞进交换注释，改为单独生成 68 条 `tidas_lifecyclemodel_connection` requirement：一条 at-user→at-grid，加上 67 条 at-grid→发电 Provider。67 条上游 Input 全部使用同一高压 Flow，但绑定 67 个不同 Provider Process；当前两条 Process 没有 elementary exchange，所以它们是 wrapper/aggregator，不是闭合背景清单。图状态必须保持 `provider_graph_closed=false`。

Flow 层生成两条不应用的映射：120 V 源 Flow 到现有本地 `Electricity, process` 的候选等价映射，显式标记电压细节和美国地理损失；2300–7650 V 源 Flow 到一个确定性本地候选身份，状态为 `pending_native_identity_preflight`。另有五份不调用的 TianGong 原生草案：一份 Flow identity-preflight、两份 Process identity-preflight、两份 Process build-plan validate。所有 `invocation_allowed=false`，没有把草案冒充原生通过结果。

U.S.-2023→CN 适用性决定为 `conditional_screening_proxy_for_structure_and_unit_pipeline_only`。地理与 inventory closure 是 high risk，时间为 medium，technology/market 为 medium-high；TIDAS `percentageSupplyOrProductionCovered` 保持 0，并要求记录 deviation。该源只可用于 parser、单位管线、身份预检准备和 Provider 图闭包准备，不能支持中国代表性、科学路线比较、comparative LCIA 或 publication claim。

真实 input version 为 `df536521cbb2ee68`，normalization version 为 `2384f6eda1ad0301`，报告 SHA-256 为 `225cf40eedfdf06cdcb62b7e05ec5c11628b341c7cf728eba232b20b3854275c`。Activity 登记五个输入 binding 和八个输出，共 13 个工件；首次调用使事件 `2211→2230`，第二次逐项重验并保持 `2230→2230`。OpenRefine 的 serializable operation/change history 落为 source/proposed/unapplied transformation row；dbt 的 DAG/assertion 机制落为 Provider connection ledger 和严格图不变量；Pint 的 quantity/dimension/Decimal 纪律落为精确 Energy 转换；GreenDelta openLCA schema 继续提供 `defaultProvider` 的源语义。没有安装这些完整系统。Process/Flow 创建、identity/build-plan 调用、model connection/mutation、外部读写、comparison 和 LCIA 均为零；唯一下一路线是 `probe-uslci-electricity-generation-provider-closure`。

第四十一拍新增 `uslci_generation_provider_closure/v1`。驱动从第 40 拍 selected slice 构造恰好 67 个 Federal LCA Commons browse GET，以四 worker、固定 retry/timeout 并发读取；每个 Process 独立保存 URL、时间、状态、attempt、bytes、response SHA-256 和 payload。编译器要求请求 ID、payload `@id`、`UNIT_PROCESS`、`03.00.000`、2023 时段和单一定量参考 product Output 一致，再保留完整 Process、reference product、inventory summary 和进一步 `defaultProvider` edge。

真实 67 个 generation-mix Process 共含 380 条 exchange，但 elementary exchange 为 0；这证明该层仍是供应聚合器，不能解释为零排放。进一步抽取 313 条 source-observed Provider edge，对应 313 个尚未观察的 unit-Provider Process。下一层 acquisition weight 使用第 40 拍 national mix share × 当前 generation-mix Input amount，再按 Provider ID 求和；该数只决定抓取顺序，不是环境影响、分配、因果贡献或 LCIA。按权重降序加 UUID tie-break 选择前 50 个，路由权重覆盖 `77.444099705109%`，其余 263 个保留为 deferred frontier。

真实 input version 为 `ce0e865c32f32684`，closure version 为 `dcf3b5a1930f22ce`，报告 SHA-256 为 `2745a5466257c8b0cf4a356e6a1dc81163a65812eaf2a220756dbde0f607a8ce`。Activity 登记三个输入 binding、67 个 raw envelope 和七个输出，共 77 个工件。Attempt 1 在网络前因 direct-script relative import 被拒绝并保留；absolute-script bootstrap 修复后 attempt 2 接受，事件到 2315，重复调用保持 `2315→2315`。Scrapy 的 bounded concurrency/retry/raw response 思路落为本地固定 driver；NetworkX 的 directed BFS layer 落为 explicit edge、observed-node exclusion、deduplicated frontier；未安装两套系统。创建、native request、连接/修改、外部写、比较和 LCIA 仍为零；唯一下一路线是 `probe-uslci-electricity-unit-provider-closure-batch`。

第四十二拍新增 `uslci_unit_provider_closure_batch/v1`。第一个 50-ID breadth-first batch 全部公开读取成功，保留 50 个完整 Process、7,130 条扁平 exchange observation 和逐 Process inventory summary。所有 50 个 Process 都出现 elementary inventory：共 6,537 条，其中 6,401 条 Output 分类为 elementary emission、136 条 Input 分类为 elementary resource；另有 343 条 product Flow 和 250 条 waste Flow。Waste Flow 没有被自动提升为环境排放，也没有添加 compartment 或 characterization factor。

真实数据推翻了“所有底层记录从 2023 开始”和“exchange amount 必须非负”两个过强假设。部分技术记录从 2016 开始、有效到 2023；53 条有限负值主要是极小 elementary Output。Kernel 改为要求数据有效覆盖 2023，并保留 signed amount 与 `negative_source_amount` 标志；定量参考 Output 仍必须为正。负值可能是 uptake、credit、correction 或数值残差，后续必须复核，不能剪裁为零。

50 个 Process 仍通过 279 条 product Input edge 指向 79 个更深 fuel/material Provider，因此 `provider_graph_closed=false`。当前 breadth-first 层已观察 50/313，第一批路由权重覆盖 `77.444099705109%`；已选第二批 50 个后累计将覆盖 `91.389763190356%`。更深 79 个 Provider 单列为 depth-4 deferred frontier，不与当前 breadth-first batch 混抓。

最终 input version 为 `9161e05843d7ce83`，batch version 为 `a90ecf89ab158fba`，报告 SHA-256 为 `45b0e303b58bc1c26e2b03dccb860b167b7b91a26025567a65e550dec9854cab`。61 个工件包括三个输入 binding、50 个 raw envelope 和八个输出。前两次被真实时间范围与有限负值假设拒绝并保留；修复后 v1 接受，随后 v2 把不可推进的同命令路由修正为明确的 `extend-uslci-unit-provider-closure-to-batch-2`，事件到 2453，重复调用保持 `2453→2453`。创建、native invocation、连接/修改、外部写、comparison 与 LCIA 仍为零。

第四十三拍新增 `uslci_unit_provider_closure_cumulative_batch/v1`。它把第 42 拍的一次性首批读取扩展为不可变批次加累计快照：严格绑定原 313 行 frontier、首批报告、50-ID observed ledger、279 条首批 deeper edge 和已选第二批。编译器要求第二批恰好等于原排序的第 51–100 行，拒绝重复、缺口、乱序、前序 hash 漂移和累计计数回退；首批原始工件不复制、不覆盖。

第二批 50/50 个 Process 公开读取成功，新增 12,546 条 exchange：207 product、122 waste、12,217 elementary，其中 11,707 条 elementary emission Output 和 510 条 elementary resource Input。50/50 Process 均有 elementary inventory。114 条有限负值保留源符号和风险标记，定量参考 Output 仍要求为正。批次另产生 150 条更深 Provider edge；累计为 100 个已观察 Process、19,676 条 exchange、18,754 条 elementary、167 条 signed negative、429 条更深 edge 和 110 个去重 depth-4 Provider。图仍未闭包，depth-4 仍延后到当前 313-node breadth 层之后。

累计 observed-ID ledger 与 deeper frontier 都是独立内容寻址工件。当前 100/313 的路由权重覆盖为 `91.389763190356%`；确定性选择第 101–150 行作为第三批后预计达到 `97.277805767028%`。该权重只决定读取顺序，不是环境影响、供应贡献或 LCIA。真实 input version 为 `69c03de52d1b5f2c`，batch version 为 `7b6ab1560670efc9`，报告 SHA-256 为 `4ea8b202a3f48dc177f6ecf01a53f9f43066a41d35eee45ea5d0ab23ae6277cc`。63 个工件包括五个输入 binding、50 个 raw envelope 和八个输出；事件达到 2522，第二次调用逐项重验并保持 `2522→2522`。

dbt incremental model 的唯一键/追加语义、Apache Iceberg 的不可变数据文件加 snapshot metadata、DVC 的输入 hash 精确失效，分别落为 observed-ID 不重叠门、batch-local 文件加累计 ledger/frontier、五输入绑定与 replay；没有安装这些完整系统。Process/Flow 创建、native invocation、model connection/mutation、外部写、comparison 与 LCIA 均为零。唯一下一路线是 `extend-uslci-unit-provider-closure-to-batch-3`。

第四十四拍把累计逻辑推广为 batch 3–7 共用的参数化续跑器，而不是继续复制按批次编号命名的编译器。Batch 1 仍是 generation closure bootstrap，batch 2 仍是迁入 cumulative state 的桥；从 batch 3 起，稳定输入固定为原 313 行 frontier、立即前序 cumulative report、observed-ID ledger、deduplicated deeper frontier 和 selected slice。编译器从前序已观察数推导唯一合法前缀，拒绝 batch-index 跳跃、ID 重叠/缺口/乱序、artifact binding 漂移、权限位放宽与累计计数非法值。

通用 runner 已用测试连续模拟到 batch 7：中间批次每次最多 50 个 ID，最后一批可为 13 个；313/313 后不伪造空 batch，而路由到显式 current-breadth-layer closure。Reducer 同时保存 immutable batch-2 recovery payload 和一个 batch 2+ latest-cumulative projection；人看状态解释当前进度与风险，机器状态只保留紧凑计数、路径/hash 和唯一 next route。

真实 batch 3 精确读取原 frontier 第 101–150 行，50/50 成功，新增 18,096 条 exchange：190 product、97 waste、17,809 elementary，其中 17,054 条 elementary emission Output、755 条 elementary resource Input。50/50 Process 均有 elementary inventory；187 条有限负值原样保留。该批新增 136 条更深 Provider edge；累计达到 150/313 Process、37,772 exchange、36,563 elementary、354 signed negative、565 deeper edge 和 143 个去重 depth-4 Provider，`provider_graph_closed=false`。

当前路由权重覆盖 `97.277805767028%`，确定性选择第 151–200 行后预计 `99.389767883186%`。真实 input version `8613647da2064808`，batch version `75670d94fe18fb9f`，报告 SHA-256 `1e5b7957e9441f8e82c980e69ca9e8fb90468517b98e5197428f2e60477b1477`。63 个工件仍是五个 input binding、50 个 raw envelope 和八个输出。首次重放暴露 Project 在 completed-batch idempotency 之前检查 next route；根因修复为先验证已登记工件并返回当前同批快照，再对新批次执行路线门。事件保持 `2591→2591`，新增 Project 级回归测试。

dbt incremental、Iceberg snapshot 和 DVC content-hash 机制现在落实为一个可运行到最后 partial batch 的模块、参数化 Project/CLI、latest reducer 与测试，不只是 batch-2 语义说明；没有安装完整外部平台。创建、native invocation、连接/修改、外部写、comparison 与 LCIA 仍为零。唯一下一路线是 `extend-uslci-unit-provider-closure-to-batch-4`。

第四十五拍直接复用第 44 拍通用 runner 执行 batch 4，没有新增 batch-specific 编译器或 Project 路径。原 frontier 第 151–200 行 50/50 读取成功，新增 16,194 条 exchange：148 product、69 waste、15,977 elementary，其中 15,682 条 elementary emission Output 和 295 条 elementary resource Input。50/50 Process 均有 elementary inventory；232 条有限负值保留源符号；新增 96 条更深 Provider edge。

累计状态达到 200/313 Process、53,966 exchange、52,540 elementary、586 signed negative、661 deeper edge 和 173 个去重 depth-4 Provider。当前路由权重覆盖 `99.389767883186%`，第 201–250 行已确定性选为 batch 5，完成后预计 `99.937318900391%`。这进一步表明权重尾部很小但节点与清单仍需逐项留证，不能用高权重覆盖替代 graph closure。

真实 input version `8a066e16f0d88c9e`，batch version `fc802d54549aa834`，报告 SHA-256 `7c8f8480946f172c4a0cf60ba93ae718a4b2f903fa0f2fa02d7b46434a7fe5a5`。63 个工件继续由五个 input binding、50 个 raw envelope 和八个输出组成；第二次调用逐项重验并保持事件 `2660→2660`。创建、native invocation、连接/修改、外部写、comparison 与 LCIA 仍为零。唯一下一路线是 `extend-uslci-unit-provider-closure-to-batch-5`。

第四十六拍继续复用通用 runner 执行 batch 5。原 frontier 第 201–250 行 50/50 读取成功，新增 10,347 条 exchange：100 product、22 waste、10,225 elementary，其中 9,852 条 elementary emission Output、373 条 elementary resource Input。50/50 Process 均有 elementary inventory；147 条有限负值保留源符号；新增 50 条更深 Provider edge。

累计达到 250/313 Process、64,313 exchange、62,765 elementary、733 signed negative、711 deeper edge 和 190 个去重 depth-4 Provider。当前路由权重覆盖 `99.937318900391%`，第 251–300 行已选为 batch 6，完成后预计 `99.999783428325%`。尾部权重接近零仍不代表最后 63 个身份可以丢弃；它们继续按 exact prefix 留证。

真实 input version `c727622d19b22ebe`，batch version `c9d7da7c6ef32080`，报告 SHA-256 `336ac783b7514687507501ccc2078b2822a0dee1c0f1d960e2a7615bb49cd249`。63 个工件逐项注册；第二次调用保持事件 `2729→2729`。创建、native invocation、连接/修改、外部写、comparison 与 LCIA 仍为零。唯一下一路线是 `extend-uslci-unit-provider-closure-to-batch-6`。

第四十七拍执行 batch 6。原 frontier 第 251–300 行 50/50 读取成功，新增 6,907 条 exchange：107 product、35 waste、6,765 elementary，其中 6,432 条 elementary emission Output、333 条 elementary resource Input。50/50 Process 均有 elementary inventory；101 条有限负值保留源符号；新增 57 条更深 Provider edge。

累计达到 300/313 Process、71,220 exchange、69,530 elementary、834 signed negative、768 deeper edge 和 199 个去重 depth-4 Provider。当前路由权重覆盖 `99.999783428325%`；最后 13 行已被选为 partial batch 7，完成后覆盖为 `100.000000000000%`。通用 runner 没有把最后一批填充到 50，也没有丢弃低权重节点。

真实 input version `5efdd08adacc0fee`，batch version `1630f8f1c832daf5`，报告 SHA-256 `15f0d5b4492d32a966296c4e01f52a73b2cff1e6cb7d2cbe2acf658068bc06fb`。63 个工件逐项注册；重放保持事件 `2798→2798`。创建、native invocation、连接/修改、外部写、comparison 与 LCIA 仍为零。唯一下一路线是 `extend-uslci-unit-provider-closure-to-batch-7`。

第四十八拍执行 partial batch 7。原 frontier 最后第 301–313 行 13/13 读取成功，新增 165 条 exchange：38 product、2 waste、125 elementary，其中 111 条 elementary emission Output、14 条 elementary resource Input；本批没有有限负值。13 个 Process 均出现 elementary inventory，另产生 25 条更深 Provider edge。

累计 observed-ID ledger 达到 313/313，exchange 为 71,385，elementary 69,655，其中 emission 67,239、resource 2,416；signed negative 共 834，deeper edge 共 793，对应 200 个去重 depth-4 Provider。当前层路由权重为 `100.000000000000%`，selected-next count 为零，materializer 不输出 batch-8 slice，路线改为 `close-uslci-unit-provider-current-breadth-layer`。

这里的“当前层闭合”只表示原 313-ID frontier 已全部观察，不能提升为 whole-provider-network closure。真实 input version `9e04a3c576ea085a`，batch version `b983a3f571faf642`，报告 SHA-256 `8cddf3646e3b788044d0a6cf62e7934a753469a257f8488c28930befcf9e05f3`。最后一拍只有五个 input binding、13 个 raw envelope、report 加六个 detail outputs，共 25 个工件；重放保持事件 `2829→2829`。创建、native invocation、连接/修改、外部写、comparison 与 LCIA 仍为零。唯一下一路线是编译 current-breadth-layer closure。

第四十九拍新增 `uslci_unit_provider_current_breadth_closure/v1`。编译器重新绑定原 313 行 frontier、最终 batch-7 report、精确有序 observed-ID ledger 和 200 行 depth-4 frontier。它拒绝 ID 重复、乱序、当前层与 depth-4 重叠、report/artifact hash 漂移、非零 same-layer remainder、伪造 batch 8 或任何副作用位放宽。Project 与编译器共用同一 ordered-binding input-version 函数，因此报告、活动目录和机器状态的 input version 都是 `39349c7b5394d31e`，避免恢复身份分裂。

闭合结论不是单一模糊布尔值。七条 satisfied assertion 分别证明 313-ID source frontier、ordered observed ledger、零剩余、当前层 100% 路由权重、313/313 elementary visibility、无 batch 8 和零副作用；四条 retained blocker 分别保留 200 个 depth-4 Provider、whole-network non-closure、834 个 signed-negative review marker 和未证明的 CN/科学比较适用性。输出显式分开 `current_breadth_layer_closed=true`、`whole_provider_network_closed=false`、`scientific_comparison_ready=false` 和 `comparative_lcia_allowed=false`。

Depth-4 排序按 793 条 incoming-edge structural occurrence 聚合，降序后用 Process UUID 打破并列。首批选择 50 个、覆盖 602 条 occurrence，即 `75.914249684741%`；另 150 个保持可寻址。该统计只解释采集顺序，不是质量、能量、因果贡献、环境影响或 LCIA。真实 closure version `47ad943e38bc28be`，report SHA-256 `674879b3e0c89a3150f64de292bed526d0b5f7189a0e5496f9cfd290c7d86cb8`；八个工件逐项注册，重放保持事件 `2857→2857`。

这一拍继续复用并具体落地此前迁入的高星工程哲学：NetworkX 的显式层/frontier 分离落为 immutable current ledger 与独立 depth-4 frontier；Great Expectations 的 expectation/result 边界落为 11 条带 observed/expected 的 assertion ledger；Iceberg/dbt/DVC 的不可变 snapshot、唯一前缀和 content-hash 失效落为四输入 binding、共享 input identity 与 replay。没有安装或声称运行这些完整平台。唯一下一路线是 `probe-uslci-depth-4-provider-closure-batch-1`；闭合编译本身及下一拍之外的创建、native invocation、连接/修改、外部写、comparison 与 LCIA 仍为零。

第五十拍新增 `uslci_depth_4_provider_cumulative_batch/v1`。Activity 同时绑定第 49 拍 report/ranked frontier/selected slice、313-ID closed ledger、两条 at-user/at-grid Process observation 和 67 条 generation Process observation，先证明 2+67+313+200 四个身份集互斥，再只允许 ranked 1–50 的公开读取。它输出 batch Process/exchange/inventory/Provider-edge、独立 depth-4 observed ledger、novel depth-5 frontier 和 batch-2 selected slice，共 64 个已登记工件。

真实响应推翻两个先验假设。50/50 source Process 都是 `LCI_RESULT`，不是 `UNIT_PROCESS`；27 条有效期止于 2016、6 条止于 2020、17 条止于 2023。Kernel 没有把 parser 全面放宽，而是在 `_normalize_process` 上加入显式 allowed-process-type 参数：旧层默认仍只接受 unit Process，depth-4 路径只接受 LCI result。时间门同样分开：旧层保留 2023 policy，当前层允许不晚于 2023 的有序历史期间，并在 report 中保留 year counts 和 33/50 historical marker。

50 个 LCI result 共含 50,496 条 exchange：232 product、14 waste、50,250 elementary，其中 emission Output 47,096、resource Input 3,154；所有 Process 均有 elementary inventory，103 条 signed negative 原样保留。没有任何 exchange 声明 `defaultProvider`，因此 Provider edge、back/cross edge 和 novel depth-5 frontier 都为零。这表示这些源记录是聚合清单结果，不表示上游负荷为零，也不能在只观察 50/200 时宣布 whole-network closure。

Attempt 1 在真实网络读取后以 unit-type assumption 被拒绝；attempt 2 再次完成 50 个响应后以 2023-only assumption 被拒绝。为避免第三次重复消耗公共服务，attempt 3 逐个校验 route/status/payload ID 后复制 attempt-2 envelope 到新的 immutable activity，并显式记录 `network_read_executed_count=0`、`response_reuse_count=50`；失败 attempt 仍完整保留。真实 input `a47ea56e1822eaf0`，batch version `32eb0a7f2bfc8d6f`，report SHA-256 `14af4cbb102b442372e47c027d43fc58df5440968f0e40b0ba11dcfe9c979757`；重放保持事件 `2931→2931`。

Scrapy 的 bounded request/retry/cache 思路具体落为 50-ID driver、failed-attempt raw cache 和逐 envelope reuse validation；OpenLineage 的 dataset facet 分离落为 Process type/time/inventory/edge 四类事实；NetworkX 的 visited/frontier 思路落为 582-ID scheduled universe 与 back/cross/novel edge 分类。没有安装这些完整系统。唯一下一路线是 `extend-uslci-depth-4-provider-closure-to-batch-2`：参数化剩余三批，保持 depth-5 deferred、创建/native invocation/连接/修改/外部写/comparison/LCIA 为零。

第五十一拍新增参数化 `uslci_depth_4_provider_continuation`，复用 batch-1 的 LCI-result/type/time/edge 编译合同运行 batch 2–4。稳定输入是 ranked 200-row frontier、立即前序 report、cumulative observed ledger、允许为空的 cumulative depth-5 frontier、selected slice、313-ID layer-3 ledger、2 条 probe Process 和 67 条 generation Process。Runner 拒绝 batch 跳跃、prefix 重排、artifact binding 漂移、scheduled-universe overlap 和权限放宽；空 JSONL 是有效 hash-bound frontier，不会因“零行”被误判为丢失文件。

Batch 2 新增 63,835 exchange，达到 100/200；input `c6ad44f073a8e4dc`、batch version `e9abac45c669958f`、report SHA-256 `80a2e604307cd126b997dfd0c22aec9de8ba2c22cd6f8b6a6df70cda3ce5e60c`。Batch 3 新增 54,786 exchange，达到 150/200；input `c029848ca89bbe5f`、batch version `3952c9b89a56a183`、report SHA-256 `a7e8479c15d56ba88f46effb1a1f611ceb9c9fa12a36a535386d955174aef40a`。Batch 4 新增 54,981 exchange，达到 200/200；input `aee4a93ea1c59e0a`、batch version `64c25409484bcf3b`、report SHA-256 `f24be02bad2f59c8efe0fc88a516365d379af143f52c045d2d46bc8ea3b3d077`。

累计 200 个 Process 全部是含 elementary inventory 的 `LCI_RESULT`，共 224,098 exchange：460 product、20 waste、223,618 elementary，其中 emission Output 215,522、resource Input 8,096；611 条 signed negative 保留。有效期止年累计为 2016:34、2020:85、2023:81，119/200 是历史结果。四批 Provider edge 都为零，depth-5 frontier 始终是空内容 SHA `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。这支持下一拍评估 bounded graph termination，不直接证明全球数据完备、CN 代表性或环境影响。

Batch 2/3 各注册 66 个工件，final batch 4 因不生成 batch-5 slice 注册 65 个；latest replay 保持事件 `3146→3146`。dbt incremental 的 unique-prefix、Iceberg snapshot 的 immutable batch + latest metadata、DVC content-hash 的 exact invalidation 已落为同一个可运行到 final batch 的模块/Project/CLI/测试，而非批次复制。唯一下一路线是 `close-uslci-depth-4-provider-layer`；创建/native invocation/连接/修改/外部写/comparison/LCIA 均为零。

第五十二拍新增 `uslci_depth_4_layer_closure/v1`。Compiler 不是读取最后一个布尔值，而是绑定 root normalization、generation closure、313/313 breadth closure、ranked 200-row frontier、final batch-4 report、exact 200-ID ledger 和 depth-5 frontier 七份工件。每份输入均校验 path/hash/bytes、contract、scope/revision、parent route 与嵌套 artifact binding；零字节 JSONL 被当成有效的 content-addressed empty frontier。

真实 input `af9cc89f84b21037`、closure version `27efd68c0632bdba`、report SHA-256 `545cc6b357a4fb8b5ba7c85f4ad9cbca5131e927fba3f0433c7ab17046a07334`。十二条结构断言通过：200-row rank、200-ID ordered ledger、四个精确 50-row batch boundary、零 remaining、100% structural occurrence coverage、200/200 `LCI_RESULT`、200/200 elementary visibility、无 batch 5、零 side effect、零 depth-4 Provider edge、显式空 depth-5 frontier 和 bounded termination。七条 blocker 继续保留 aggregated-result upstream semantics、global absence、119 个历史结果、CN 代表性、Flow/unit mapping 与 characterization、1,445 个 signed negative、comparison/LCIA。

闭合 manifest 把已接受 lineage 明确写成 582 个互异 Process：root 2、generation 67、unit 313、terminal LCI result 200；Provider-edge occurrence 为 1,174：68 + 313 + 793 + 0。由此只发布 `bounded_uslci_provider_graph_structurally_terminated=true`，同时强制 `global_provider_absence_proven=false`、`zero_upstream_burden_proven=false`、`scientific_comparison_ready=false`、`comparative_lcia_allowed=false`。十个工件全部登记，首次接受事件 `3146→3162`，重放 `3162→3162` 且输出逐字节一致。

NetworkX 的 directed frontier termination 具体落为 exact ledger + explicit empty frontier + lineage-scoped boolean；Great Expectations 的 definition/result 边界落为 19 条 observed/expected/status/explanation ledger；OpenLineage 的 dataset/run/facet 分离落为七份输入 dataset、一个本地 compiler activity 和 structural/temporal/scientific facets。没有安装或虚报三个完整平台。唯一下一路线是 `compile-uslci-background-inventory-integration-plan`：先把不可变 layer-local Process/exchange 工件、`UNIT_PROCESS`/`LCI_RESULT` 类型、Flow/unit/Provider mapping 与最小 native slice 编成计划，再讨论创建或连接；当前创建、native invocation、连接/修改、外部写、comparison、LCIA 仍为零。

第五十三拍新增 `uslci_background_inventory_integration_plan/v1`。Project 不从当前 summary 猜测源数据，而是扫描 accepted event history，按 layer/batch 选择最新接受 payload，再把 root 1 份、generation 3 份、unit batches 1–7 各 4 份、depth-4 batches 1–4 各 4 份编成 48-row source-artifact manifest。每行固定 `layer:batch:role` key、Process-type policy、预期行数、path/hash/bytes；compiler 在 workspace 边界内重新验证所有文件，拒绝 path escape、重复/缺失角色、行数漂移、类型漂移和 hash 漂移。

真实库存扫描产生 582-row Process ledger：root/generation/unit 合计 382 个 `UNIT_PROCESS`，depth-4 为 200 个 terminal `LCI_RESULT`。root/generation 使用 Process observation 内嵌 exchange，后两层使用独立 batch exchange 工件，逐 Process 复核 exchange count 后共得到 295,933 条 observation。Provider edge occurrence 为 root 68、generation 313、unit 793、depth-4 0，总计 1,174；这延续闭合图的结构语义，不把 terminal aggregated inventory 变成可递归单元过程。

Flow ledger 识别 2,300 个唯一源身份：21 product、88 waste、2,191 elementary，定义冲突为零。两条既有电力 proposal 只以 `proposed_not_applied` 复用；其余 2,298 条进入 native identity-preflight/authoring 队列。九个 unit context 全部得到确定性但未应用的 conversion recipe，其中六个 identity、MWh→MJ `3600`、kg*km→t*km `0.001`、short ton→kg `907.18474`。Flow mapping、unit conversion、characterization 与 geographic applicability 是四个不同门，不因数学上可换算而合并。

最小 slice 通过完整 Provider path 的稳定字典序选择 at-user、at-grid、generation、unit 与 terminal LCI result 五个 Process。该路径实际覆盖 1,459 个 unique Flow 和五个 unit context，说明“最少 Process”不等于“少量身份预检”。stage plan 因此先进入 bounded native preflight，而不是直接 materialize 582 个 Process。TianGong context-pack manifest 对 Schema/ruleset 的 hash 忽略单个末尾换行，而登记文件保留该换行；兼容层只允许 exact one-LF packaging normalization，真实计数为 2，不放宽 Activity file binding。

dbt-core 的 manifest/source/model 依赖落为 48-row manifest、唯一 artifact key 和 content-addressed dependency identity；OpenRefine 的 serializable operation history 落为 source/proposal/factor/loss/application-status 分离；Helm 的 render→lint→dry-run→apply 落为 plan→five-Process slice→native preflight→gated batch materialization。机制进入 compiler、Project/CLI、reducer、双投影和 tests，完整外部平台未安装。真实 input `6e702df377080a37`、plan version `5300ed97c5f30065`、report SHA-256 `cd9efbd62f9c48a32be80c709b566e5ab43cd39255f8690ebe933070a359f4fd`；19 个工件登记，事件 `3162→3187`，重放 `3187→3187`。唯一下一路线是 `preflight-uslci-native-integration-slice`；网络读取、Flow/Process 创建、native invocation、连接/修改、外部写、comparison 与 LCIA 均为零。

第五十四拍新增 `uslci_native_slice_preflight_plan/v1`。Compiler 不从“五个 Process”推测工作量，而是重新绑定 integration report、selected slice、Flow/unit requirements、normalization report、13-Flow 与 2-Process 本地候选目录、portable runtime report/manifest 九份工件。它逐项校验 path/hash/bytes、parent route、nested artifact binding、Process/Flow/unit 覆盖、source Flow type、单位换算和 runtime tree，避免将旧摘要或名称相似当作可执行事实。

1,459 个 Flow 动作按两段稳定排序：五个 Process 中去重后的三个 quantitative-reference Flow 按 Process 出现顺序优先，其余按 source UUID 字典序。动作被切成 15 批，每批上限 100，声明的 native concurrency 上限为 4。第一批正好 100 个并包含全部三个 reference Flow。五个 Process identity action 和五个 build-plan template 显式依赖对应 Flow 决策，总 action graph 为 1,469 nodes/15 edges，不允许 Process 门跳过 reference Flow 门。

每个 action key 绑定完整 request、两个候选目录哈希和 copied TianGong runtime tree，只有内容完全一致才可复用。既有两个 proposal 保留原 target UUID；其余目标使用确定性本地 UUIDv5，且强制不等于 foreign source UUID。Airflow dynamic task mapping/pools 落为稳定扩展、15 批与四 worker 上限；Argo explicit artifacts 落为 request/candidate/runtime/dependency binding；Bazel action cache 落为 content-derived key 与 output-manifest reuse。三个完整平台都未安装。

真实 input `a1f025411d0b6328`、plan version `393d17f2d7539f12`、report SHA-256 `323233b39d9c71401b49dae80e497035ed08b7d926897bf3215470d357b31502`。十六个工件全部登记；首次接受事件 `3187→3209`，重放 `3209→3209`。这一拍只编译可执行预检计划，没有 native invocation、Flow/Process 创建、build validation、连接/修改、外部写、comparison 或 LCIA。唯一下一路线是 `execute-uslci-native-slice-preflight-batch-1`。

第五十五拍新增 `uslci_native_slice_preflight_batch/v1`。它不把 100 个 Flow 扩展为 100 个 Project activity，而是在一个 Activity 中以 `ThreadPoolExecutor(max_workers=4)` 调度 action-key 目录。每个 action 都将 canonical request 送入 copied Node/TianGong CLI，只传入已绑定 13-Flow catalog 的 `--candidate-input`，不传 `--remote-candidates`，并从子进程环境移除远程凭证变量。子进程输出还要经过目标字段、绝对输入/输出路径、candidate/report 一致性、唯一 file source 与 13-row coverage 检查，命令完成与 Kernel 语义接受仍是两个事实。

真实执行发现 TianGong CLI 0.0.25 的重要合同语义：`flow identity-preflight` 只有 `status=passed` 返回退出码 0，完整的 `needs_review` 报告返回 1。初始 batch logic 把非零统一当作 command failure，导致 98 个有效 manual-review action 被多重试一次。根因由已固定 CLI 源码 6419–6422 行和原始 report 共同证明。Logic `2026-07-17.2` 只对 `{0,1}` 之外的退出码或缺失输出重试，0/1 均先做语义校验；fake CLI regression 明确覆盖该差异。早期 action-key 字段名假设错误和 exit-code 错误的工件/事件都保留，没有覆盖。

最终 100/100 action 均语义验收，原生调用 100 次、重试 0、观察峰值并发 4。每个 action 都读取全部 13 个本地候选，因此候选 observation 为 1,300，file-source observation 为 100。决定分布是 `reuse=1`、`create_new=1`、`manual_review=98`；后者是有效原生决定，不是命令失败，也不自动转为人类停点。

三个 quantitative-reference Flow 中，120 V electricity 以 high-confidence 复用已有 `Electricity, process`；high-voltage electricity 与 `power plant construction` 均为 low-confidence manual review。前者的最高候选只有 score 30 的泛化 energy Flow，后者的最高候选只有 score 15 的同 Product-flow type，都不足以证明等价复用。因此 reference automatic passed 为 1/3，不允许直接进入 Process build-plan 或 batch 2。

原始 action tree 共 600 个文件，先编译 path/bytes/SHA-256 manifest，再用 lexical order、mtime 0、uid/gid 0 和固定 mode 打包为确定性 tar；Project 只登记五个 input binding 和六个 aggregate output，避免事件/上下文线性增长。真实 input `ba9c66bb31ce66f2`、batch version `a4fea8d098393094`、report SHA-256 `9f11543c3c331911616efb5d71e1b92ee6fad354fe6ce5fb968f377116c8dd9c`、raw archive SHA-256 `4c4b64c5788d072227275c52f2f0f1696d52ceca94f06477a1eac192812e100c`；重放事件保持 `3247→3247`。唯一下一路线是 `resolve-uslci-native-reference-flow-preflight-blockers`；由 AI 自主解析两个 reference Flow，而不请求人工决策。

第五十六拍新增 `uslci_reference_flow_resolution/v1` policy module。它把 batch report、三行 reference result、首批 action、action graph、600-row manifest 和 raw tar 作为六个不可变 materials；tar 只能包含 canonical regular members，path/bytes/SHA-256 必须与 manifest 集合完全一致。Resolver 随后交叉核对 action/request/result/raw decision/candidate/source/semantic records，避免聚合摘要替代原始证据。

Policy 不使用相对最高分作为复用阈值。每个 manual-review candidate 都生成类似 Great Expectations validation result 的 assertion：expected target semantics、observed candidate fields、逐项 passed 和 failure reasons。高压 electricity 的 13 个候选均不保留完整电压/来源名称与 reference unit；power-plant-construction 的 13 个候选也不保留 Number/Item(s) 语义。两组共 26/26 assertion 失败，因此保留既有 target UUID 为 `create_new_intent`，而不是复用弱候选。120 V native reuse 则保留 selected dataset ID 与两个 semantic-loss marker。

OPA decision-log 的 input/policy revision/decision ID/result 机制落为六个 input binding、`policy_version`、content-derived resolution/decision IDs 和三行决策账；in-toto materials/products 机制落为六输入、四输出及 tar/manifest integrity check。没有安装 OPA、GX 或 in-toto 完整平台。

Action graph 的十条 reference edge 被投影成两层门：十条 `identity_decision_gate` 均 released；120 V 复用的两条 `entity_availability_gate` 为 ready，两个 create-new intent 的八条为 `waiting_for_flow_authoring`。身份科学判断、实体存在和下游执行因此不会混为一谈。

真实 input `35c66e55e98bc067`、resolution `1b934b495ef8b46b`、report SHA-256 `debef776d4b2f646ba6f6c869b433971d517b33365c6d76a2d763e2ed8163332`；十个 activity artifact 登记，事件 `3247→3264`，重放 `3264→3264`。本拍 native invocation 与所有创建、连接、修改、比较、LCIA 计数仍为零。唯一下一路线是 `compile-uslci-native-reference-flow-authoring-plan`。

第五十七拍新增 `uslci_reference_flow_authoring_plan/v1`。Compiler 绑定 resolution report/decisions/gates、Process identity actions/build templates、background integration report/Flow mapping/source inventory、copied runtime report/manifest、接受 resolution event anchor 和库存指向的全部十一份 exchange-observation 工件；不从当前摘要、Flow 名称或未验证的本地 reference catalog 猜测 property/unit。

高压电力的 313 条规范化观察和 762 条 mapping 观察一致绑定 Energy `f6811440-ee37-11de-8a39-0800200c9a66`、观察单位 MWh `92e3bd49-8ed5-4885-9db6-fc88c7afcfcb` 与参考单位 MJ。电厂建设的 399 条规范化/mapping 观察一致绑定 Number `01846770-4cfe-4a25-8ad9-919d8d378345` 与 Item(s) `6dabe201-aaac-4509-92f0-d00c26cb72ab`。两个 source tuple 一致，但本地 FlowProperty/UnitGroup entity 可寻址性未证明，因此只发布 source-observed assertion，不发布 local-entity 或 publish-ready 声明。

TianGong 的技术基础名规则把高压目标修正为 `Electricity, AC, 2300-7650 V`；`USLCI US 2023` 只留在 source/time provenance。这产生六个 `invalidate_and_recompile_name_bound_action` 和两个 `release_after_entity_registered` action，旧记录不覆盖。两个 saved build plan 使用 native category `1/17/171/1710` 与 `5/53/532/5326`；17-node/16-edge DAG 为每条 Flow 排序 materialize report-only→semantic check→field-scoped classification apply/check→dataset validate→qa flow→save-draft dry-run→staged register，最后汇总项目结果。

Terraform saved plan 的预览/apply 绑定落为逐字节 Flow plan、content-derived plan version 和执行前 re-hash；Kubernetes Server-Side Apply 的 field ownership 落为只允许 classification decision 修改 classification 子树，其余 TIDAS 字段仍属于 build plan；TianGong CLI 0.0.25 的 Flow materialize/classification/validate/QA/dry-run 落为可执行 DAG。没有安装 Terraform 或 Kubernetes。

真实 input `37f215f9c29daccd`、plan `22925ce7fba5f6cb`、report SHA-256 `461a958e844f689fb94832e4c3ac7047c9ec76054346c6cd125f16d220e0a324`；31 个 activity artifact 登记，事件 `3264→3302`，重放 `3302→3302`。该拍 native invocation、network read、Flow/Process creation、draft save、connection/mutation、external write、comparison 和 LCIA 均为零。唯一下一路线是 `execute-uslci-native-reference-flow-authoring-plan`：只在 activity-local staging 内执行保存 DAG，不执行 remote save 或 publication。

第五十八拍新增 `uslci_reference_flow_authoring_execution/v1`。Executor 在执行前后重验 copied runtime tree，并逐字节复核 plan report/index/DAG/classification/support/patches、两份 saved Flow plan 与 runtime report/manifest 十份输入。两条 Flow 分支以最大并发二隔离运行；每条依次执行 native materialize report-only、Kernel 语义核验、只含本目标一行的 classification apply、classification-only delta 核验、native Flow validation、Flow QA、save-draft dry-run 和 staged registration。聚合两行分类账不能直接送入单 Flow：真实原生探针会因另一目标不存在而返回 blocker，因此 branch-local filtering 是显式适配器，不是静默放宽。

真实执行完成 10 次 native 调用、17/17 DAG trace node、2/2 staged Flow 和 8 条 downstream transition；92 个原始输出被 path/bytes/SHA-256 manifest 约束并打成确定性 tar。高压电力 Flow SHA-256 为 `1bd5a0254459b9087de03d53fcf0068979f731dcf7046fdc0810d9bab27818ba`，电厂建设 Flow 为 `862bf5228ae479de73204d9c69e92405d8107df437fbdcca1e3b86c0619893aa`。六个高压 name-bound action 转为 `ready_for_recompile`，两个建设 action 转为 `released_to_local_validation`；分支失败合同会保留已完成证据但拒绝提交 current projection。

Argo DAG 的依赖/terminal semantics 落为 17-node explicit trace、两条隔离分支和全分支终态收集；Bazel action cache/CAS 的 input-command-environment-output 身份落为十输入绑定、sanitized environment、执行版本与逐输出哈希。两个完整外部平台都未安装。真实 input `97bea799ab7eeab1`、execution version `5d4147761435b2ed`、report SHA-256 `260c5a59d440d8b73d1413ea605c1e1dc12416bfb3f069933482dd86ea093d89`；19 个 activity artifact 登记，事件 `3302→3328`，重放 `3328→3328`。catalog/remote Flow creation、actual draft save、Process creation、connection/mutation、external write、comparison 和 LCIA 均为零。唯一下一路线是 `recompile-uslci-reference-flow-dependent-process-actions`。

第五十九拍新增 `uslci_reference_flow_dependent_process_action_recompile/v1`。Compiler 绑定 Beat 58 execution report/transition/two staged Flow、Beat 57 plan report/patch、Beat 54 preflight report/five identity actions/five build templates/action graph，以及 copied runtime report/manifest十二份工件。它重验 nested binding、staged Flow UUID/name/type/property、八个 gate transition、原始 1,469-node graph 中十个 Process node/十五条依赖和每个 patch 的 source-action canonical SHA。

高压电力的三条 identity action 与三条 build action 只允许把 `Electricity, AC, 2300-7650 V, USLCI US 2023` 改为 `Electricity, AC, 2300-7650 V`，再做必要的 successor dependency wiring；真实逐字段差异检查六次全部精确相等。Successor keys 分别为 identity `fadd83ec80ab3d06`、`f4fda40bb6619fec`、`48bc62cfb5a8c737`，build `907b639185cfb8b5`、`a257e809df74d1a4`、`9035c2f219695f7c`。Construction 的 `e654f8eca7180b63`/`b4771156beef4350` 与 120 V 的 `118f357a7339a2cc`/`3484d6653c24e6e5` canonical bytes/key 不变。

Kubernetes controller 的 desired/current 与 replacement-object 机制落为 `desired_generation`、`observed_generation`、predecessor/successor 和四类 condition；Bazel narrow action invalidation 落为 predecessor key/hash、patched content、staged Flow hash、candidate catalog 与 runtime tree 共同派生 successor key。没有安装 Kubernetes 或 Bazel。真实 input `d07d20f697500e4d`、recompile version `78bd6044004b6277`、generation `90e9e64a513736e8`、execution-plan version `9b5a4b3e5857e445`、report SHA-256 `bc23ecfd43192eded1d9ba54dac164f28c499fd125c68e412328f10cf568d3d9`；20 个工件登记，事件 `3328→3355`，重放 `3355→3355`。本拍全部 native/creation/write/connection/comparison/LCIA 计数为零，唯一下一路线是 `execute-uslci-native-slice-process-action-plan`。

第六十拍新增 `uslci_native_slice_process_action_execution/v1`。Executor 绑定 recompile report/plan/successor/release/carry/reconciliation/condition、原始五组 Process action、两行候选目录、两份 staged Flow 和 copied runtime 共十五份输入，重建五个 desired identity→build 分支，并在执行前后验证完整 runtime tree。五个分支最大并发四；native identity exit 0/1 只有在 decision/candidates/sources 三份输出完整且语义绑定时才可继续。`manual_review` 仅在逐候选名称、参考 Flow、地域和技术路线断言全部拒绝等价时自动转为 `create_new`；duplicate/equivalent/schema/build blocker 均 fail closed。

五个接受的身份决定被编译为 `declared_unit_dataset`、参考量 1、严格 ruleset、七个来源字段绑定的最小可执行 build plan，再逐一运行 native `process build-plan validate --report-only`。真实结果为五个自动 `create_new`、十条非等价候选断言、五个 7/7 strict gate pass；十次 native 调用完成十个 trace node，61 个 raw files 打入确定性 archive。Argo 的 explicit DAG/artifact 与全分支终态收集落为 saved plan、隔离目录和 terminal collection；OPA decision log 落为 policy version、candidate assertions、native/final decision 与 decision ID。没有安装 Argo 或 OPA。

真实 input `749578b3a1745334`、execution version `e3c4fce38a8a0397`、report SHA-256 `860314d46fef987053acf197c3f5d3a82b2de9b584b66d5a04e69d3d68a9c2d3`；25 个 Activity artifact 登记，事件 `3355→3387`，重放 `3387→3387`。Process/Flow materialization/creation、save、network read、connection/mutation、remote/external write、publication、comparison 和 LCIA 均为零。最低作者门不等于科学清单就绪；仍有 1,359 个 Flow action，唯一下一路线为 `execute-uslci-native-slice-preflight-batch-2`。

第六十一拍新增 `uslci_native_slice_preflight_continuation/v1`，把只支持 batch 1 的原型扩展为 batch 2–15 的 append-only continuation。每批绑定 plan report、完整 1,459-action ledger、15-row descriptor ledger、前一批 report、当前 route-authority、13-Flow catalog 和 copied runtime report/manifest八个角色；从 descriptor 重建唯一连续切片并逐项复算 action key。Batch 2 的 authority 是 Beat 60 Process execution，batch 3 起由前一 continuation 同时承担 prior/authority，因此 reference-Flow detour被明确建模而不是从 conversation 猜测。

Reducer 只把最新 batch 投影到 Hot/Machine view；`batch_index>1` 不再失效已经接受的 reference resolution、两份 staged Flow、Process successor plan 和五个 minimum gate。旧 batch event/artifact 仍在无损历史中。Human view 动态呈现实际批号；Machine view保留累计位置、remaining、decision counts 和 artifact pointers，不复制 600-file raw tree。

真实 batch 2 精确执行 global positions 101–200：100 次 native call、无 retry、峰值并发四、100/100 semantic accepted、13×100=1,300 candidate observations、100 source observations；决定为 `create_new=1`、`manual_review=99`，raw files 600。Accepted input `9f9f933f7d1b56ee`、continuation `b3f00ec6eb77be20`、report SHA-256 `997469dd58f41ba18cb6cf158c835e794015365ec30b9b18c4bf7a86839129fc`，14 个 Activity artifact 登记。前两个 attempt 因 direct-script shared-runner import path 失败，均在 native invocation 前终止并保留 immutable failure evidence；第三个 attempt 以显式 package-root bootstrap 修复 harness。含失败证据的事件为 `3387→3412`，重放 `3412→3412`。全部 creation/materialization/network/remote/connection/write/comparison/LCIA counter 为零，唯一下一路线是 batch 3。

第六十二拍用同一已验收 continuation 连续执行 batches 3–15，没有新增框架分叉。Canonical batch selection 必须按 batch index 取 lossless history 中最新 accepted event，避免把 batch 1 的 superseded logic 结果重复累计。十五批精确覆盖 1–1,459：1,459 semantic accepted、1,459 native invocations、accepted retry/failure 均为零、18,967 candidate observations、1,459 file-source observations、8,754 raw files；决定总计 `reuse=1`、`create_new=5`、`manual_review=1,453`。

Batches 3–14 每批 100 actions，batch 15 为 positions 1,401–1,459 的 59 actions。Final input `e4739e4126106ef2`、continuation `4d69fc49571f9ec5`、report SHA-256 `efb7d92a8ccfaea8af0fe1c0230845bdae12771c47a83863acea993dc4ee5d49`；batches 3–15 每批登记 14 个 aggregate Activity artifacts，共 182 个，事件 `3412→3685`，final replay `3685→3685`。Reference-Flow/Process accepted chain 保持 current。全部 creation/materialization/network/remote/connection/write/comparison/LCIA counter 仍为零；下一路线是独立编译 Flow identity-resolution work plan，而不是直接批量创建。

## Beat 63：全量 Flow 身份消歧计划

第六十三拍新增 `uslci_flow_identity_resolution_plan/v1`。Compiler 从 lossless event history 按 batch index 选择最新 accepted event，而不是使用只保存最新批次的 current projection。它为十五批各自绑定 report、result ledger、action-output manifest、raw archive 和 failure ledger，重新验证 path/bytes/SHA-256、tar canonical regular-member 规则及 manifest/archive 双向完备性，再把 1,459 个 action 与原始 request、native decision、13-row candidate observation 和 file source 逐一连接。

Reference Flow 的既有 resolution 与 authoring lineage 继续作为同一决策权威：position 1 的 120 V electricity 保留 native reuse；positions 2、3 的高压电力与电厂建设采用已验收 `create_new_intent`；native preflight 的五条 `create_new` 也直接进入自动账。由此 8 条决定无需再次推断，其中 `reuse=1`、`create_new_intent=7`，两条新身份仍是 staged local Flow 而不是 catalog-created entity。

剩余 1,451 条 action 不逐条制造 Project event，而是编成两个 policy group。1,450 条为 `bounded_catalog_entity_type_mismatch`：目标是 Elementary Flow，而绑定的 13 个本地候选全为 Product Flow，因此在当前有界目录内可确定拒绝复用；这不声称远端或全局无重复。唯一例外是 global position 443 的 Product Flow `petroleum fuel, through transportation`，其两个最高候选只共享 Product/Energy 结构，不保留石油载能体或运输服务语义，因此进入 `product_identity_semantic_review`。十五个 resolution batch 保持每批最多 100 条；batch 1 先放 position 443，再放 99 条硬类型不匹配，以便最早验证复杂策略分支。

Bazel 的 explicit action identity 具体迁移为 source action key、batch-artifact hashes、policy revision 和 evidence profile 共同派生的 resolution action；OPA decision-log 迁移为 policy group、input binding、decision intent、risk marker 与稳定 decision ID；Argo explicit artifacts/terminal collection 迁移为十五个有序 descriptor、完整 failure ledger 和 batch-level output contract。没有安装或虚报三个完整平台，机制只进入 compiler、Project/CLI、reducer、双投影和 tests。

真实 input `4f9aae15f311cf20`、plan version `231e974f95cb4b81`、report SHA-256 `3a0bc20938d73e9a23c8ebdd97a980015f8c00954138fb9cf4440877abac4ded`；20 个 Activity artifact 登记。两个失败 attempt 分别暴露 Project 辅助整数读取函数作用域错误，以及 direct-script package bootstrap/catalog tuple 比较错误；修复后接受事件为 `3685→3716`，重放 `3716→3716`。本拍 native/network/creation/materialization/save/mutation/write/comparison/LCIA 均为零。唯一下一路线是 `execute-uslci-flow-identity-resolution-batch-1`。

## Beat 64：首批 Flow 身份自主消歧

第六十四拍新增 `uslci_flow_identity_resolution_batch/v1`。Executor 绑定 plan report、verified native-batch index、evidence profiles、automatic decisions、resolution actions、policy groups、resolution descriptors、空 failure ledger、动态 source-evidence index 与 route authority 十个角色。Batch 1 的 100 个 action 实际来自原生 batches 1、2、5；执行器重新哈希三份 action-output manifest 和 raw tar，要求 1,800 个 member 全部是 manifest 声明的 canonical regular file，再读取每个选中 action 的 request、native decision、candidates、candidate source、execution 与 semantic-validation 六份原始证据。

99 个 `bounded_catalog_entity_type_mismatch` action 各生成 13 条候选断言；目标均为 Elementary Flow，候选均为 Product Flow，因此类型身份已足够拒绝复用。唯一 Product action 是 position 443 的 `petroleum fuel, through transportation`。Natural-gas heat 与 electricity 虽然同为 Product/Energy、native score 30，但名称不保留 petroleum carrier identity，且候选 `reference_unit=null` 不能证明与 MJ 等价；其余十一项还存在 Mass/Energy 差异。十三项均不支持等价，所以结果是保留目标 UUID 的 `create_new_intent`，并留下 high-priority review marker 与 `remote_duplicate_unknown`，不暂停后续批次。

OPA decision-log 机制具体落为 100 行 policy/version/input/assertion-digest/result 决策账和 1,300 行 observed/expected/non-equivalence assertion；Bazel action identity 落为十输入 batch version 与逐 action decision ID；Argo artifact/terminal collection 落为三份显式 source archive、100 action 全终态收集和一个 aggregate Project event。完整外部平台均未安装。

首次执行暴露原始合同适配错误：Kernel 误读 `execution.returncode` 与 `semantic.accepted`，而真实原生工件使用 `execution.exit_code=1` 以及 `status=needs_review`、decision/candidate/source hashes。该 attempt 在输出前失败并保留；修复后新增回归测试覆盖 exit-code-one 的完整语义结果。接受 input `2f0b7003e5f2ed11`、batch version `9f37a9adcba18112`、report SHA-256 `95885223bdca819c52da5aafe804f50543bb97eb88115062fff0b07406a42f8d`；17 个 Activity artifact 登记，事件 `3716→3742`。

本批 100/100 决定均为 `create_new_intent`，candidate equivalence supported 为 0，累计已决 108，剩余 1,351。Native invocation、network/remote、Flow/Process creation/materialization、save、model connection/mutation、external write、comparison 与 LCIA 都是零。唯一下一路线是把同一执行合同推广到 `execute-uslci-flow-identity-resolution-batch-2`，并用 batch 1 report 作为 immediate-prior authority。

## Beat 65：全量 Flow 身份决定闭合

第六十五拍将同一 `uslci_flow_identity_resolution_batch/v1` 从首批推广到 batches 2–15，没有复制十四套执行器。Batch `N` 只能由 accepted batch `N-1` 的 report 授权；Project 从 lossless history 取 exact prior report，而 current projection 只保留最新批次。重放当前批次仍复用原 input identity，不追加事件。Descriptor action count、policy-group count、source native-batch selection、output filenames、cumulative progress 和 next route 全部由内容推导；batch 15 自动使用 51-action tail。

Batches 2–15 都只有 `bounded_catalog_entity_type_mismatch`。每个 action 仍重新读取十三项 candidate evidence 并输出逐候选 assertion，而不是因为 policy 相同就跳过原始证据。Resolution batches 1–15 合计 1,451 action、18,863 assertion、8,706 个 selected raw file；因 descriptor 跨原生 batch 边界，三十次 source-archive verification 合计复核 17,508 个 member occurrence。该数字是重验证发生次数，不应误写成 17,508 个唯一原始文件；唯一原始 corpus 仍是 8,754 files。

最终 decision positions 精确覆盖 1,451 个互异位置；未出现在 batch 决策中的八个位置为 `[1,2,3,88,143,408,440,590]`，与 automatic-decision ledger 完全一致。合并后 1,459 个 Flow 身份全部闭合：`reuse=1`、`create_new_intent=1,458`。其中两个 reference Flow 已经 staged，另外 1,456 条只是 authoring intents；身份决定闭合不等于 entity-ready、support-reference-ready 或 publish-ready。

首个 batch 2 Project 汇总曾在 activity 已成功后暴露 acceptance-local 变量作用域错误；修复没有重跑 activity，随后 current-batch replay 和最终 batch-15 replay 都保持事件不增长。Focused tests 新增 immediate-prior authority 与 51-action tail progress coverage。Final input `25adaa14dc0a6ee4`、batch version `4e02142df6c748df`、report SHA-256 `6634a17296f59bf7fe41aab6a1dd84369cd040d11613d3342eec5e1f1f5d630f`；batches 2–15 接受事件 `3742→4078`，replay `4078→4078`。

全批次 native invocation、network/remote、Flow/Process creation/materialization、save、model connection/mutation、external write、comparison、LCIA 均为零。唯一下一路线为 `compile-uslci-native-slice-flow-authoring-plan`：先合并 automatic/batch decisions，保留 reuse 与两条 staged branch，再按 Flow type/property/unit/support readiness 编译其余 1,456 条 authoring intent，而不是直接批量写入 TianGong。

## Beat 66：全量 Flow 原生创作计划

第六十六拍新增 `uslci_flow_authoring_plan/v1`。Project 从无损事件历史按 batch index 选择十五个最新 accepted identity-resolution batch，并为每批绑定 report、decision、candidate assertion、action evidence、source-archive verification 与 failure 六种工件。Compiler 重新计算全部 path/bytes/SHA-256，复核十五批 immediate-prior authority chain、1,451 条 batch decision、18,863 条非等价 candidate assertion，再与八条 automatic decision 合并；positions 1–1,459、source Flow ID 和 deterministic target UUID 必须各自完整且唯一。

身份决定与作者合同保持分层：一个 reuse 和两个已通过本地原生门的 staged Flow 作为 preserved branch，不重新创建；其余 1,456 个 `create_new_intent` 被编为不可变 authoring intent。每个 intent 同时绑定 source provenance、目标 UUID/Flow type/FlowProperty/reference unit、USLCI coarse classification、单位恒等转换和 support-reference readiness。`WASTE_FLOW` 不得静默转成 Elementary Flow；精确 TianGong classification 仍未知时只允许进入需求账，不允许生成已授权实体。

真实分组为五类：elementary emission Mass/kg 1,396、elementary resource Mass/kg 46、elementary resource Energy/MJ 8、elementary resource Area*Time/m2*a 5、technosphere Product Energy/MJ 1。支撑引用压缩为三类需求：Mass/kg 1,442 条绑定 TianGong builtin Mass UUID 但仍需本地 addressability probe，Energy/MJ 9 条和 Area*Time/m2*a 5 条保留 source-observed UUID、等待本地实体确认。分类需求压缩为 emission 1,396、resource 59、technosphere product 1；三类都要求 evidence-bound exact native path。

每类选择最小 position 的一个代表性 canary，共五个；剩余工作被保存为十四个 100-action bulk batch 与一个 51-action tail，连同五个 canary 组成 16 批。35-node/34-edge DAG 强制 support/classification resolution→saved-plan compile→local native dry-run→canary analysis gate，bulk 只能从分析门释放。Canary 失败只失效 bulk release，不重写已接受身份决定。

Terraform saved-plan 的 immutable intent/re-hash 机制落为 content-derived plan version 与执行前 binding；Helm render/lint/dry-run/apply 分层落为 group template、support/classification gate 和未授权实体写；Argo Rollouts canary analysis 落为五类代表样与 bulk-release dependency；OPA policy authority 落为作者阶段不得重新推翻 identity decision。未安装或虚报这些完整平台。

真实 input `8b356ea0b96ec3d9`、authoring plan version `44d477987db9e8fe`、report SHA-256 `3c5cb37a270edf298e62e7ad8c1068944760325be01d202dd107d1c3ae6f1df7`；29 个 Activity artifact 登记，事件 `4078→4114`，重放 `4114→4114` 且 report byte-stable。Planning failure 为零；native invocation、network/remote、Flow/Process creation/materialization、save、model connection/mutation、external write、publication、comparison 和 LCIA 全部为零。唯一下一路线是 `resolve-uslci-flow-authoring-canary-supports`：先消解五个 canary 的 exact classification 与本地 support reference，再编译 saved Flow plans，不直接释放 1,451 个 bulk action。

## Beats 67–72：从 canary 到首个 100-Flow 冻结计划集

Beats 67–70 完成五类代表性 Flow 的分类/支撑消解、精确 saved-plan 编译、复制运行时本地原生门禁和 typed Elementary-QA 适用性消解。Canary 不是独立演示：它证明了 TianGong Flow materialize、field-scoped classification、dataset validation、Flow QA 与 review-marker 保留的真实调用链，同时把 generic `qa flow` 对 Elementary Flow 的四个类型 blocker 原样留存并限定为精确 runtime/ruleset/UUID/finding 条件下的 not-applicable 决定。该链只释放 bulk planning，不释放实体写入。

Beat 71 只重开 authoring batch 2 的 100 个稳定 `authoring_intent_key`。每个 key 绑定一条源 observation、一个原生分类决定和同一份验真的 Mass/kg 支撑合同。源 compartment 为 air 61、water 30、ground 9；只映射到 TianGong unspecified leaves `1.3.4`、`1.1.3`、`1.2.3`。九条 ground→soil 术语差异作为低优先级标记保留，不被改写成更细类别。

Beat 72 新增 `uslci_flow_authoring_bulk_batch_saved_plans/v1`。这是 batch 专用深模块，不把 100 行伪装成 canary，也不修改五份已接受 canary 计划字节。它在一个十四输入 Activity 中完成稳定键一对一 join、Mass 合同重哈希、runtime tree 复核、100 份计划渲染、内容描述符、字段断言、review carry-forward 和未来门禁 DAG；任何缺失、重复、顺序漂移、路径漂移或 marker 丢失都会拒绝整个 attempt。

Terraform 的 saved plan/apply 分离落实为“未来门禁只能读取同一计划字节”；OCI Image Spec 的 descriptor 落实为每份计划的 media type、`sha256:` digest、byte size 与 path；Helm template/install 分离落实为 compile-only Activity 与独立 native-gate route；OPA 的 decision attribution 落实为复制并绑定 Beat 71 decision/review ID 与 canonical hash，compiler 不重新做科学分类。2026-07-18 GitHub API 观察为 Terraform 49,215 星、Helm 30,013、OPA 11,991、OCI image-spec 4,395；四个完整系统均未安装。

真实 input 为 `bd629930d6291ec3`，plan-set version `1f1196cbfa8de5eb`，report SHA-256 `67d2a573e17dd1bdca74314f4e4b454091b863548f01bf80dfff30a0384b412a`。100 个互异 stable key/source UUID/target UUID/descriptor 产生 1,262,394 bytes 计划；100/100 分类、Mass 支撑和源工件字段断言完整，九个标记保留，DAG 为 201 nodes/200 edges，failure ledger 为空。122 个 Activity artifact 使事件 `4267→4395`，重放保持 `4395→4395`。Native invocation、network/remote、Flow/Process materialization/creation、save-draft、model mutation、external write、comparison 和 LCIA 全为零。

唯一下一路线是 `execute-uslci-flow-authoring-bulk-batch-2-native-gates`：只允许对这 100 份精确字节执行隔离的本地 TianGong materialize/classification/validate/QA 门禁；实体创建、实际保存、远程操作、前景连接、比较和 LCIA 继续关闭。

## Beat 73：首个 100-Flow 原生门禁批次

第七十三拍新增可复用的 `uslci_flow_authoring_bulk_batch_native_gates/v1` 深模块，而不是把五-canary 顺序执行器复制二十遍。Project 绑定 batch-2 saved-plan report/index/descriptors、九条 review marker、100-plan workspace manifest、Beat 70 typed-QA report/applicability decisions/Product control 与 copied runtime report/manifest 十个角色。执行前逐项复核 plan media type、digest/size/path、stable key/source/target、runtime tree 和完整计划字节；任何漂移都发生在首次 native call 之前。

每个计划在独立目录中执行五条真实 TianGong CLI 0.0.25 命令：build-plan validate、report-only materialize、field-scoped classification apply、FlowSchema dataset validate、`qa flow`。`ThreadPoolExecutor(max_workers=4)` 只负责有界调度；异常被写成分支终态而不取消其他分支，聚合验收仍要求 100 个 success 和零 unexpected failure。一个 Project Activity/Event 记录整批，避免 100 个 Flow 把 event/context 线性放大。

Typed QA 不把 native blocker 删除或改成 pass。每行必须先通过 UUID/type、Mass property/version/mean value、三层精确分类和 FlowSchema 零 issue 断言，再要求 ruleset id/version/source、七条 rule ID、finding counts、target UUID、severity、rule/methodology ID、message、evidence 和 disabled LLM 与 canary 校准完全一致；否则 fail closed。100 个原生 finding 全部单独留存，100 个 scoped 决定均为 `not_applicable_to_intentional_elementary_authoring_batch`，实体创建授权仍为 false。

Kubernetes 123,790 星的隔离 job/终态收集机制落为每 stable branch 独立目录和全终态账；Bazel 25,630 星的 action/CAS 机制落为十输入 action identity 与逐输出哈希；Argo Workflows 16,832 星的 DAG/parallelism/archive 机制落为四路并发、201-node/200-edge terminal DAG 和 raw archive；OCI image-spec 4,395 星的 descriptor 机制落为 2,900 个 raw member 的 media/digest/size/path。星数为 2026-07-18 GitHub API 观察，完整外部系统均未安装。

真实 input `1e7875822eb46c53`、execution version `e6ced92ffdd64b55`、report SHA-256 `f8890751294b5212df1c0c082328f6ea5bfe6f440c73b2cf52ae979255f7112b`。100/100 分支成功、500/500 native calls、实际并发峰值四、九条 review marker 保留、failure ledger 为空。2,900 个 raw manifest path 与 2,900 个 tar member 一致，archive 为 7,024,640 bytes、SHA-256 `c229cb2135f18698eaa926e2ce4f408a6106f534a90932326a4aaa530127b70f`。22 个 Activity artifact 使事件 `4395→4423`，重放保持 `4423→4423`。

本拍的本地 materialized/classified bytes 是门禁证据，不是 TianGong catalog entity。Remote Flow creation/materialization、save-draft、network、Process creation、foreground connection、model mutation、external write、publication、comparison 与 LCIA 全部为零。唯一下一路线是 `resolve-uslci-flow-authoring-bulk-batch-3-supports`：把 batch-2 resolver 泛化为由 immediate-prior authority 驱动的后续批次合同，不重跑已接受的 100 个分支。

## Beat 74：Flow 支撑消解 continuation

第七十四拍没有复制 batch-3 resolver，而是把 `uslci_flow_authoring_bulk_batch_supports/v1` 的 batch selection、route authority、decision/risk identity、output filenames、status 和 next route 参数化。Batch 2 仍由 exact typed-QA report 释放；batch 3 必须由 contract/status/batch index/100 terminal successes/100 typed decisions/zero failure/closed entity release 全部匹配的 batch-2 native-gate report 释放。Reducer 只把 latest support batch 投影为 current，不清除已经接受的 batch-2 saved-plan/native-gate状态。

Compiler 从 16-row authoring-batch ledger 读取 exact descriptor，而不是假设连续 source positions。Batch 3 key 为 `e5c916b8a2a393cc`，100 个 stable key/source/target 均唯一；源分布为 air 56、water 33、ground 11，对应 native leaves `1.3.4=56`、`1.1.3=33`、`1.2.3=11`。三条 distinct classification path 各执行一次 copied-runtime probe，同一 Mass/kg support contract 以 canonical hash 复用 100 次，11 条 ground→soil marker 保留并继续阻止 entity creation。

第一次真实 Activity 完成后，Project acceptance 暴露 batch descriptor expected-key 变量插入到错误方法的 harness bug；修复只复用已完成 Activity，没有重跑 native probe。随后版本审计发现通用 continuation 仍沿用原 batch-2 logic `2026-07-18.1`，因此该 accepted result 保留为 superseded，并以 `2026-07-18.2` 重新生成 current result。当前 input `e275e9e180366bd4`、resolution `9c67381bacee1403`、report SHA-256 `f861e53c1c97b3311d40194d79c6239a37f0a953cf24d328443d54e1873212ed`；两次 23-artifact Activity 使事件 `4423→4483`，replay `4483→4483`。

三次 native probe 是本地 copied-runtime read，network/remote search、saved-plan compile、Flow/Process creation/materialization、save-draft、model mutation、external write、publication、comparison 与 LCIA 全为零。唯一下一路线是 `compile-uslci-flow-authoring-bulk-batch-3-saved-plans`：继续泛化 saved-plan compiler，不能覆盖 batch-2 plan bytes。

## Beat 75：不可变 Flow saved-plan continuation

第七十五拍把 `uslci_flow_authoring_bulk_batch_saved_plans/v1` 从 batch-2 常量实现改成 descriptor-driven continuation。Batch index/key/action count 由 accepted selected-batch descriptor 提供；source/classification 分布从 exact ledgers 独立计数；plan lineage、status、输出文件、DAG node id、Activity/idempotency key 与 next route 全部由同一 batch identity 推导。Project 增加 batch-specific thin wrapper 和 generic method；已完成的旧批次只能经注册 path/hash/bytes 重验后返回，不能用新 logic 静默重算。

Compiler logic 从 `2026-07-18.1` 升为 `.2`。Accepted batch-2 report、plan index、100 plan bytes 和 native-gate report 均未改写；reducer 在 batch 3 saved plans 成为 latest projection 时保留 batch-2 native-gate projection。Terraform 的 saved-plan/apply 绑定落为 exact plan bytes；Helm 的 render/effect 分层落为 zero-native compile Activity；OPA 的 decision output 落为 classification/support/review ledger 原样复制而非重推；OCI descriptor 落为每份计划的 media type/digest/size/path。2026-07-18 GitHub API 观察分别为 Terraform 49,216、Helm 30,014、OPA 11,991、OCI image-spec 4,395 星；完整平台均未安装。

真实 batch 3 input `462ab59310ab6d79`、plan-set version `ac5c9a0f3128a11b`、report SHA-256 `1af8e9d0ee1293fa49921d60412e27c0b1cb153aa9e9e31077fc5c1d939358ea`。100 个 exact plans/descriptors/field assertions 全部完整；分布为 air/`1.3.4` 56、water/`1.1.3` 33、ground/`1.2.3` 11，十一条 marker 原样携带并继续阻止 entity creation。201-node/200-edge DAG 的 batch-3 native nodes pending，failure ledger 为空；122 个 Activity artifact 使事件 `4483→4611`，public-command replay 保持事件文件与 machine projection hash 不变。

本拍 14 类 native/network/entity/save/model/external/publication/comparison/LCIA effect counter 全为零。Batch-2 saved report SHA-256 仍为 `67d2a573e17dd1bdca74314f4e4b454091b863548f01bf80dfff30a0384b412a`，batch-2 native report 仍为 `f8890751294b5212df1c0c082328f6ea5bfe6f440c73b2cf52ae979255f7112b`。唯一下一路线是 `execute-uslci-flow-authoring-bulk-batch-3-native-gates`：泛化 native executor 并只执行这 100 份 exact plan bytes，保持 entity/save/remote/model/comparison/LCIA 关闭。

## Beat 76：Flow native-gate continuation

第七十六拍把 `uslci_flow_authoring_bulk_batch_native_gates/v1` 的 batch index/key、plan/review counts、branch/DAG identity、artifact paths、Activity/idempotency identity 和 success/repair/continuation routes 参数化。Typed-QA policy 没有参数化成宽松规则：每行仍必须匹配 exact copied runtime、ruleset/version/source、完整 rule list、target UUID、FlowSchema 零 issue、唯一 finding 的 rule/methodology/message/evidence 和 disabled LLM。Project 的历史批次路径只重验已登记 Activity artifacts，不以 `.2` 重跑 accepted batch-2 `.1` 执行。

Batch 3 的每个 exact plan 在独立分支中依次真实调用 TianGong CLI 0.0.25 五次：plan validate、report-only materialize、field-scoped classification apply、FlowSchema validate、`qa flow`。四路有界并发达到实际峰值四；100/100 branch terminal success，500/500 native invocation，无 retry/failure。100 个原生 `elementary_flow_in_flow_qa`/`tidas.flow.type.required` finding 全部保留，100 个 scoped typed decision 通过，十一条 terminology marker 继续阻止 entity creation。

真实 logic `2026-07-18.2`、input `76f6ee4c68589f31`、execution version `a29b7ec7bb8cd6a9`、report SHA-256 `dd133fdf052c600a0974e468690ecf6c6e09fe11acfa3a4c5ffcb189e11cd8bc`。Terminal DAG 为 201 nodes/200 edges；2,900-row raw manifest 与 tar member/path/bytes/hash 逐项一致，archive 7,024,640 bytes、SHA-256 `b174ccb7d05179da96ec5b5a42fbda7c1499495f6c46ec0dffdc1b7057b80ec2`。22 个 Activity artifact 使事件 `4611→4639`；public replay 保持 event SHA `399f9172a672e890ee38dd1af5883b86c0ccff32f688f2bec7de9afd7e33974b` 与 machine SHA `41a4b003a0d5cef13b818906a6cb5b404c0721174008fbaec96c6d8b84af9cad` 不变。

Kubernetes isolated terminal branches、Bazel exact action/CAS、Argo bounded DAG/archive 与 OCI raw descriptors 继续以可运行模块/contract/test 落地，完整外部系统未安装。Network/remote search、Flow/Process entity creation、remote materialization、save-draft、model connection/mutation、external write、publication、comparison 与 LCIA 全为零。唯一下一路线是 `resolve-uslci-flow-authoring-bulk-batch-4-supports`：复用 generic support resolver，增加 thin Project/CLI route 并要求 batch-3 native report 为 immediate-prior authority。

## Beat 77：Flow 支撑消解 batch 4

第七十七拍没有改写 `uslci_flow_authoring_bulk_batch_supports/v1` 的深模块或版本，只为 Project/CLI 增加 batch-4 薄路由，并把 Project 的可调用范围与模块已有的 2–16 批合同对齐。策略测试同时覆盖 batch 3 和 batch 4：后续批次必须由 `N-1` 的 exact accepted native-gate report 释放；batch index、100 success、零 failure、100 typed-QA pass、closed entity release 与唯一 next route 任一漂移都 fail closed。

真实 batch key `6d2ca0cc83582115` 选择 100 个唯一 stable intent；input `088fee0f9372af2a`、resolution `0f6fa9f36f80a45f`、report SHA-256 `8bc80521b94ddda32a0765352aa0d3754d0269cd51cc2530fd07e252e6d12e2f`。源分布为 air/`1.3.4` 56、water/`1.1.3` 31、ground/`1.2.3` 13。三条 copied-runtime classification probe 全部通过，同一 Mass/kg support contract 以 canonical hash 复用 100 次；100 条 source observation、decision、support assignment 和 readiness 逐 stable key 闭合。十三条 ground→soil marker 不阻断 saved-plan 编译，但继续阻断 entity creation。

Kubernetes controller reconciliation 落为 immediate-prior current-state admission；Bazel CAS 落为十个有序注册输入和逐工件重哈希；Terraform plan/apply 分层落为只释放 saved-plan compiler；OPA decision explanation 落为逐行 rationale 与不暂停的风险标记。完整外部框架均未安装。23 个 Activity artifact 使事件 `4639→4669`；public replay 保持事件/工件总数不变，event SHA 为 `0f8f2272dba59ebb29b5da1a2ed1e5198fcb6cfaae80c1e555f93fa3bd48d041`，machine SHA 为 `bed86bbc1a2af66abc6d5454eb4a213fad38837be9be071a09dc84cf7f59df30`。Batch-3 native report 仍为原 SHA `dd133fdf052c600a0974e468690ecf6c6e09fe11acfa3a4c5ffcb189e11cd8bc`。

Network/remote search、saved-plan/native execution、Flow/Process entity creation/materialization、save-draft、model connection/mutation、external write、publication、comparison 与 LCIA 全为零。唯一下一路线是 `compile-uslci-flow-authoring-bulk-batch-4-saved-plans`：复用 generic saved-plan compiler，增加薄路由，只编译这 100 条 exact accepted intent，并保留 batches 2–3 的计划/门禁字节。

## Beat 78：不可变 Flow saved-plan batch 4

第七十八拍继续复用 `uslci_flow_authoring_bulk_batch_saved_plans/v1` 的 generic compiler，逻辑保持 `2026-07-18.2`。新增内容只有 Project/CLI batch-4 薄路由与参数化 continuation fixture；纯编译测试证明相同模块同时处理 batch 3 的 56/33/11 与 batch 4 的 56/31/13 分布、十一/十三条 marker，不会继承前一批计数。

Project 重哈希当前 support report 和六个 ledger、全量 authoring plan/intents、canary support report/contracts、copied runtime report/manifest 与 Project planning anchor。Compiler 仅以 `authoring_intent_key` join 100 条记录，独立计数分类/代码，并把 source/decision/support/runtime hashes、target UUID、三层分类与 Mass/UnitGroup 引用冻结到每份 plan。Terraform saved plan、Helm render/effect separation、OPA attributed decision carry-forward 与 OCI media/digest/size/path descriptor 都落为实际 plan/contract/test，完整框架未安装。

真实 input `7517eca0f37f26ab`、plan-set version `e6e51109f4563d37`、report SHA-256 `f13698dc7072b6c0c2678290e9baeac72a751f754fc5c78ea7fecc1ada4eeaf5`。100 个 exact plans/descriptors、100 条完整字段 assertion、13 条 entity-blocking marker carry-forward 均闭合；计划合计 1,263,778 bytes。201-node/200-edge batch-4 DAG 的 native nodes 全部 pending，failure ledger 为空。122 个 Activity artifact 使事件 `4669→4797`；public replay 保持事件/工件总数不变，event SHA `8d2010df6de4c284955ddbb66b16f3d2b067a3aa7fb4531a7f0ec7f6d04192fe`、machine SHA `1b68e2b9af53f8cdf64ac663ed97bc371c1415ed3a85ad0568945d5e85ec39c1`。

Batch-2 saved/native SHA 仍为 `67d2a573e17dd1bdca74314f4e4b454091b863548f01bf80dfff30a0384b412a` / `f8890751294b5212df1c0c082328f6ea5bfe6f440c73b2cf52ae979255f7112b`；batch-3 saved/native SHA 仍为 `1af8e9d0ee1293fa49921d60412e27c0b1cb153aa9e9e31077fc5c1d939358ea` / `dd133fdf052c600a0974e468690ecf6c6e09fe11acfa3a4c5ffcb189e11cd8bc`。所有 native/network/remote/entity/save/model/external/publication/comparison/LCIA counter 为零。唯一下一路线是 `execute-uslci-flow-authoring-bulk-batch-4-native-gates`。

## Beat 79：Flow native-gate batch 4 与中断恢复

第七十九拍没有复制 native executor，只增加 Project/CLI batch-4 薄路由，并把 generic Project callable range 与模块的 2–16 批合同对齐。100 个 exact plan 各自在隔离分支中执行五次 TianGong CLI 0.0.25 调用：plan validate、report-only materialize、field-scoped classification apply、FlowSchema validate、`qa flow`。四路有界并发峰值为四，100/100 branch terminal success、500/500 native invocation、零 branch failure。100 个 `elementary_flow_in_flow_qa` / `tidas.flow.type.required` finding 原样保留并由精确 typed predicate 判为 intentional Elementary authoring 下的 scoped not-applicable；十三条 ground→soil marker 继续阻断实体创建。

真实 input `edd020987ba49f98`、execution version `de586f7ee45f9541`、report SHA-256 `12f123aaf2be0c06c6453223a1d2d91afe74d4ae72b7eb7584ff439b6718b5cd`。Terminal DAG 为 201 nodes/200 edges；2,900-row raw manifest 与 tar member 的顺序、path、bytes、hash 逐项一致，archive 7,024,640 bytes、SHA-256 `b613875e69ef92d82da3bdbf004ea56c7313c8472d7e584c74202c01cd7889bf`。Batch-2/3 saved/native 与 batch-4 saved report 均保持原 hash。

首次真实 Project 调用在底层命令完成并写出全部输出后、控制器登记工件前中断。这被诊断为 `harness_failure`，而不是 prompt/context/science failure。Research Kernel 的 `run_activity` 现在识别“exact `activity.started` + 无终态 + 同 attempt 的 `activity-execution.json`”状态：只有 Activity/name/skill/effect、exact command、accepted exit code、typed stdout/stderr 与所有 expected output 都匹配时，才复用已产生的 exit-zero 证据并补齐登记；缺失或漂移则 fail closed，要求新 attempt。恢复没有重跑 500 次 native call。22 个登记工件使事件 `4797→4825`；replay 保持 `4825→4825`、工件 30,078、event SHA `2c822c247b17c29d5a745943d4cb1e88ed1f7ffc931486926eeff18c9aafe901`、machine SHA `3a4e2fc34aff74b553114bfd5336afef5e505bdd3982ef877e9622360b053ad8`。

Kubernetes controller reconciliation、Bazel action identity/CAS、Argo bounded terminal DAG/archive 与 OCI descriptor 继续以本地模块、contract 和测试落地，完整外部系统未安装。Network/remote、Flow/Process entity creation、save-draft、model connection/mutation、external write、publication、comparison 与 LCIA 全为零。唯一下一路线是 `resolve-uslci-flow-authoring-bulk-batch-5-supports`：复用 generic resolver，以 exact batch-4 native report 作为 immediate-prior authority，并只增加薄 Project/CLI 路由。

## Beat 80：Flow 支撑消解 batch 5

第八十拍复用 `uslci_flow_authoring_bulk_batch_supports/v1` 原模块与 `2026-07-18.2`/`.1` 逻辑/策略，只增加 batch-5 Project/CLI 薄路由。Continuation policy 测试现在覆盖 batches 3–5：batch `N` 只能由 exact `N-1` native report 释放，前序 index/status/success/failure/typed-QA/entity-release/next-route 任一漂移都会在 native probe 前 fail closed。

真实 key `ac6c2d3b4cfe9234`、input `a7f36f76838361f0`、resolution `00d656a9690db09c`、report SHA-256 `a14077c0a6ead3e44ff8c6e55b5298e9903deac2f060001e76044a3018dce700`。Descriptor 5 选择 100 个唯一 stable intents；源分布为 air/`1.3.4` 55、water/`1.1.3` 40、ground/`1.2.3` 5。三条 copied-runtime category probe 各执行一次，Mass/kg support contract 以 canonical hash 复用 100 次；source/classification/support/readiness 四个 ledger 全部一一闭合。五条 ground→soil marker 不阻断 saved-plan 编译，但继续阻断 entity creation。

Kubernetes observed-state reconciliation 落为 exact batch-4 terminal admission，Bazel CAS 落为十个有序注册输入与重哈希，Terraform plan/apply separation 落为只释放 batch-5 compiler，OPA attributed decision 落为逐行 rationale 与非暂停风险标记。完整外部框架均未安装。23 个登记工件使事件 `4825→4855`；public replay 保持 `4855→4855` 和工件 30,102，event SHA `3a255937f4cef0dfe3eff54153959d76da8ef2a9ec73359b13f5982f584ca9c7`、machine SHA `8410ad05a3ce7f5fab4eaa5ba39adfe8ef5a90c20be4c71b4422390fc3fce7ee`。Batches 2–4 saved/native reports 均保持原 hash。

三次 native probe 是 copied-runtime local read；network/remote、saved-plan/native-gate execution、Flow/Process entity creation/materialization、save-draft、model connection/mutation、external write、publication、comparison 与 LCIA 全为零。唯一下一路线是 `compile-uslci-flow-authoring-bulk-batch-5-saved-plans`：复用 generic immutable compiler，只增加薄 Project/CLI 路由。

## Beat 81：不可变 Flow saved-plan batch 5

第八十一拍继续复用 `uslci_flow_authoring_bulk_batch_saved_plans/v1` generic compiler 与 `2026-07-18.2` 逻辑，只增加 batch-5 Project/CLI 薄路由和参数化 continuation fixture。测试证明同一 compiler 从 batch 3 的 56/33/11、batch 4 的 56/31/13 延伸到 batch 5 的 55/40/5，不继承前批计数、风险数、文件名或 DAG identity。

Project 重哈希 batch-5 support report 与六个 ledger、全量 authoring plan/intents、canary support/contracts、copied-runtime report/manifest 和 planning anchor。Compiler 仅按 `authoring_intent_key` join；每份 plan 冻结 source/observation/decision/support/runtime hashes、target UUID、classification path 和 Mass/UnitGroup 引用。Terraform immutable plan、Helm compile/effect separation、OPA attributed decision carry-forward 与 OCI media/digest/size/path descriptor 均为实际模块/合同/测试；完整框架未安装。

真实 input `3d1d63636c97b37c`、plan set `2304a129907d5d3c`、report SHA-256 `ebbc9c1fb26fa9dd232a4052b2e1abaef522056222275acb3b84a3e1f5b85341`。100 个 unique plan/descriptors 与 100 条 complete-field assertions 闭合，计划共 1,260,575 bytes；五条 entity-blocking marker carry-forward 保留，failure ledger 为空，201-node/200-edge batch-5 DAG 的 native nodes 全 pending。122 个登记工件使事件 `4855→4983`；replay 保持 `4983→4983`、工件 30,225、event SHA `befd60c5b6a5a4f10a42cd9abeb2978bf9cc97ace1fed252f5d6c58020b53538`、machine SHA `f946b5f47ccd11c48972d03944886caed4f5904e58823c011b0d5702bbaea464`。

Batches 2–4 saved/native 与 batch-5 support report 保持原 hash。Native/network/remote/entity/save/Process/model/external/publication/comparison/LCIA counter 全为零。唯一下一路线是 `execute-uslci-flow-authoring-bulk-batch-5-native-gates`：复用 generic executor，只增加薄 Project/CLI route，并执行 exact batch-5 plan bytes。

## Beat 82：Flow native-gate batch 5

第八十二拍复用 `uslci_flow_authoring_bulk_batch_native_gates/v1` generic executor 与 `2026-07-18.2`/`.1` logic/policy，只增加 batch-5 Project/CLI 薄路由。Typed-QA 与 terminal-DAG tests 延伸到 batch 5；exact runtime/ruleset/rule list/target UUID/schema/classification/support/finding fingerprint 仍不放宽。

100 个 exact plan 在隔离分支内各真实调用 TianGong CLI 0.0.25 五次：plan validate、report-only materialize、classification apply、FlowSchema validate、`qa flow`。四路并发实际峰值为四；100/100 branch success，500/500 calls，零 retry/failure。100 个 `elementary_flow_in_flow_qa` / `tidas.flow.type.required` finding 原样保留，100 个 scoped typed decision 通过，五条 ground→soil marker 继续阻断 entity creation。

真实 input `b756e6ae4edf51be`、execution `1885fe787a50fd6f`、report SHA-256 `dbc9a81d514546bfc9751e7a2081ae0b9ba23d582638bab927752cf158f9dd9f`。Terminal DAG 为 201/200；2,900-row raw manifest 与 tar member 顺序、path、bytes、hash 逐项一致，archive 7,024,640 bytes、SHA-256 `fa80b9b55b51995c8926200dd012968bb9cbdd033fe1a5ab526b2b365ef9a136`。22 个登记工件使事件 `4983→5011`；replay 保持 `5011→5011`、工件 30,248、event SHA `24343ec9db191f3e628085144c6e77f0d1636049249087e76919fe980a4d935a`、machine SHA `90c97fa6bdd37fee8d680b7691ac824b11bb88d4c0fa5e103242de46e993bc6f`。

Kubernetes isolated terminal jobs、Bazel action/CAS、Argo bounded DAG/archive 和 OCI raw descriptors 继续落为本地模块/合同/测试，完整系统未安装。Batches 2–4 saved/native 与 batch-5 support/saved reports 保持原 hash。Network/remote/entity/save/Process/model/external/publication/comparison/LCIA 全为零。唯一下一路线是 `resolve-uslci-flow-authoring-bulk-batch-6-supports`。

## Beat 83：Flow 支撑消解 batch 6

第八十三拍保持 generic resolver `2026-07-18.2`/`.1` 不变，只增加 batch-6 Project/CLI route。Immediate-prior 测试覆盖 batches 3–6，route authority 任一漂移均 fail closed。真实 key `acbac5f907286db2`、input `eb697a0cd96acf4a`、resolution `5527fb6dba787f08`、report SHA `d8018e6cf0d153f19dc55f5a8e3ca2ec04be63611224ff33414fd6f8d0f28cd0`。100 stable intents 包含 56 air/`1.3.4`、31 water/`1.1.3`、13 ground/`1.2.3`；三次 local probe、100 次 Mass reuse、四 ledger 一一闭合，十三 marker 保留。

Kubernetes reconciliation、Bazel CAS、Terraform plan/apply 与 OPA attributed risk 继续落为本地合同/工件/测试，完整框架未安装。23 个登记工件使事件 `5011→5041`；replay 保持 5,041 events、30,272 artifact files、event SHA `adbd5663fe3acf179932945a0ff438a60987ab4ccfba5c7fd4e3bb405581f7cc`、machine SHA `92d5459c77f051f9988d5553c55f9be5cc241c18a39874a46c9fffae357b793a`。所有 remote/entity/save/model/comparison/LCIA effect 为零；唯一下一路线是 `compile-uslci-flow-authoring-bulk-batch-6-saved-plans`。

## Beat 84：不可变 Flow saved-plan batch 6

第八十四拍复用 generic compiler `2026-07-18.2`，仅增加 batch-6 薄路由和 56/31/13 参数化 fixture。真实 input `d8230b8299a8237b`、plan set `c3a553976bec3407`、report SHA `e9d60b30d48d78c8707daf9352939fc1e7d29be75eff70ebf15b1e1988469b4d`。100 plan/descriptors/assertions 共 1,264,063 bytes，十三 marker 保留，failure 空，201/200 DAG pending。Terraform/Helm/OPA/OCI 机制落为 exact bytes、effect separation、attributed carry-forward 与 content descriptors；完整框架未安装。

122 个登记工件使事件 `5041→5169`；replay 保持 5,169 events、30,395 files、event SHA `70ac1439381ad7c0ecd0b26fcc06fcaadb357f9535350c85a8d123bc910fed5c`、machine SHA `ac09f1fcbdecf271c37b53f5d1014677e803d019dd11ea70769eac698fd03217`。Native/entity/save/remote/model/comparison/LCIA 全为零；唯一下一路线是 `execute-uslci-flow-authoring-bulk-batch-6-native-gates`。

## Beat 85：Flow native-gate batch 6

第八十五拍仅增加 batch-6 薄路由；generic executor/typed policy 不变。真实 input `4e6cb5578d37f580`、execution `229398418a7526ac`、report SHA `1c93862235568023878df717c70cee99ee9d9c538cf7dde90de1ef41f1508f02`。100 branches/500 calls 全成功，100 findings/typed decisions 与十三 markers 保留，201/200 DAG completed。2,900-member、7,024,640-byte archive SHA 为 `131d11cb80df6c7dc9762dbc6d29dbb052612344231882e01775147296836a6f`，逐 member 校验通过。

事件 `5169→5197`；replay 保持 5,197 events、30,418 files、event SHA `6654fc88a1d39cbaf4722e78de300456dadaf382398a15572aec56c23d5eb1e1`、machine SHA `e87354b46947cb62c8f758462f4cfe6edeac776239e94aaac0e3709509459f30`。Entity/save/remote/model/comparison/LCIA 为零；唯一下一路线是 batch-7 supports。

## Beat 86：Flow 支撑消解 batch 7

第八十六拍保持 generic resolver `2026-07-18.2`/`.1` 不变，仅增加 batch-7 Project/CLI 薄路由并把 immediate-prior tests 延伸到 descriptor 7。真实 key `71764b206204672a`、input `31ed3727bf48674e`、resolution `d26a9427865a116b`、report SHA `bc9dd3e9886bc8573d30d91f69e10effc23e9a8639741b473a5f61a7c27004ad`。100 stable intents 包含 55 air/`1.3.4`、35 water/`1.1.3`、10 ground/`1.2.3`；三次 local probe、100 次 Mass reuse、四份 ledger 一一闭合，十条 marker 保留。

Kubernetes reconciliation、Bazel CAS、Terraform plan/apply 与 OPA attributed risk 继续落为本地合同、工件和测试；完整框架未安装。23 个登记工件使事件 `5197→5227`；replay 保持 5,227 events、30,464 artifact files、event SHA `9bc5b5fcc3972b60630fd25d86e7274bf01f0acd974e08389f1646873c6f318c`、machine SHA `0cb7cf0b4448f903af383f956d3b2a029a390b3b8dcaa7076d6baffaa9726ec1`。Remote/entity/save/model/comparison/LCIA effects 为零；唯一下一路线是 `compile-uslci-flow-authoring-bulk-batch-7-saved-plans`。

## Beat 87：不可变 Flow saved-plan batch 7

第八十七拍复用 generic compiler `2026-07-18.2`，仅增加 batch-7 薄路由和 55/35/10 参数化 fixture。真实 input `3f6acb8b9cdacd8c`、plan set `1c6aa63ca5d4ec72`、report SHA `73d55a7bca5f06f2643361f83408e97bacfdab7a6abf509aa1bba392276c93ea`。100 plan/descriptors/assertions 共 1,262,937 bytes，十条 marker 保留，failure 空，201/200 DAG 含 100 completed 与 101 pending nodes。Terraform/Helm/OPA/OCI 机制继续落为 exact bytes、effect separation、attributed carry-forward 与 content descriptors；完整框架未安装。

122 个登记工件使事件 `5227→5355`；replay 保持 5,355 events、30,587 files、event SHA `6da022c14abb146eaa413fdbb5a84f36f05bbfb567284e9fe18ce23f8bda0278`、machine SHA `6bf9b5eb422a8404bee45ddd0f94fe005c7fffcdb97cb059e225565d8fa7c8bd`。Native/entity/save/remote/model/comparison/LCIA 全为零；唯一下一路线是 `execute-uslci-flow-authoring-bulk-batch-7-native-gates`。

## Beat 88：Flow native-gate batch 7

第八十八拍仅增加 batch-7 薄路由；generic executor 与 typed policy 不变。真实 input `ec016e6d1b341fbb`、execution `e0fb1b092858df31`、report SHA `fc0982a8d3e951029673621f4c7fd8928497b581a58cf6f7bb536cff63b70a83`。100 branches/500 calls 全成功，100 findings/typed decisions 与十条 markers 保留，201/200 DAG completed。2,900-member、7,024,640-byte archive SHA 为 `3c0961ebba24d85c0bd0679cc2b302699ff276889626dcb31c14cf798786c80f`，逐 member path/order/bytes/hash 校验通过。

Kubernetes isolated branches、Bazel action/CAS、Argo bounded DAG/archive 与 OCI raw descriptors 继续落为本地模块、合同、工件和测试；完整框架未安装。事件 `5355→5383`；replay 保持 5,383 events、30,610 files、event SHA `b00958ba3216348b2e0f7ebba400e3655ec36e37d0fb0d8bc9cf8b2e1f83d6e4`、machine SHA `115c14d91fde6cf3b19da42ebedff516ec19d63cca2ec9b2d976e8774dbdd2a2`。Entity/save/remote/model/comparison/LCIA 为零；唯一下一路线是 batch-8 supports。

## Beat 89：Flow 支撑消解 batch 8

第八十九拍保持 generic resolver `2026-07-18.2`/`.1` 不变，仅增加 batch-8 Project/CLI 薄路由。真实 key `9c13c88f07348346`、input `57ca46b02dc469bc`、resolution `4253fa6ff013eca4`、report SHA `01a7e50a9393def789cec56a8db49220440439515aefe72d07e7281cf80ebb91`。100 stable intents 包含 52 air/`1.3.4`、42 water/`1.1.3`、6 ground/`1.2.3`；三次 local probe、100 次 Mass reuse、四份 ledger 一一闭合，六条 marker 保留。

Kubernetes reconciliation、Bazel CAS、Terraform plan/apply 与 OPA attributed risk 继续落为本地合同、工件和测试；完整框架未安装。23 个登记工件使事件 `5383→5413`；replay 保持 5,413 events、30,634 artifact files、event SHA `81877f57a129dc8946b81818888d71c84e6cc3b128a15408b308075bb4f3d177`、machine SHA `ffd8f5a8f01b162dbdccfb8c43790b699932cf0a1ff30f68d7bc29287815b158`。Remote/entity/save/model/comparison/LCIA effects 为零；唯一下一路线是 batch-8 saved plans。

## Beat 90：不可变 Flow saved-plan batch 8

第九十拍复用 generic compiler `2026-07-18.2`，仅增加 batch-8 薄路由和 52/42/6 参数化 fixture。真实 input `f14aaddb7c2aec1f`、plan set `6a40f77d85fc1012`、report SHA `43b28b85919300a6fdad928555c687d024cc6c2d56e30b12ad2384a75d8b0b83`。100 plan/descriptors/assertions 共 1,260,916 bytes，六条 marker 保留，failure 空，201/200 DAG 含 100 completed 与 101 pending nodes。Terraform/Helm/OPA/OCI 机制继续落为 exact bytes、effect separation、attributed carry-forward 与 content descriptors；完整框架未安装。

122 个登记工件使事件 `5413→5541`；replay 保持 5,541 events、30,757 files、event SHA `a0712b509ec9818690e3e68dbf764854951def21afd828ffaab38553f2698b1c`、machine SHA `2f1aa7a50a43e5f7b110777b67f25951f360ed9eec30d160bc17cbbdd7efc92e`。Native/entity/save/remote/model/comparison/LCIA 全为零；唯一下一路线是 batch-8 native gates。

## Beat 91：Flow native-gate batch 8

第九十一拍增加 batch-8 薄路由，并把 independent plan review 暴露的执行边界固化到 generic logic `2026-07-18.3`：批次准入必须恰好 100 plans 与六条 ground-derived markers；五类 native subprocess command 进入 allowlist，validate/materialize 强制 report-only；子进程移除凭据类环境变量并声明 offline；Project 再独立打开 tar，核对全部 2,900 members 的 order/path/size/bytes/SHA/digest。`.2` 首次本地候选在验收前被 supersede，作为 append-only evidence 保留。

硬化后的真实 input `9006a25d7d5f86de`、execution `b208fec5b25c212a`、report SHA `cffc9886e9b1b0911b03f5cf5633757a8e300301680b0cf6a6f4f2371463b6b7`。100 branches/500 calls 全成功，100 findings/typed decisions 与六条 markers 保留，201/200 DAG completed。2,900-member、7,024,640-byte archive SHA 为 `0ab1bb4f433ea9bba3ecad397149f1998e9f68da878440477679713cecf625f2`，逐 member path/order/bytes/hash 校验通过。

Kubernetes isolation、Bazel action/CAS、Argo bounded DAG/archive 与 OCI descriptors 继续落为本地模块、合同、工件和测试；完整框架未安装。两次 Activity 使事件 `5541→5597`；replay 保持 5,597 events、30,803 files、event SHA `6b08c17df787112731ca1ae237598fbc391d2a7c4f1c70fbd24f3a8a64cc66b5`、machine SHA `15bcf89d45fa30e05f363942184441aa658cead01309ff87f6884aa615075084`。Entity/save/remote/model/comparison/LCIA 为零；唯一下一路线是 batch-9 supports。

## Beat 92：Flow 支撑消解 batch 9

第九十二拍先增加 batch-9 薄路由；independent review 随后证明两个共享边界需要硬化，generic resolver 因此升级到 `2026-07-18.3`。Batch 9 准入不再只信任上一批的计数摘要，而是强制核对 batch-8 `.3` contract/policy、100 terminals、500 calls、201/200 DAG、2,900 archive members、六条 markers、四类关键 content descriptors、零 failures/effects 和唯一 route。三个 copied-runtime classification-path probes 进入精确 allowlist，子进程移除凭据变量并声明 offline/no-proxy；machine projection 新增 `source_route_authority_version=b208fec5b25c212a`。

硬化后的真实 key `aafe6ff62efd4f89`、input `fff3456486c11c38`、resolution `5160dd3a971e0af0`、report SHA `9e3dd2a027012cb06994b6a62abd218d540c8cea93ba251497b6ac70f4fa8c18`。100 stable joins 包含 38 air/`1.3.4`、54 water/`1.1.3`、8 ground/`1.2.3`；三次本地 probe、100 次 Mass reuse、四份 ledger 一一闭合，八条 marker 保留。23 个当前 Activity 工件逐 path/bytes/hash 独立复核通过，failure 与所有禁止 effects 为零。

`.2` 首次本地候选在验收前被 supersede，作为 append-only evidence 保留。两次 Activity 使事件 `5597→5657`；replay 保持 5,657 events、30,833 files、event SHA `6b507652c53b3fdd4c65da82a2325e8b18b5ea1c476038cfe1e2d5a12051afaf`、machine SHA `67f3f72096de035b40e2447c3972e017710e1c9b096901b78bc8520c763f96dc`。Kubernetes/Bazel/OPA/Terraform 机制继续落实为本地隔离、CAS、attributed decisions 与 plan/apply 分离；完整框架未安装。唯一下一路线是 batch-9 saved plans。

## Beat 93：不可变 Flow saved-plan batch 9

第九十三拍在 generic compiler `2026-07-18.3` 中固化 batch-9 的严格准入：只接受恰好 100 条 hardened support rows，并同时绑定 support logic、batch-8 route authority version/report；99/101 行、archive/DAG/effect 或任一 provenance 漂移均 fail closed。Project 不复述 compiler 结论，而是独立重读 100 份计划、assertions、descriptors、markers、generation manifests 与 DAG，核对 stable order/key、path/bytes/hash/digest/size 和 exact topology。

真实 input `07c12c14dc1dca0c`、plan set `3c3a9424a7cb7a22`、report SHA `0900fb8e5048803b02915906e967fb4a56ec03f15e4d0d3b19b7ba03c0a0c76d`。100 plan/descriptors/assertions 覆盖 38 air、54 water、8 ground，共 1,262,016 bytes（单份 12,557–13,062 bytes）；八条 marker 保留，failure 空，201/200 DAG 含 100 completed plan nodes、100 pending native nodes 和一个 pending release-analysis node。122 个登记工件逐 path/bytes/hash 复核通过。

Terraform immutable plan/apply separation、Helm compile/effect separation、OPA attributed risk carry-forward 与 OCI media/digest/size/path descriptors 已迁移为本地模块、合同和测试；完整外部框架未安装。冻结计划只是 exact native execution inputs，不是 Flow entity。事件 `5657→5785`；public replay 保持 5,785 events、30,956 files、event SHA `7a53393038cdda1ac32335c6d82e6df84f1de85bf906cdce52c989528cb04963`、machine SHA `998edb5732c9bbc7934089e6ee4765c1492e9dbd0ed6469472ababab1bc5a824`。Native/entity/save/remote/model/comparison/LCIA 全为零；唯一下一路线是 `execute-uslci-flow-authoring-bulk-batch-9-native-gates`。

## Beat 94：Flow native-gate batch 9

第九十四拍把 generic executor 升级到 `2026-07-18.4`，并让 Project preflight 与 direct executor 共用 exact saved-plan authority validator。任何 batch-9 `.3` input/plan-set/report、100/8 cardinality、support logic/resolution/report 或 batch-8 route authority version/report 漂移都会在首个原生分支前 fail closed；batch 2–8 的既有 authority/replay 语义保持不变。

真实 input `4e90d3fa09d69779`、execution `56870a987f96fb17`、report SHA `655a58b06c565f423b16919f8db06673db13e4c8fc8a2647e46b996524dddb84`。100 branches/500 次 allowlisted、credential-free offline/report-only TianGong calls 全成功，峰值并发为四；100 findings/typed decisions 与八条 markers 保留，201/200 DAG completed。2,900-member、7,024,640-byte archive SHA 为 `679e7c3b8ae9bcdcecc604ccddabf7dd4bfc699f59601307953bb8ea702e95d5`，Project 与独立审计均逐 member 核对 order/path/size/bytes/SHA/digest。

Kubernetes isolation、Bazel action/CAS、Argo bounded DAG/archive 与 OCI descriptors 继续落为本地模块、合同、工件和测试；完整框架未安装。22 个登记工件使事件 `5785→5813`；replay 保持 5,813 events、30,979 files、event SHA `486a506dc2a1472d332851143a398588402adec334669fbe1e0a3c16e9244062`、machine SHA `1d59c78461ecfc4f9b1173d0c83b73eddf9d980226942065a976570752c55083`。Entity/save/remote/model/comparison/LCIA 为零；唯一下一路线是 batch-10 supports。

## Beat 95：Flow 支撑消解 batch 10

第九十五拍首先留下了一个有价值的 fail-closed 诊断：generic `.4` 仍固定绑定早期 Process `43c906b5-...` 的响应；它碰巧覆盖 batches 2–9，却缺少 batch-10 Flow `a320e284-d276-3167-89b3-19d790081c08`。这不是科学推断失败，而是 harness 把单个不可变 observation 错当成全局 corpus。Generic resolver 因此升级到 `2026-07-18.5`：通过与 compiler 相同的 batch admission 推导 100 个 source Flow IDs，只遍历 Project 已登记的 `raw-public-responses`，确定性选择完整覆盖的成功响应，再按 registry 复核 path/2,171,478 bytes/SHA `43984a451d9a2fdd3f605ac4705ae40e46626c3882a23f712355d090765e46c4`。没有未登记扫描、语义补值或宽松 fallback。

真实 key `183bcb1cf880f679`、input `b28c5eeb9a718ae6`、resolution `1284b1d20a584a48`、report SHA `dbc989e195106db7c9f1584075a7bbf45a8451b7a9ade3bbc85090b2a11cb551`。100 stable joins 覆盖 57 air/`1.3.4`、29 water/`1.1.3`、14 ground/`1.2.3`；三个 credential-free offline native probes、100 次 Mass contract assignment、四份核心 ledger 与十四条 marker 一一闭合。23 个当前 Activity 工件及十个输入逐 path/bytes/hash 独立复核，failures 与所有禁止 effects 为零。

失败 `.4` Activity 作为 append-only diagnostic evidence 保留；失败与成功两次尝试使事件 `5813→5846`。Replay 保持 5,846 events、31,017 files、event SHA `112f903e6b22b8b80563bca310d27c3398338003f18bb21be85cea2572d72968`、machine SHA `ec7b58d56a9c3493e234ac330953fc6ac1fcc37d7acb15aca428b190a7afc0c1`。Content-addressed query planning、Bazel CAS、OPA attributed decisions 与 Terraform plan/apply separation 均落实到本地模块、合同、工件和测试；完整外部框架未安装。唯一下一路线是 batch-10 saved plans。

## Beat 96：不可变 Flow saved-plan batch 10

第九十六拍把 generic compiler 升级到 `2026-07-18.4`。Shared admission 对 batch 10 强制核对 support `.5`、key/input/resolution、57/29/14 分类计数、batch-9 native execution/report，以及完整覆盖源 envelope 的 path suffix/SHA/bytes；Project 进一步把 accepted support event 和 report SHA 锁进 planning anchor。任何 route、source artifact、marker 或 effect 漂移都在计划生成前 fail closed，batch-9 既有准入保持回归通过。

真实 input `01f9068619c0ee41`、plan set `b238bd643791013c`、report SHA `265009f491b1d951fc02ac32c20b9185f6a5b0ef6dcae2a2b47ffaaf7d3325dd`。100 plan/descriptors/assertions 覆盖 57 air、29 water、14 ground，总计 1,263,017 bytes（单份 12,544–13,056 bytes）；此前暴露缺口的 Flow `a320e284-d276-3167-89b3-19d790081c08` 也拥有精确 plan。十四条 marker 保留，failure 空，201/200 DAG 含 100 completed plan nodes、100 pending native nodes 与一个 pending release-analysis node。122 个登记工件逐 path/bytes/hash 复核通过。

Terraform immutable plan/apply separation、OCI descriptors、Bazel CAS 与 OPA risk carry-forward 继续落为本地模块、合同和测试；完整外部框架未安装。事件 `5846→5974`；replay 保持 5,974 events、31,140 files、event SHA `033d6dfd83e33a25f53ed81f066a860e22cf22960214603374b0bc2f72d5b695`、machine SHA `8ac01e62729cc3caa40e2baae43cc81413a256cbc4b6c83b64d9e6ebaccc95a7`。Native/entity/save/remote/model/comparison/LCIA 全为零；唯一下一路线是 batch-10 native gates。

## Beat 97：Flow native-gate batch 10

第九十七拍把 batch-10 的 `.4` frozen plan authority、`.5` support lineage 和 batch-9 route authority 固化进 generic executor `2026-07-18.5` 的共享准入。任何 input/plan-set/report、57/29/14 分类、100/14 cardinality、source envelope 或上游 route 漂移，都会在启动首个原生分支前 fail closed。Batch 2–9 的既有准入和回放语义保持回归通过。

真实 input `57105881f484c9cc`、execution `a8deb2b37461e3f7`、report SHA `1dcbb3c5e2fa9fb865c6e10b07461f2796ea9aed7aef4e220a907a667a161913`。100 branches/500 次 allowlisted、credential-free offline/report-only TianGong calls 全成功，峰值并发为四；100 findings/typed decisions 与十四条 markers 完整保留，201/200 DAG completed。Project 和独立审计逐项核对 22 个登记工件，并重新打开 ordered 2,900-member、7,024,640-byte archive，比较全部 descriptor/member 的 order/path/size/bytes/SHA/digest；archive SHA 为 `0be9978f7a38ecbdf0c8ca20b5983866204db4c78906071cd089d881ac72b06a`。

Kubernetes isolation、Bazel action/CAS、Argo bounded DAG/archive 与 OCI descriptors 继续迁移为本地模块、合同、工件和测试；完整框架未安装。事件 `5974→6002`；幂等 replay 保持 6,002 events、31,163 files、event SHA `e664a51f3115ad5e5bf4426f279b0f5f3731467b48d78a1284438c85c81f755a`、machine SHA `efdeda6f1fbbdc86746b4eaa5f1fff81ec1fc612dd3e7921cbb8ea63c99cc65d`。Entity/save/remote/model/comparison/LCIA 为零；唯一下一路线是 batch-11 supports。

## Beat 98：Flow 支撑消解 batch 11

第九十八拍没有复制 resolver，而是在共享 immediate-prior admission 表中加入 batch-10 的精确 authority：logic `2026-07-18.5`、input `57105881f484c9cc`、plan set `b238bd643791013c`、execution `a8deb2b37461e3f7`、report/archive hashes、100/500/201–200/2,900 cardinality、十四条 markers 与零 effects。任一字段或 content descriptor 漂移都会在首次分类 probe 前 fail closed；batch 9/10 既有准入回归通过。

真实 key `48d7fdcb315d1dda`、input `e443be3b0ee233e2`、resolution `4d16ff032fda285b`、report SHA `5595dab36555382a6e9bed2b3a8cf9e077a4955834c4c545c0290fa88a69eb5f`。100 stable joins 覆盖 53 air/`1.3.4`、37 water/`1.1.3`、10 ground/`1.2.3`；三个 credential-free offline TianGong probes、100 次 Mass assignment、四份核心 ledger 与十条 marker 一一闭合。完整覆盖源 envelope 继续只从 Project registry 选择并复核 2,171,478 bytes/SHA；23 个当前 Activity 工件全部通过独立 path/bytes/hash 审计，failures 与禁止 effects 为零。

Kubernetes reconciliation、Bazel action/CAS、OPA attributed decisions 与 Terraform plan/apply separation 均落实为本地 admission、binding、ledger、artifact 和 test；完整框架未安装。事件 `6002→6032`；replay 保持 6,032 events、31,187 files、event SHA `3f59702655bbf727da8ca41589e8c9fb2ea935fc1ba37f2f1712cb581f38438e`、machine SHA `f397058fe0f61d380d1c65ad52e8daf73af6124731ba857e53f3faabfc6f6743`。唯一下一路线是 batch-11 saved plans。

## Beat 99：不可变 Flow saved-plan batch 11

第九十九拍在 generic compiler `2026-07-18.4` 中加入 batch-11 的精确准入：support `.5`、key/input/resolution/report、53/37/10 分类、batch-10 native execution/report 和完整覆盖 source envelope 必须同时一致。Project 还把真实 support event `6031`、resolution 与 report SHA 绑定为 planning anchor；任一 provenance、cardinality、marker 或 effect 漂移都在生成计划前 fail closed。

真实 input `49b055931e404a01`、plan set `340858c063024f8b`、report SHA `b0cf6ba4752baf26a4bbbbf130ffeee570b963bd91a47451cba39240cbb1d20c`。100 plan/descriptors/assertions 覆盖 53 air、37 water、10 ground，总计 1,261,491 bytes（单份 12,544–13,027 bytes）；十条 marker 保留，failure 空，201/200 DAG 含 100 completed plan nodes、100 pending native nodes 与一个 pending release-analysis node。122 个登记工件及所有计划 content descriptors 逐 path/bytes/hash/digest 复核，plan/index/assertion/execution identity 一一闭合。

Terraform immutable plan/apply separation、OCI descriptors、Bazel CAS 与 OPA risk carry-forward 继续落为本地模块、合同、工件和测试；完整外部框架未安装。事件 `6032→6160`；replay 保持 6,160 events、31,310 files、event SHA `f4abefd575eef865b754f6f3303b8fe76282aa33b4e85372476059577119c29d`、machine SHA `2c7d4bc6a300170bf060b92b6b88efb77e8d641483d07774c08c2ce2ba3160bd`。Native/entity/save/remote/model/comparison/LCIA 全为零；唯一下一路线是 batch-11 native gates。

## Beat 100：Flow native-gate batch 11

第一百拍在 generic executor `2026-07-18.5` 中加入 batch-11 精确 authority：saved-plan `.4` key/input/set/report、support `.5` resolution/report、batch-10 route execution/report、53/37/10 分类与 100/10 cardinality 必须一致，任一漂移都会在首个分支前 fail closed。Batch 9/10 既有准入、typed-QA 和 terminal-DAG 回归保持通过。

真实 input `787421515ed8d58a`、execution `f93d909c6218b6e1`、report SHA `4be9110ffaa74fbbdab8207598a9744d4ce6c3dace3ef1ef11b2704f34ae41cd`。100 branches/500 次 allowlisted、credential-free offline/report-only TianGong calls 全成功，峰值并发为四；100 findings/typed decisions 与十条 markers 完整保留，201/200 DAG completed。Project 和独立审计重新打开 ordered 2,900-member、7,024,640-byte archive，逐项比较 descriptor/member 的 order/path/size/bytes/SHA/digest；archive SHA 为 `59b19585422ac2a726a8d3aec5bccd0d07e059042db0fcef8c0c28dcaca19fed`。

Kubernetes isolation、Bazel action/CAS、Argo bounded DAG/archive 与 OCI descriptors 继续落为本地模块、合同、工件和测试；完整框架未安装。22 个登记工件使事件 `6160→6188`；幂等 replay 保持 6,188 events、31,333 files、event SHA `62859266a906605a9e474ae9de5b28205956ed1857ddcb344d92bb6cc813981e`、machine SHA `c4f9d9c127544c0f4842d4827ae74af5171c34c95cbdd86cef44a995e6653a3d`。Entity/save/remote/model/comparison/LCIA 为零；唯一下一路线是 batch-12 supports。

## Beat 101：Flow 支撑消解 batch 12

第一百零一拍没有复制 resolver，而是在共享 immediate-prior admission 表中加入 batch-11 的精确 authority：logic `2026-07-18.5`、input `787421515ed8d58a`、plan set `340858c063024f8b`、execution `f93d909c6218b6e1`、report/archive hashes、100/500/201–200/2,900 cardinality、十条 markers 与零 effects。任一字段或 content descriptor 漂移都会在首次分类 probe 前 fail closed；batch 9–11 既有准入回归保持通过。

真实 key `c168a5b69724b74e`、input `e285b204cac5c5fd`、resolution `388eb4b3fd78ea7d`、report SHA `20bb485d3ad0bbef9ef3673bc8737957ffdc9e5ba6c38d92f64b3e0e02525b99`。100 stable joins 覆盖 54 air/`1.3.4`、34 water/`1.1.3`、12 ground/`1.2.3`；三个 credential-free offline TianGong probes、100 次 Mass assignment、四份核心 ledger 与十二条 marker 一一闭合。完整覆盖源从 Project registry 动态选择并复核为 `raw-public-responses/026-45ef7d06-b57a-36cd-a6ce-37d5ded64f60.json`，1,428,215 bytes，SHA `268c0977394b659f7abc609807b1ae357c4c32fbf6627326f2ecaa9e00cc13ed`。23 个当前 Activity 工件全部通过独立 path/bytes/hash 审计，failures 与禁止 effects 为零。

Kubernetes reconciliation、Bazel action/CAS、OPA attributed decisions 与 Terraform plan/apply separation 均落实为本地 admission、binding、ledger、artifact 和 test；完整框架未安装。事件 `6188→6218`；replay 保持 6,218 events、31,357 files、event SHA `e4eca3d863e2cad3c1b6c9d264d953aa5fdecb0a36ad9d0ecd9a98e53ed561bb`、machine SHA `b14dd9d420028ebe7d8a2ddbf17d76ae679fb3b74d4e71dcd0b8e0b8997ca719`。唯一下一路线是 batch-12 saved plans。

## Beat 102：不可变 Flow saved-plan batch 12

第一百零二拍在 generic compiler `2026-07-18.4` 中加入 batch-12 的精确准入：support `.5`、key/input/resolution/report、54/34/12 分类、batch-11 native execution/report，以及这次不同的 1,428,215-byte 完整覆盖 source envelope 必须同时一致。Project 还把真实 support event `6217`、resolution 与 report SHA 绑定为 planning anchor；任一 provenance、cardinality、marker、source path/hash/bytes 或 effect 漂移都在生成计划前 fail closed。

真实 input `a43b51abf309f13d`、plan set `e21a6c5d8654c16a`、report SHA `daa39b6e86735efdfdf113c246140446a83fc1d8a6d805e27f918d9f25e4ed73`。100 plan/descriptors/assertions 覆盖 54 air、34 water、12 ground，总计 1,263,141 bytes（单份 12,552–13,030 bytes）；十二条 marker 保留，failure 空，201/200 DAG 含 100 completed plan nodes、100 pending native nodes 与一个 pending release-analysis node。122 个登记工件及所有计划 content descriptors 逐 path/bytes/hash/digest 复核，plan/index/assertion/execution identity 一一闭合。

Terraform immutable plan/apply separation、OCI descriptors、Bazel CAS 与 OPA risk carry-forward 继续落为本地模块、合同、工件和测试；完整外部框架未安装。事件 `6218→6346`；replay 保持 6,346 events、31,480 files、event SHA `093aae5a1d32e768f726aa3abf249c3c6d4df8472a751af674697ddd7df70b8e`、machine SHA `00293cc63da25d470ba344d0858f82969b0fff1a35ffedabe0dcd65cd621f2a6`。Native/entity/save/remote/model/comparison/LCIA 全为零；唯一下一路线是 batch-12 native gates。

## Beat 103：Flow native-gate batch 12

第一百零三拍在 generic executor `2026-07-18.5` 中加入 batch-12 精确 authority：saved-plan `.4` key/input/set/report、support `.5` resolution/report、batch-11 route execution/report、所选 source envelope、54/34/12 分类与 100/12 cardinality 必须一致，任一漂移都会在首个分支前 fail closed。Batch 9–11 既有准入、typed-QA 和 terminal-DAG 回归保持通过。

真实 input `bd3605f17db828f7`、execution `1170c3f1be7ed8ce`、report SHA `0d7c3419d2f7310c88020be9257e84d80abcaa429a779926fe066db00d1bd1db`。100 branches/500 次 allowlisted、credential-free offline/report-only TianGong calls 全成功，峰值并发为四；100 findings/typed decisions 与十二条 markers 完整保留，201/200 DAG completed。Project 和独立审计重新打开 ordered 2,900-member、7,024,640-byte archive，逐项比较 descriptor/member 的 order/path/size/bytes/SHA/digest；archive SHA 为 `1e22485c2a1fcb99b31ea543c9e796a02c013104588de5211f496a61347c7a67`。

Kubernetes isolation、Bazel action/CAS、Argo bounded DAG/archive 与 OCI descriptors 继续落为本地模块、合同、工件和测试；完整框架未安装。22 个登记工件使事件 `6346→6374`；幂等 replay 保持 6,374 events、31,503 files、event SHA `fc93407f7c1d5e2c7a2077308470d4fc0660f02b928c6630d2705dfb1235c002`、machine SHA `d8ef40ac10520f903b91503553bc22f9c3f31a7d00ac4e9363f19c8ca1bf8028`。Entity/save/remote/model/comparison/LCIA 为零；唯一下一路线是 batch-13 supports。

## Beat 104：Flow 支撑消解 batch 13

第一百零四拍在共享 immediate-prior admission 表中加入 batch-12 的精确 authority：logic `2026-07-18.5`、input `bd3605f17db828f7`、plan set `e21a6c5d8654c16a`、execution `1170c3f1be7ed8ce`、report/archive hashes、100/500/201–200/2,900 cardinality、十二条 markers 与零 effects。任一字段或 content descriptor 漂移都会在首次分类 probe 前 fail closed；旧批次准入回归保持通过。

真实 key `4d83c19550a6a282`、input `8e49c91222bfec6a`、resolution `66cfe44ebf186a31`、report SHA `5cf58e1e0c68ebb4348262e31c9ef8832b0d872dbeacd05c8c629c0e930ae690`。100 stable joins 覆盖 59 air/`1.3.4`、28 water/`1.1.3`、13 ground/`1.2.3`；三个 credential-free offline TianGong probes、100 次 Mass assignment、四份核心 ledger 与十三条 marker 一一闭合。完整覆盖源继续从 Project registry 动态选择并复核为 1,428,215 bytes、SHA `268c0977394b659f7abc609807b1ae357c4c32fbf6627326f2ecaa9e00cc13ed`。23 个当前 Activity 工件全部通过独立 path/bytes/hash 审计，failures 与禁止 effects 为零。

Kubernetes reconciliation、Bazel action/CAS、OPA attributed decisions 与 Terraform plan/apply separation 均落实为本地 admission、binding、ledger、artifact 和 test；完整框架未安装。事件 `6374→6404`；replay 保持 6,404 events、31,527 files、event SHA `d49d1dbb879433fe729ea3b5ac180d9b250362e6077d4218e3e413666de59951`、machine SHA `d5bd4896814f397ca94db77752a977c4f1c9be847f4bf24d87beec23e0eefb6c`。唯一下一路线是 batch-13 saved plans。

## Beat 105：不可变 Flow saved-plan batch 13

第一百零五拍在 generic compiler `2026-07-18.4` 中加入 batch-13 的精确准入：support `.5`、key/input/resolution/report、59/28/13 分类、batch-12 native execution/report 与所选 source envelope 必须同时一致。Project 把真实 support event `6403`、resolution 与 report SHA 绑定为 planning anchor；任一 provenance、cardinality、marker 或 effect 漂移都在生成计划前 fail closed。

真实 input `13ad58cf0b5df066`、plan set `9f872eb1c10cbad6`、report SHA `cfd5d211e3c92151dcfb49ca71d7fd300e4db172cfc4844aa18dfa727f3aa996`。100 plan/descriptors/assertions 覆盖 59 air、28 water、13 ground，总计 1,263,590 bytes（单份 12,554–13,021 bytes）；十三条 marker 保留，failure 空，201/200 DAG 含 100 completed plan nodes、100 pending native nodes 与一个 pending release-analysis node。122 个登记工件及所有计划 content descriptors 逐 path/bytes/hash/digest 复核。

Terraform immutable plan/apply separation、OCI descriptors、Bazel CAS 与 OPA risk carry-forward 继续落为本地模块、合同、工件和测试；完整外部框架未安装。事件 `6404→6532`；replay 保持 6,532 events、31,650 files、event SHA `f38a02b8b5269951e710c744157ec0071aff782a269bead0fc13ee5fc6163ee7`、machine SHA `603be05ac95fcc54494679de7698d9100b8fbda19155ae68c82e979d6074fdc8`。Native/entity/save/remote/model/comparison/LCIA 全为零；唯一下一路线是 batch-13 native gates。

## Beat 106：Flow native-gate batch 13

第一百零六拍在 generic executor `2026-07-18.5` 中加入 batch-13 精确 authority：saved-plan `.4` key/input/set/report、support `.5` resolution/report、batch-12 route execution/report、所选 source envelope、59/28/13 分类与 100/13 cardinality 必须一致，任一漂移都会在首个分支前 fail closed。旧批次准入、typed-QA 和 terminal-DAG 回归保持通过。

真实 input `462a14a70e8352db`、execution `988668c8c56e7400`、report SHA `2a3240bab1b88d302e43e1543fd8d4c3ab8f711ef09dab4bb1c946241ae376c3`。100 branches/500 次 allowlisted、credential-free offline/report-only TianGong calls 全成功，峰值并发为四；100 findings/typed decisions 与十三条 markers 完整保留，201/200 DAG completed。Project 和独立审计重新打开 ordered 2,900-member、7,024,640-byte archive，逐项比较 descriptor/member；archive SHA 为 `aceabc033f9632c43aaca73d9ceabdea6ca8dd77a3b64ec56b6250990daa946e`。

Kubernetes isolation、Bazel action/CAS、Argo bounded DAG/archive 与 OCI descriptors 继续落为本地模块、合同、工件和测试；完整框架未安装。22 个登记工件使事件 `6532→6560`；replay 保持 6,560 events、31,673 files、event SHA `b5b6a3a987fba43f23bde820f2303fc7826cf4fc990c770b46c501eb043fe739`、machine SHA `bb1bdefe1f1c60fe2b302bade8c724753ee4e3c266e4b2a9032ac7e917d5f6f6`。Entity/save/remote/model/comparison/LCIA 为零；唯一下一路线是 batch-14 supports。

## Beat 107：Flow 支撑消解 batch 14

第一百零七拍在共享 immediate-prior admission 表中加入 batch-13 的精确 authority：logic `2026-07-18.5`、input `462a14a70e8352db`、plan set `9f872eb1c10cbad6`、execution `988668c8c56e7400`、report/archive hashes、100/500/201–200/2,900 cardinality、十三条 markers 与零 effects。任一字段或 content descriptor 漂移都会在首次分类 probe 前 fail closed；旧批次准入回归保持通过。

真实 key `3d0478a9d71bfc30`、input `159ad89ba2f3c1ba`、resolution `4487c6e7113b3346`、report SHA `1aa9bfa058362359d0c3d259d31de74a6f626c198ee1a7bb15a1b4be82396cf7`。100 stable joins 覆盖 55 air/`1.3.4`、36 water/`1.1.3`、9 ground/`1.2.3`；三个 credential-free offline TianGong probes、100 次 Mass assignment、四份核心 ledger 与九条 marker 一一闭合。完整覆盖源继续从 Project registry 动态选择并复核为 1,428,215 bytes、SHA `268c0977394b659f7abc609807b1ae357c4c32fbf6627326f2ecaa9e00cc13ed`。23 个当前 Activity 工件全部通过独立 path/bytes/hash 审计，failures 与禁止 effects 为零。

Kubernetes reconciliation、Bazel action/CAS、OPA attributed decisions 与 Terraform plan/apply separation 均落实为本地 admission、binding、ledger、artifact 和 test；完整框架未安装。事件 `6560→6590`；replay 保持 6,590 events、31,697 files、event SHA `10fca260db7affdcc4977b14fbe4e3809deb15803785c99ef9d1d8e61339feba`、machine SHA `db447318af5a0c9115185c974ca3c4e3ac2ae95cb6d214bb0d29474ba3c33422`。唯一下一路线是 batch-14 saved plans。

## Beat 108：不可变 Flow saved-plan batch 14

第一百零八拍在 generic compiler `2026-07-18.4` 中加入 batch-14 的精确准入：support `.5` key/input/resolution/report、55/36/9 分类、batch-13 native execution/report 与所选 source envelope 必须同时一致。Project 把真实 support event `6589`、resolution 与 report SHA 绑定为 planning anchor；任一 provenance、cardinality、marker 或 effect 漂移都在生成计划前 fail closed。

真实 input `5fed25f62bee0917`、plan set `624a32d07a2c8ed6`、report SHA `72fe69a8896773e031364344cf9822bbc129eb7d9c2de43fa73ad45c81ce6ae8`。100 plan/descriptors/assertions 覆盖 55 air、36 water、9 ground，总计 1,261,645 bytes（单份 12,548–13,039 bytes）；九条 marker 保留，failure 空，201/200 DAG 含 100 completed plan nodes、100 pending native nodes 与一个 pending release-analysis node。122 个登记工件及所有计划 content descriptors 逐 path/bytes/hash/digest 复核。

Terraform immutable plan/apply separation、OCI descriptors、Bazel CAS 与 OPA risk carry-forward 继续落为本地模块、合同、工件和测试；完整外部框架未安装。事件 `6590→6718`；replay 保持 6,718 events、31,820 files、event SHA `847b034eb40ba1cdcbd60ffac77970b21681174fdecc686a59abff4aaf288f0d`、machine SHA `3d2a5525f47d9c2140f1d349f1fadc349182721d8bfb5d9bf9f70b2ff7503a86`。Native/entity/save/remote/model/comparison/LCIA 全为零；唯一下一路线是 batch-14 native gates。

## Beat 109：Flow native-gate batch 14

第一百零九拍在 generic executor `2026-07-18.5` 中加入 batch-14 精确 authority：saved-plan `.4` key/input/set/report、support `.5` resolution/report、batch-13 route execution/report、所选 source envelope、55/36/9 分类与 100/9 cardinality 必须一致，任一漂移都会在首个分支前 fail closed。旧批次准入、typed-QA 和 terminal-DAG 回归保持通过。

真实 input `60f168dc364bf9cd`、execution `0b80309731f4b0b4`、report SHA `2232bf0cb539314e62004d537a3ff8245ebe46a34fdbe1e83222c9bf92acb2cf`。100 branches/500 次 allowlisted、credential-free offline/report-only TianGong calls 全成功，峰值并发为四；100 findings/typed decisions 与九条 markers 完整保留，201/200 DAG completed。Project 和独立审计重新打开 ordered 2,900-member、7,024,640-byte archive，逐项比较 descriptor/member；archive SHA 为 `cd4de5ba9a62985f6e498f15e469f0f353f0087b5c5a74623299a24e9e19a207`。

Kubernetes isolation、Bazel action/CAS、Argo bounded DAG/archive 与 OCI descriptors 继续落为本地模块、合同、工件和测试；完整框架未安装。22 个登记工件使事件 `6718→6746`；replay 保持 6,746 events、31,843 files、event SHA `b29cfe742eab71d3108e0923c6169daba1cffe88adad9e05893a6181752254d6`、machine SHA `726d9fa0292c8e89b7dd8c44466b21a1e2ba2c6c15da87c2c4dd5598fa8498b6`。Entity/save/remote/model/comparison/LCIA 为零；唯一下一路线是 batch-15 supports。

## Beat 110：混合属性 Flow 支撑消解 batch 15

第一百一十拍由真实数据触发动态加深：batch-15 descriptor 首次不是单一 Mass emission，而是 95 Mass/kg emission、4 Area*Time/m2*a resource 和 1 Energy/MJ resource。旧测试夹具把所有续批复制成 Mass，导致 `_select_batch` 先于证据读取拒绝真实 descriptor。紧反馈回路、真实 descriptor 最小重现和 mixed-property 回归测试证明根因后，选择器改为从 selected intents 重建并核对 group counts；不能只放宽字段。

Resolver 升级为 `2026-07-18.6`。它复用第 67 拍已验真的 Mass、Area*Time、Energy 三份 FlowProperty/UnitGroup 合同，并新增两条精确 TianGong 分类策略：Land use + Area*Time + input + ground-resource → `3.1` Land occupation，保留中风险解释标记；Coal + Energy + input + ground-resource → `2.1.3` Non-renewable energy resources from ground。策略绑定名称、属性、方向和 source category，未泛化成不受约束的“resource/ground”规则。

真实 key/input/resolution/report 为 `42c33c64a4a06c05` / `bdf7c6c2ba9005fa` / `5c2d82a6fe8f1d25` / `f960ff126506bccd82cea2e5f4cdda7db867f2fc22cd7e8e8d03e1c6b9859140`。100 stable joins 覆盖 48 air、41 water、6 ground emission、4 Land-use resource 与 1 Coal resource；五条离线原生路径、三份 support contract、100 assignments/readiness 一一闭合。Review ledger 含 6 low ground→soil 与 4 medium Land-use→occupation markers；25 个 Activity 工件全部通过独立复核，failures/effects 为零。

`.5` 候选在发现逻辑版本未失效旧结果后保留为 append-only 证据；`.6` 以新 input identity 重跑并成为唯一 authority。接受事件 `6778→6810`；replay 保持 6,810 events、31,895 files、event SHA `5c542e37dc0a205b21b96fef2a1073a8e4a6e6dbeded5bcb68f0ad540e832445`、machine SHA `127c1d69260eac7937d41b86605bda3326e4b2e7373279864e1d4800cd5256ae`。唯一下一路线是 mixed-property batch-15 saved plans。

## Beat 111：混合属性不可变 Flow saved-plan batch 15

第一百一十一拍将 generic compiler 加深为 `2026-07-18.5`：准入精确绑定 batch-15 `.6` support authority、batch-14 native route 与所选 source envelope；每个 plan 按 FlowProperty ID 选择 Mass、Area*Time 或 Energy 合同，不将混合属性压平为 Mass。Low/medium marker 保留原始严重度和归属，不再使用“全部 low ground marker”的历史假设。

真实 input `ed20693a595d1723`、plan set `534c3aa6dcc0e7b6`、report SHA `a4042c6990a54932d63ee8d5437124ea2dbc616dede8533d0487e1de5c35922e`。100 份 immutable plans 覆盖 95 Mass、4 Area*Time、1 Energy，总计 1,269,730 bytes（单份 12,624–13,094 bytes）；6 low 与 4 medium markers 保留，failure 空，201/200 DAG 含 100 completed plan nodes、100 pending native nodes 与一个 pending release-analysis node。独立审计重新解析 100 份计划，并逐 path/bytes/hash 校验 122 个登记工件。

Terraform immutable plan/apply separation、OCI descriptors、Bazel CAS 与 OPA attributed risk 继续落到本地模块、合同、工件和测试；完整外部框架未安装。事件 `6810→6938`；replay 保持 6,938 events、32,040 files、event SHA `d76fce6bdb87c8ce1107da50a9ca4e81dff14980867d63d496a791cb065cf285`、machine SHA `396ab52275e2ffec70559f3bfe56456b9b0847a3d7b87aecc0f49fdd2dc95ae2`。Native/entity/save/remote/model/comparison/LCIA 仍为零；唯一下一路线是 batch-15 native gates。

## Beat 112：混合属性 Flow native-gate batch 15

第一百一十二拍将 native admission 升级为 `2026-07-18.6`：在任一分支启动前，精确绑定 saved-plan `.5`、support `.6`、batch-14 route、95/4/1 property counts 和 6/4 marker severity。共享执行器本身保持 property-neutral：它按每份冻结 plan 的分类与 FlowProperty ID 物化、校验，不新增三套分支逻辑。

真实 input `4b850db915dc5ff5`、execution `4c35d988ad94df48`、report SHA `38ac5ef1311a48a5a886d430e67630be262a189c23ac3a3c6e1eeacb58a479ec`。100 branches/500 次 allowlisted、credential-free offline/report-only TianGong calls 全成功，峰值并发为四；100 findings/typed decisions 与 95 Mass、4 Area*Time、1 Energy support assertions 完整保留，分类分布为 `1.1.3=41`、`1.2.3=6`、`1.3.4=48`、`2.1.3=1`、`3.1=4`，201/200 DAG completed。

Project 和独立审计重新打开 ordered 2,900-member archive，逐项比较 descriptor/member path、order、size、bytes、SHA 与 digest；archive SHA 为 `bdb2149a1856c7b0814f81b46583fb8f7807a36e88125c6317978fdb0d98bc73`。Kubernetes isolation、Bazel action/CAS、Argo bounded DAG/archive 与 OCI descriptors 继续落为本地模块、合同、工件和测试；完整框架未安装。22 个登记工件使事件 `6938→6966`；replay 保持 6,966 events、32,063 files、event SHA `e23c738ee899b9ac4836436a46cc94d4cf9b177c78bc017b1d788398f5012e03`、machine SHA `312d2fab86161fd5deb04bc67282732272d34dd5839c041a46ada11ec92eae4f`。Entity/save/remote/model/comparison/LCIA 为零；唯一下一路线是先读取真实 batch-16 tail descriptor 再解析 supports。

## Beat 113：最终资源 Flow 支撑消解 batch 16

第一百一十三拍先读取真实 descriptor：最后 51 条全是 elementary resource，其中 45 Mass/kg、6 Energy/MJ；源分布是 43 ground、4 water、3 air、1 biotic。Resolver 升级为 `2026-07-18.7`，不延伸 batch-15 的 Land/Coal 规则，而是以精确 name allowlist + property + Input direction + source compartment 选择 9 条本地 TianGong 资源路径。

真实 key/input/resolution/report 为 `1388a23ccbf90529` / `10a240a578f79172` / `2bbadf526faae1d3` / `9486eda41ea78283bb8d93d5cedaf12055e308bf94e6eeb501dd1e3d0f4a246e`。分类计数是 `2.1.1=24`、`2.1.2=18`、`2.1.3=1`、`2.1.6=1`、`2.2.3=1`、`2.2.5=1`、`2.2.6=2`、`2.3.3=1`、`2.3.5=2`。49 条 medium markers 保留可再生性、material/element 与 biotic→ground 等解释风险，不阻断 saved-plan 编译。

Crude oil 在同一 source Flow ID 下同时有 Energy/MJ 和 Mass/kg 观察。新 admission 只绑定与 accepted intent FlowProperty ID 一致的 Energy 观察，对该属性内的冲突仍 fail closed，并把 Mass 变体完整保留在 source/risk ledger，没有静默丢弃或转换。第一次真实 Activity 因 subprocess allowlist 未包含新 code 而在 probe 前失败关闭；失败证据追加保留，显式 allowlist 测试与第二次执行通过。29 个 accepted Activity 工件与四份 51-row ledger 逐 path/bytes/hash 复核；事件 `6966→7004`，replay 保持 7,004 events、32,104 files、event SHA `db5feeb82329bb6a798d9f160f4f8cb83a2943369511e9bb1b659b3f47a53787`、machine SHA `d04a097433a5d352474c9ba3ae5d7d76454420305fe7098ad882e96dde19392c`。唯一下一路线是 final batch-16 saved plans。

## Beat 114：最终资源 Flow 不可变 saved-plan batch 16

第一百一十四拍将 compiler 升级为 `2026-07-18.6`，精确准入 batch-16 `.7` support key/input/resolution/report、batch-15 native authority、所选 1,323,034-byte source envelope、51/49 cardinality、9 类 code 与 2 份 support contracts。生成器仍从稳定 intent 表派生计划，不复制一套 tail-only renderer。

真实 input `abc7ed216aedfca0`、plan set `88becab5ea06c78a`、report SHA `1cfcc0ae9cef919c59fea831f3fb3cd1583cc3ecb53fe2a509a92b0074da0fd0`。51 份 immutable plans 覆盖 45 Mass、6 Energy，总计 668,349 bytes（单份 12,676–13,187 bytes）；49 medium markers 保留，failure 空，103/102 DAG 含 51 completed plan nodes、51 pending native nodes 与一个 pending release-analysis node。Crude-oil plan 绑定被选 Energy observation hash 和上游变体风险证据，未构造 conversion 或第二身份。

73 个登记工件与所有 plan/content descriptors 逐 path/bytes/hash/digest 独立复核。Terraform plan/apply separation、OCI descriptors、Bazel CAS 与 OPA risk carry-forward 继续落为本地模块、合同、工件和测试。事件 `7004→7083`；replay 保持 7,083 events、32,178 files、event SHA `994a3f2101eac271487f8f8b44f75c6212986230906a47ebeae2e346ffec429b`、machine SHA `4768d7abf8a27d7c38854428235facae55637227ec4cbcdb690c69aee00675a0`。唯一下一路线是 final batch-16 native gates。

## Beat 115：最终资源 Flow native-gate batch 16

第一百一十五拍将 shared native executor 升级为 `2026-07-18.7`。精确准入 `.6` saved-plan input/set/report `abc7ed216aedfca0` / `88becab5ea06c78a` / `1cfcc0ae9cef919c59fea831f3fb3cd1583cc3ecb53fe2a509a92b0074da0fd0`、`.7` support lineage、batch-15 native authority、45/6 properties、9 类 code、2 份 support contracts 与 49 medium markers。执行器仍按 plan 内容工作，没有资源专用分支；最后出口显式变为 corpus closure，而不是虚构 batch 17。

真实 native input/execution/report/archive 为 `d9c04523c47c80ed` / `79ddf50eee031804` / `eb333489c97f42775c4fd251ea5c453538660762d32c34a0d0aa2ba60b9c14ab` / `3c688c044d4c7747ccf5cd37e3630783707b611dd923548064dcfe02fb418a65`。51 个隔离分支、255 次 offline/report-only TianGong 调用全部成功，峰值并发四；51 份 classification/support/finding/typed-decision 与 49 条 medium risk 全部保留，103/102 DAG 完成，entity/save/remote/model/comparison/LCIA 仍为零。

独立审计逐一核对 22 个 Activity 工件与 ordered 1,479-member、3,584,000-byte archive 的 path/bytes/hash/digest。Kubernetes 隔离、Bazel action/CAS、Argo 有界终态收集和 OCI descriptors 均保持为本地模块、合同、工件和测试。事件 `7083→7111`；in-memory replay 重建 7,111 events、5,487 registered artifacts 和两种投影，event SHA `b262aa37d3b030a31ea475a53e7d07295ad6c05efbc67b81213a0b9bf2ae03fe`、machine SHA `d6bc40cc1dfd39d23ae8a51117e20d0d4ebafbeab28528a9da574e4153e8844d`。唯一下一路线是完整 1,456-intent Flow corpus closure analysis。

## Beat 116：完整 Flow-authoring corpus closure

第一百一十六拍新增 `uslci_flow_authoring_corpus_closure/v1`。Project 从 append-only history 选择 canary 与 batches 2–16 的最新 accepted support/saved/native authorities，再生成 40,831-byte、113-binding manifest。Standalone kernel 重开并验哈希所有报告和 ledger，逐 intent 连接 identity/classification/property/risk，不从 current projection 猜测历史。

真实 input/closure/report 为 `774bd8fb62b0b4b8` / `871ddbd34d1e7b9c` / `6b7e690b9cdd05f849fd32e8b8eb76fd0337a94cbf8b0cbfdbb664b33495c093`。1,456 个 intent/source/target 各自唯一，碰撞为零；Mass/Energy/Area*Time 为 1,442/9/5。196 个风险完整守恒，low/medium/high 为 140/55/1。唯一 high-risk row 同时是唯一 `flow-product` namespace 的 CPC `33370` canary，其余 1,455 行为 `flow-elementary`。

OPA 式 risk partition 落为 1,260 clean、195 retained-review、1 held-conflict；Kubernetes finite reconciliation 落为 49/48 closure DAG；Bazel CAS 落为 authority manifest；OpenLineage facets 落为 16 个 slice digest summaries。独立审计复核 113 source bindings、10 Activity artifacts 与全部输出行；idempotent rerun不追加事件。事件 `7111→7127`，event SHA `67fdfcad8f5bd66d2f792c675c62ec1e5da437e78fc4ec7b08fd1d4aa30adc90`、machine SHA `1ca99d89aee19c26d4ee09ab362be29e0aeb7c8c73c78a03cec7cd944deb398e`。实体 release 仍未授权，唯一下一路线是 immutable three-lane release plan。

## Beat 117：完整 Flow corpus immutable release plan

第一百一十七拍新增 `uslci_flow_authoring_corpus_release_plan/v1`。Project 从 append-only history 恢复 1,456 份 saved-plan artifact refs 和对应 native terminal rows，生成 1,446,947-byte authority manifest。Standalone compiler 逐一重开总计 18,405,261 bytes 的源计划，并把 plan path/hash/bytes、saved/native report、terminal ledger 与 canonical row digest 绑定到 closure identity。

真实 input/plan-set/report 为 `baf99c6c14f08ba0` / `80c2f62b344154d7` / `7f4853e5c6b0c7dae01ae4d6979861d40f00fc1a22dd816793047d0cd376393d`。为控制 artifact/context 增长，没有复制 1,456 个新文件；一个 JSONL plan set 与 ordered-hash index 提供 per-row immutability。1,456 个 plan hashes 全部唯一，risk 196、lanes 1,260/195/1 均守恒；1,457/1,456 DAG 保留一个 pending canary gate。

Terraform plan/apply 分离、Bazel CAS、OCI path/hash/bytes descriptor 与 OPA risk lanes 都落为模块、合同、工件和测试。三条 deterministic canaries 覆盖 Mass/Energy/Area*Time，分别来自 corpus positions 1/3/2，lanes 为 review/clean/review；执行授权仍为零。独立审计与 idempotent rerun通过。事件 `7127→7143`，event SHA `b3663d5e098865a8c22319d3d5dc8e218fbf8fb5e0a85596dc6ef83d44cfc9b3`、machine SHA `a9295dd4fdd722c9ef39063f13eca1e53a91aca103bfcb3bd8d4219c94ce6942`。唯一下一路线是 isolated three-property entity-release canary。

## TianGong 第一权威跟随式第二案例

第二案例把 TianGong 与 Research Kernel 的所有权边界落实为可运行架构：TianGong 是官方数据身份与版本、TIDAS 语义、Provider 关系、LCIA 方法、原生工具及原生结果的第一权威；Kernel 只负责只读同步、精确绑定、本地编排、验证、解释、失效、重算、重放与交付。历史 LiFePO4 证据继续固定在 `@tiangong-lca/cli@0.0.25`，新阶段通过独立 capability-adoption 事件精确采用 `@tiangong-lca/cli@0.0.29`，没有浮动版本或改写历史。

普通用户 Open Data Process 精确读取与官方公开结果构成可接受主路线，受保护的 Calculation Bundle 只是可选的更强证据，官方 TIDAS 导出是无网络回退。该分层纠正了把单一角色权限误当全局阻塞的 selection-surface narrowing。Kernel 从官方快照 manifest 机械绑定 5,832 个根过程，经 Schema 和九门槛逐层筛选；122 个根通过 Schema，其中 121 个与 LiFePO4 实质不同，最终只接受 Freshwater distribution service `69605c09-e319-4e53-8d5f-c944c763c808@01.01.001`。不可变 Authority Receipt `50d206a2899a86b1` 把精确 Process、参考 Flow、1 m3 功能单位、64-node/63-edge Provider 闭包、25 个方法结果、TIDAS Schema、CLI runtime 和所有来源哈希连接起来；Kernel 没有运行影子求解器。

依赖图与权威转换是通用模块，不按案例名称分支。由于官方历史只有一个版本，本阶段采用明确隔离的 `synthetic_binding_canary`：只改变精确 LCIA method version，`official_update_claim=false`，只失效 native binding 及两种投影，保留 capability adoption 与 case selection，且不推进当前权威。它证明精确失效/有序重建机制，不声称 TianGong 发生了真实更新。

最终包固定为八个交付件，包含权威历史、机器状态、中文报告、重放证据、风险/反馈登记、验证、manifest 与报告。干净上下文验收在无凭据子进程中只复制已登记证据、四种当前投影与可重开 source bindings，逐字节重建八个交付件，并验证源 Project 树未改变、网络读取与远程写入等禁止副作用为零。该案例证明一个实质不同官方案例的权威跟随、原生结果保真、精确 transition、重放和本地交付；不外推为通用 LCA 成熟度，也不消除整包 matrix readiness 失败、Provider 数值权重未暴露及公开数据缺项风险。
