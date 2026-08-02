# OpenSpec 状态说明

OpenSpec 保存“为什么变更、规范增量、设计决策和任务证据”，不替代当前使用文档。

- 当前系统事实与结构：`docs/design.md`
- 配置、部署和使用：`docs/config.md`、`docs/deploy.md`、`docs/usage.md`
- 变更完成度：只看对应 `changes/<name>/tasks.md`
- `openspec-cn list` 中的 complete 表示实现任务完成，不等于公网部署、录屏或现场验收完成

当前依赖顺序：

```text
add-general-memory-core
├── add-agent-active-memory
├── enhance-memory-metadata
│   └── add-investment-research-profile
│       └── add-memory-relations
│           └── automate-memory-relations
│               └── harden-memory-relations
│                   └── benchmark-investment-memory-quality
│                       └── harden-policy-routing-recall
│                           └── harden-memory-maintenance-recall
│                               └── harden-recall-delivery-reliability
└── streamline-project-maintenance
```

`add-general-memory-core` 继续保留公网 HTTPS/安全组、现场脚本和录屏等交付任务；这些
任务没有因为代码完成而勾选。其余已完成变更暂不抢先归档，避免在基础变更仍活动时
把依赖增量分散到主规范与 changes 两处。等交付任务完成，或正式拆成独立
`release-acceptance` 变更后，再按上述依赖顺序同步并归档。

归档时先处理独立的 `streamline-project-maintenance`，再从
`add-agent-active-memory`、`enhance-memory-metadata` 沿依赖树向下，最后处理
`harden-recall-delivery-reliability`；基础 `add-general-memory-core` 只在 5 个真实环境/
现场任务完成或拆入独立 acceptance 变更后归档。这样不会用“complete”掩盖尚未执行的
公网、专用测试库和现场验收。
