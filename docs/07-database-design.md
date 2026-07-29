# SQLite 日志数据库设计

更新时间：2026-07-29

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

`legacy_jsonl_imports` 只证明 runs/events importer 已处理某个来源快照，不证明
`raw_logs/errors` 已核验或投影。历史固定 capture 使用独立版本
`HISTORICAL_CAPTURE_RECONCILIATION_VERSION=1`：B4.3 只从显式安全 session basename
读取 JSONL，并把旧绝对 `raw_path` 降为词法身份；实际 evidence 必须从当前项目
`logs/raw/<basename>` 以安全 fd 重新计算长度和 SHA-256。legacy single-event 仍保持
`phase=unknown`，不绕过 B4.1 的最后 `complete` 限制。native mirror 只接受唯一 UUID
的两条 `prepare → complete` 记录，并要求 task/source/selected-port 与 completion
身份一致；成功 completion 必须有匹配 port，失败 completion 可省略 port 且 mirror
端口允许一致为 `null`。项目扫描、raw 跨 run 唯一归属、持 lease 的二次文件摘要与数据库
profile 比对、B4.1 调用和独立 marker 发布属于 B4.4。
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

三个查询入口使用独立的 SQLite URI `mode=ro` 连接，并设置 `query_only=ON`；它们不调用
数据库初始化、schema 迁移或 JSONL importer。缺库按空查询/未找到返回且不创建目录或
数据库；schema v2 只开放 runs/events，只读期间不升级；schema v3 缺少必需表/列、未知
版本或损坏持久化 JSON 均返回结构化错误。latest 的 run/last event 与 get 的 run/events
分别来自同一个显式只读事务快照。

WAL 模式即使以 `mode=ro` 打开，也可能在目录可写且协调文件缺失时创建 `-wal/-shm`。
这些是 SQLite 的并发协调文件，不是应用迁移或日志导入。日志数据库可能仍有并发写入，
所以查询层不使用只适合确定不再变化数据库的 `immutable=1`。

`esp_logs_get` 在 schema v3 中还会从同一只读事务返回 `raw_logs` 和 `errors`。它不调用
现有分连接 getter：两类记录都先按 `project_id + run_id` 过滤，再按时间/ID 倒序取
`limit + 1` 个最新候选，截去探针行后恢复时间正序。默认 raw 1000 条、error 200 条，
两个截断标志互相独立。schema v2 即使物理上存在旧 raw/error 表，也只报告
`artifact_capability.reason="schema_v2"` 并返回空数组，不推断、不迁移。

error 的 `file`、`exception_type`、`message`、`raw_text` 分别在 SQL 侧限制为
4096/256/2048/8192 个字符；逐记录 `field_truncation` 与汇总
`fields_truncated` 说明是否裁剪。raw 的 UUID、kind、相对 POSIX path、时间和 SHA-256，
以及 error 的 UUID、kind、行列号、recoverable 和时间，在返回前重新按仓储规则校验。
身份字段不静默截断；不规范或过长时整个查询 fail-closed。

这些上界约束新增 artifact 字段，不改变既有 events 的 `tail` 合同；因此不能把 C2 描述为
整个 `esp_logs_get` 响应的总字节上界。C3 读取真实 artifact 文件时还必须使用自己的
`max_bytes`，不能把详情行数上限当成文件读取授权。

### DB-first 错误解析

`esp_error_parse_log` 不复用公开 `esp_logs_get`，而是先捕获一次 `LogScope`，再调用
`read_error_parse_snapshot()`。该仓储入口使用 C1 的 `mode=ro + query_only + BEGIN`
连接，从同一项目/run 快照读取 C2 的有界 errors/raw_logs 和兼容 event 投影；不准备
数据库、不迁移、不导入 JSONL，也不会在读取后重新查询活动项目。

来源权威顺序固定为：

1. schema v3 正式 errors：直接返回最新正式结构化 error，不打开任何 raw 文件。
2. schema v3 正式 raw_logs：只消费 `serial_capture_raw` 与 `serial_monitor_chunk`，
   不与旧 event 文本混合。
3. 当没有可用正式 error/raw 时，才消费旧 event/Monitor 兼容证据。

兼容 event 查询只取最新 64 条。message 在 SQL 侧最多投影 8192 字符，之后仍受本次
`max_bytes` 总字节限制；payload 最多投影 16384 字符，若探针显示截断则整个 payload
不解码，不能从不完整 JSON 提取 error_report 或 raw_path。schema v2 只读取这些
runs/events 投影，即使物理上残留 raw/error 表也不启用正式 artifact 能力。

正式 raw path 必须与 kind/run 形状一致：固定 capture 为 `raw/<file>`，Monitor chunk
为 `serial/<run_id>/chunk-NNNNNN.bin`。日志根、每层父目录和文件均执行 no-reparse/
普通文件/读前后身份检查；单文件完整核验上限为 64 MiB。工具在同一安全 fd 上读取全文件
并比对 SQLite 登记 SHA-256，但只把 `max_bytes` 范围内的内容送给错误解析器。摘要缺失、
不匹配、路径类型冲突或目录身份变化都 fail-closed。

旧 Monitor 兼容先验证 manifest、磁盘 chunk 精确集合、长度和摘要，再有界读取；旧 event
raw_path 只允许当前项目 logs 内的安全文件。后者没有 SQLite 登记摘要，所以只能报告
“已计算 SHA-256”，不能标为“已与权威摘要比对”。输出用 `query_source` 标识 SQLite
版本，用 `source_truncation` 区分 raw/error/event 窗口或字段截断。

## 验证状态

- 2026-07-29 C3 本地门禁：专项 `7 passed in 1.09s`，相关回归
  `49 passed in 5.63s`，main 全量 `120 passed in 48.77s`，test 显式加载 main
  `557 passed, 4 skipped in 250.00s`。使用临时 SQLite/文件；未访问或升级正式数据库，
  未访问 COM3，远端 CI 尚未完成。
- 2026-07-20 历史本地门禁：SQLite 定向 `33 passed`，跨工作树完整测试
  `134 passed`。当时当前项目 19 份旧 JSONL 已完成迁移：32 events，状态分布为
  12 cancelled、2 failed、5 succeeded，外键检查为空。
