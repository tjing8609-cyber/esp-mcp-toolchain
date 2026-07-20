# SQLite 日志数据库设计

更新时间：2026-07-20

## 定位

每个已选择工程使用独立数据库：

```text
<storage_root>/<project_id>/esp_mcp.sqlite
```

默认 `storage_root` 为 `~/.codex/esp-mcp-toolchain/data/projects`；设置 `ESP_MCP_DATA_ROOT` 时使用该显式根目录。

SQLite 是 runs/events 的正式状态与查询源。`logs/sessions/*.jsonl` 和 `latest.json` 继续作为可阅读审计镜像；串口原始字节仍存放在分块文件中。查询工具不得扫描或静默回退到这些镜像。

## 核心表

### runs

- 复合主键：`project_id + run_id`。
- 必填字段：`task_type`、`status`、`started_at`、`next_sequence_no`、`payload_json`。
- 状态仅允许 `running`、`succeeded`、`failed`、`cancelled`。
- `selected_port`、`summary`、`ended_at` 为可选运行元数据。
- `payload_json` 必须是 JSON 对象。

### events

- `event_uuid` 是全局唯一、规范化的 RFC 4122 UUID；数据库和仓储层都会校验。
- `project_id + run_id + sequence_no` 唯一，且通过复合外键绑定 runs。
- `phase` 仅允许 `unknown`、`prepare`、`execute`、`verify`、`cleanup`、`complete`。
- `level` 仅允许 `debug`、`info`、`warning`、`error`、`critical`；旧 `serial` 归一化为 `info`。
- `ts` 统一归一化为带时区的 UTC ISO 8601。
- `payload_json` 必须是 JSON 对象。

按项目、时间、阶段、级别、工具和来源建立索引。`raw_logs`、`errors`、`hardwork_*`、`memory_*` 已改为 project-scoped schema；hardwork 和 memory 的当前运行时仓储仍是原有文件实现，不在本阶段切换。

## 任务生命周期

同步工具在动作或状态变更前创建 run 并写 `prepare`，完成后写 `complete`，最后把 run 置为成功或失败。嵌套工具调用复用外层 run，不产生重复任务。

后台串口 Monitor 是异步 run owner：start 固化 `LogScope`，worker 后续只使用启动时的 `project_id`、数据库和日志目录。用户停止记为 `cancelled`；断连或内部错误记为 `failed`。status/read/stop 是 follower，不另建 run。崩溃后恢复的 stale manifest 使用确定性 complete event 对账到原项目 SQLite；只有 run 终结成功才写 `sqlite_reconciled=true`，manifest-only 中断可再次重试。

终态不能改写。相同终态的重复 finish 是幂等操作；冲突终态会失败。终态 run 不接受新事件，但已有 UUID、相同规范化时间戳和相同内容的严格重试可以去重返回。

## 并发与幂等

- 首次连接设置 WAL 时对 `locked/busy` 做有界重试；schema 初始化随后使用 `BEGIN IMMEDIATE`，取得写锁后重新读取版本和表结构。
- 事件序号在同一 `BEGIN IMMEDIATE` 事务中读取并递增。
- 相同 `event_uuid` 的身份比较包含 project、run、时间戳、阶段、级别、工具、来源、消息和 payload；任一字段变化均返回冲突。
- SQLite 使用外键、WAL、5 秒 busy timeout 和 `synchronous=NORMAL`。

## JSONL 迁移

首次准备项目数据库时读取 `logs/sessions/*.jsonl` 的稳定快照，并用文件 SHA-256 记录 `legacy_jsonl_imports`。迁移可重复执行：

- 保留已有规范 UUID；旧 event_id 或无 ID 记录使用稳定 UUIDv5。
- 旧记录缺少 phase 时写入 `unknown`；无 phase 的 `STOPPED` 会话恢复为 `cancelled`，错误级别优先恢复为 `failed`。
- 复制同一份 JSONL 到另一文件不会产生重复事件。
- 已有原生 run 只允许既有 UUID 的严格身份去重；同 run_id 的新 UUID 返回 `native_run_import_conflict`，不得追加事件、回填端口或写入 marker。
- 导入器只结束由导入器创建或标记的历史 run：无 phase 文件按静态历史结束；有显式 phase 的 run 看到 `complete` 后才结束。已有原生 `running` run 只做 UUID 去重，不改变生命周期。
- JSONL 审计镜像写入 `task_type` 和 `selected_port`；迁移允许同一 run 的后续事件把端口从 NULL 回填为具体值，非空冲突拒绝覆盖。
- 单文件处理成功后才用冲突安全插入写 marker；处理中断不写 marker，已落库事件依靠 UUID 幂等安全重试。
- 已知旧 level 别名会归一化；无法识别的旧 level 映射为 `info`，并在 payload 保留 `legacy_level`。新事件写入仍严格校验 level。

v1 数据库迁移会在单一事务内重建 runs/events/raw_logs/errors 及四张 hardwork/memory 表，把旧数据写入指定 `project_id`，最后执行外键检查；复制或检查失败会整体回滚。

## 故障语义

动作或状态变更开始前无法建立 SQLite 审计时，工具 fail-closed，不执行业务变更。SQLite 事件已经提交后，JSONL 或 `latest.json` 镜像失败只形成 warning，不能反向否定正式审计或阻止业务动作。业务变更已经完成后，完成事件或 run 收尾失败不能覆盖真实结果；返回原始 `ok` 状态，并附加 `logging_persisted=false` 和 `logging_warning`，避免调用方误判后重复烧录、擦除、删除、端口选择或配置写入。optional 默认端口在创建 run 时被冻结并传给业务函数，防止审计端口与实际动作端口发生 TOCTOU 偏差。

## 查询接口

`esp_logs_latest`、`esp_logs_get` 和 `esp_logs_query` 只读 SQLite。query 支持全文词项以及 `run_id`、`phase`、`level`、`tool`、`source`、时间范围和 sequence 范围；sequence 范围必须同时提供 `run_id`。CLI 与 FastMCP schema 暴露同一组参数。

时间边界与事件时间使用同一 UTC 规范化函数；`from_ts` 晚于 `to_ts` 时拒绝查询，不按原始 ISO 字符串直接比较。

## 验证状态

2026-07-20 最终本地门禁：SQLite 定向 `33 passed`，跨工作树完整测试 `134 passed`。当前项目 19 份旧 JSONL 已在临时数据库完成只读迁移演练：32 events，状态分布为 12 cancelled、2 failed、5 succeeded，外键检查为空。