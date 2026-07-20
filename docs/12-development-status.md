# 当前开发状态

更新时间：2026-07-20（Asia/Shanghai）

## 当前分支

- 实现工作树：`index` / `main`。
- 测试工作树：`index-test` / `test`。
- 当前目标：按已审核方案把 SQLite 升级为正式 runs/events 日志库，并完成迁移、任务生命周期和结构化查询。
- 当前状态：实现、跨工作树门禁、本地提交、当前项目正式迁移和 marketplace 源同步已完成；GitHub 推送/Actions 与 Codex 插件缓存重载尚未完成。

## 已完成实现

- 每个 project 使用独立 `esp_mcp.sqlite`；runs/events 按 `project_id` 隔离。
- schema v2 包含复合主键/外键、JSON 对象约束、规范 UUID 约束和常用查询索引。
- 首次连接设置 WAL 时对并发锁做有界重试；schema 初始化在 `BEGIN IMMEDIATE` 后复查版本和形态。
- v1 的日志、hardwork、memory 表会在单一事务内重建并保留数据，失败时整体回滚。
- sequence 分配与事件插入处于同一写事务；UUID 幂等身份包含规范化时间戳和全部内容。
- run 只允许 `running -> succeeded/failed/cancelled`；同终态重试幂等，冲突终态和终态新事件拒绝。
- legacy JSONL 使用稳定快照、SHA-256 marker 和 UUIDv5；复制文件不重复导入，已有原生 running run 不会被提前结束。
- JSONL 审计镜像携带 `task_type` 和 `selected_port`；导入器管理的 run 允许端口从 NULL 受约束地回填，非空冲突不得覆盖；原生 run 只允许已有 UUID 严格去重，新 UUID 显式冲突且不产生任何变更。
- `esp_logs_latest/get/query` 从 SQLite 读取；结构化过滤已同步 CLI、FastMCP schema 和静态工具注册资源。
- build、clean、backup、flash、erase、restore、reset、port select、file、exec 和固定时长 capture 使用统一同步任务生命周期。
- Monitor start 固化完整 `LogScope`，worker 在原项目写终态；用户停止为 cancelled，断连/错误为 failed；stale manifest 通过确定性事件与 `sqlite_reconciled` 标记可重复对账。
- 动作或状态变更前的 SQLite 正式日志失败会阻止执行；SQLite 已提交后的 JSONL/latest 镜像失败和执行后的收尾失败保留真实业务结果并附加 warning。optional 默认端口在 run 与业务调用间冻结一致。

## 本地验证

- 独立 Conda 环境：`C:\Users\16224\anaconda3\envs\esp-mcp-toolchain\python.exe`。
- 测试工作树通过 `ESP_MCP_SOURCE_ROOT` 和跨工作树脚本显式加载实现工作树源码。
- SQLite 定向契约：`33 passed`。
- 跨工作树完整门禁：`134 passed in 21.44s`。
- 覆盖 fresh/v1 schema、首次并发建库、hardwork/memory 复合键、并发 sequence/import、UUID/时间戳、run 终态、selected_port、JSONL 增长与复制去重、native run 冲突隔离、嵌套任务、Monitor 跨项目/stale 对账、MCP schema 以及前后置镜像故障。
- 当前项目已正式创建 schema v2 SQLite：首轮导入 19 files / 32 events，第二轮导入 0 / 0 / 0；状态为 12 cancelled、2 failed、5 succeeded，19 个 migration marker，外键检查为空。
- 本轮没有烧录、擦除、删除、full clean 或其他真实硬件动作；SQLite 存储层不需要新增实板门禁。

## 待完成

1. 推送 GitHub 并确认 Windows/Linux、Python 3.10/3.12 矩阵。
2. 通过 Plugin Management 重新安装/加载 `0.1.0+codex.20260720110129`，在新任务核对 SQLite 工具 schema 与源码版本。

在上述步骤完成前，不把 SQLite 标记为已发布到当前 Codex 插件缓存，也不自动开始项目合并/导入导出体系。