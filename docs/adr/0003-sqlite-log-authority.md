# ADR 0003：SQLite 作为正式日志查询源

- 状态：Accepted
- 日期：2026-07-20

## 背景

JSONL 适合追加和人工审计，但不能可靠承担并发序号分配、结构化过滤、严格幂等和跨项目复合约束。后台 Monitor 又要求任务可以跨活动项目切换继续运行，并在 worker 线程中安全写入终态。

## 决定

### 双层存储

SQLite 是 runs/events 的正式状态与查询源；每个项目使用独立 `esp_mcp.sqlite`。JSONL 和 `latest.json` 保留为审计镜像和旧数据迁移入口，原始串口字节继续使用分块文件。任何查询结果以 SQLite 为准，不静默回退到镜像。

### 身份和状态机

run 由 `project_id + run_id` 标识。event 使用规范 RFC 4122 UUID，并在同一 run 内分配单调 sequence。幂等身份包含规范化时间戳和全部事件内容。

run 只允许从 `running` 进入 `succeeded`、`failed` 或 `cancelled`。同终态重试可以幂等返回，冲突终态和终态新事件必须拒绝。异步 Monitor 固化完整 `LogScope`，终止时由 worker 关闭 run。

### 事务边界

首次连接设置 WAL 也必须容忍并发锁竞争；schema 检测与迁移在取得 `BEGIN IMMEDIATE` 后复查。事件插入和 `next_sequence_no` 递增处于同一事务。legacy JSONL 使用稳定快照、SHA-256 marker、UUIDv5 和冲突安全 marker 插入；导入器只结束自己创建或标记的历史 run，既有原生 run 仅允许已有 UUID 严格去重，新 UUID 显式冲突且不得改变端口或 marker。导入失败不写 marker，已写事件依靠 UUID 幂等安全重试。stale Monitor 使用确定性 complete event 与 `sqlite_reconciled` manifest 标记可重复对账到启动时绑定的项目。

### 动作与日志故障

动作或状态变更前无法建立 SQLite 正式审计时不执行。SQLite 事件已经提交后，JSONL 或 `latest.json` 镜像失败只作为 warning 附加，不能阻止业务动作；业务变更完成后的日志失败也不能把已经成功的硬件动作、端口选择或配置写入改报为失败或未执行。optional 默认端口必须在 run 创建与业务调用之间冻结一致。

## 后果

- 查询具有稳定字段、项目隔离和确定顺序。
- JSONL 仍可独立检查，但不能覆盖 SQLite 状态。
- 调试日志失败和业务动作失败必须分别处理。
- hardwork/memory 表具有 project-scoped schema；当前文件仓储的迁移另行决策。
- 跨工作树门禁必须由 `index-test` 显式加载 `index` 源码；测试数量属于验证状态，不写入本 ADR 的架构决定。
- `Accepted` 表示架构决定已接受，不等同于实现已经发布到 GitHub 或 Codex 插件缓存。

## 未采用方案

- 仅保留 JSONL：无法可靠提供并发序号、复合约束和结构化查询。
- 仅保留 SQLite：失去简单的追加审计镜像和对旧 session 文件的直接恢复路径。
- Monitor 每次写入时读取活动项目：项目切换会造成跨项目串库，因此禁止。