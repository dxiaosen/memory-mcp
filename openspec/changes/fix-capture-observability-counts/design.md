# 设计决策

## D1：计数语义用新增字段，不重命名 candidate_count

**决策**：`memory.capture.completed` 新增 `extracted_candidate_count`（模型原始抽取，
含被拒）与 `outcome_count`（产生 decision 的总数）。`candidate_count` 保留为通过校验、
进入 Candidate 构造的数量，不重命名。

**理由**：`candidate_count` 是已发布的日志字段，重命名会破坏现有日志查询与监控。
新增字段 + 恒等式 `outcome_count == auto_saved + pending + discarded + blocked`
让各计数可互相验证。`extracted >= outcome >= candidate`（解析失败整轮 raise，通常
extracted == outcome）。

## D2：被拒候选走独立 validation 内容事件，不改 CaptureOutcome DTO

**决策**：`CaptureOutcome` 是对外 DTO（不携带正文），不加 proposal 字段。新增内部
`RejectedProposal` dataclass 记录被拒 proposal 的关键字段，经 `CandidateProcessingResult.
rejected_proposals` 透传，由 `memory.capture.validation` 内容事件输出。

**理由**：recommend.md §1 要求"能看到被拒完整候选"。被拒候选未进入 Candidate 构造
（缺 source_metadata），但 proposal 的 subject/content/source_expression/assertion_kind
足够调试。用内部结构而非改 DTO，保持向后兼容。

## D3：原子化靠 prompt 引导，不改 Candidate/Evidence DTO

**决策**：Candidate/Evidence 单 `source_expression` 是对外 DTO，不改。靠 prompt 引导
"一候选一事实/一推断，混合需拆分"。多来源覆盖由拆分后的多候选各自带 Evidence 实现
（`load_recall_evidence` 已支持 per_revision 多条）。

**理由**：约束明确"不改 DTO 向后兼容"。改 Candidate 支持多 source_expression 是大改动，
且原子化拆分本身就是更优结构（一条记忆一个事实，便于生命周期去重与召回）。

## D4：assertion_kind 归一化纳入 expression_basis

**决策**：`_normalize_assertion_kind` 增 `expression_basis` 参数。document/tool/web 来源
+ inferred -> system_inference（从材料推断的结论非原始事实，本次日志"资本开支强度"即
external_fact+inferred 违规）；+ explicit + 非外部 -> external_fact；+ ambiguous 保守不改。
assistant + user_* -> system_inference（保留）。

**理由**：recommend.md §3 要求 assertion_kind 与 expression_basis 一致。旧逻辑只看
assertion_kind，导致 external_fact+inferred。inferred 表示模型推断，不可能是直接摘录的
原始事实，应归 system_inference。

## D5：source_uri 用 os.path.relpath 转 workspace-relative

**决策**：`extract_document_messages(transcript_path, *, cwd=None)`，cwd 提供时用
`os.path.relpath(file_path, cwd)` 转，分隔符统一为 `/`。跨盘符/无 cwd 保留原路径。
Host Adapter 传 `event.cwd`（AgentTurnEvent 已有 cwd）。

**理由**：recommend.md §4 要求避免长期记忆绑定绝对路径。relpath 是标准库跨平台方案。
不强行截断（跨盘符 relpath 可能返回含 `..` 的路径或绝对路径，保留结果优于误删）。

## D6：Capture 分阶段耗时三段在 process 内累加透传

**决策**：extraction/relation/persistence 三段在 capture_service 用 perf_counter 计时
（易）；validation/admission/lifecycle 三段在 `process` 循环内用累加变量计时，经
`CandidateProcessingResult.timing` dict 透传（内部 dataclass，非 DTO）。未执行阶段记 0。

**理由**：recommend.md §5 列 6 阶段。process 内三段交织在单循环，用累加变量分别记录
每段 perf_counter 差值。`CandidateProcessingResult` 是内部结构，加字段不违反 DTO 兼容。

## D7：日志顺序对齐业务，relations_planned 后移

**决策**：当前 `relations_planned` 的 log 在 plan 调用后、candidates/admission 之前输出，
但业务执行顺序是 候选处理 -> 关系规划。将 relations_planned 的 log 移到 admission 之后、
relation_candidates 之前。新增 validation 事件在 candidates 之后、admission 之前。

**理由**：recommend.md §6 要求日志反映真实业务顺序，且"不要只为顺序移动代码"。这里
log 本就在各自业务点之后，只是排列顺序写反了；调整排列不改变业务逻辑。
