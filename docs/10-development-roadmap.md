# 开发路线图

路线按依赖关系推进。每项功能必须在实现工作树完成源码和文档，在测试工作树完成契约，并通过全量测试与对应硬件门禁后才能作为稳定版本发布。

1. Python CLI：串口枚举、选择、固定时长捕获和日志读取。已完成基础闭环。
2. MCP Server：使用官方 MCP Python SDK、`FastMCP` 和 stdio transport 暴露 tools、resources 和 prompts。已完成。
3. ESP 开发闭环：构建、备份、烧录、恢复、复位、文件操作和错误解析。串口、reset、Raw REPL 和错误检测已完成本轮软件加固；`erase_flash` 的受管进程树清理、显式前后复位参数和失败契约已通过本地及远端门禁。2026-07-30 已再次按明确授权备份当前 4 MiB、整片擦除并恢复 MicroPython v1.28.0；相对上传/下载、活动程序停止、受控异常解析、GPIO34 查询、回归、性能和 soft reset 的实板复验均已执行。
4. hardwork 上下文：附件归档、资料索引、硬件审查门禁和映射增量回写。已完成基础闭环。
5. 项目 memory：写入、读取、检索、更新和删除。已完成基础闭环。
6. 后台串口 Monitor：状态机、不可变项目绑定、游标读取、有界缓冲、分块落盘、跨进程串口锁和退出清理。软件测试、四平台 CI、插件缓存验证和历史真实 ESP 串口验收已完成。
7. SQLite schema 与仓储层：SQLite 已成为 runs/events 正式查询源；project-scoped schema、v1/JSONL 迁移、事务序号、UUID 幂等和 run 生命周期已完成并发布。
8. 日志查询增强：`run_id`、phase、level、tool、source、时间和 sequence 过滤已接通；后续导出和聚合属于非阻断增强。
9. 任务书 12 项能力：6 项基础和 6 项提高均已有正式工具或闭环实现；公开提示词重组为 12 套，工具面为 48 tools / 12 resources / 12 prompts。独立 Conda 启动器已绑定 `esp-mcp-toolchain` 环境，其中 `mpremote 1.28.0` 已验证。个人 Marketplace 源已更新为 `0.1.0+codex.20260730084223`，当前安装缓存仍为上一版 `0.1.0+codex.20260730053724`，等待重启核对；正式项目 SQLite 已显式升级到 v3 并完成历史补投影。当前实板门禁已覆盖 runtime、串口 Monitor、文件上传/读取/列表/相对下载、活动程序停止、受控错误解析、GPIO34、正向与 negative 回归、无硬件副作用性能插桩和 soft reset。实板发现的 exec/run-file 正式错误漏投影已在源码与合同中修复；重启后的受控 run `exec_code_20260730_150437_0d9c65aa` 已确认该 run 的 `esp_logs_get.errors` 恰好返回一条，解析来源仅为 `sqlite_errors`。
10. 项目数据迁移体系：工程路径重绑定、项目合并、导出、导入和完整性校验。与数据库 schema 迁移是两类任务，继续排在本轮任务书能力发布之后。

v3-B4.1、B4.2 与 B4.3 已完成双分支远端门禁。v3-B4.3 历史固定 capture
adapter 从显式安全 session basename 解析 legacy single-event/native final-complete
JSONL，旧绝对 `raw_path` 不参与 I/O，当前 `logs/raw` 文件用安全 fd 核对长度/SHA-256。
旧 writer 证据只登记 `serial_capture_legacy_text`；精确 raw 同时要求 native source
与 UUID 排他文件名；native 必须严格为唯一 UUID 的两条 `prepare → complete`，并保持
task/source/port 身份一致，合法失败可省略 payload port。legacy `phase=unknown`
显式不具备 B4.1 资格。专项
`50 passed, 1 skipped`，main 全量 `120 passed`；正式项目 4 个 capture
只读检查为 1 个 `resolved`、3 个 `ineligible`，禁止 SQLite connect 后 189 个项目文件
元数据/摘要不变。B4.3 不获取 lease、不访问 SQLite，也不发布 marker。
最终本地门禁为 main `120 passed`、test 自身源码 `508 passed, 4 skipped`；
main/test 两条 Actions 共 8 个 Windows/Linux、Python 3.10/3.12 job 全部成功。

2026-07-29 已完成 B4.4：schema v3 以 additive 方式增加
`historical_raw_claims`，规范 raw path 在项目内形成持久唯一主键；B4.1 在同一
`BEGIN IMMEDIATE` 内比较调用方提供的 event/run profile、精确 sequence 与
`next_sequence_no`，再提交 claim、raw/error 或整体回滚。项目协调器使用项目 lease、
Monitor run lease、两次 resolver、跨 run raw 所有权预检和独立原子 marker；v2 在任何
控制文件或迁移前拒绝。B4.1-B4.4 组合为 `145 passed, 1 skipped`，main 全量
`120 passed`，test 跨工作树全量 `531 passed, 4 skipped`。截至该源码门禁阶段，这些只
使用临时 v3 数据库；当时正式项目数据库仍为 v2，尚未执行升级或正式补投影。

2026-07-29 已完成 C1-C3 的本地软件链：查询入口使用只读事务且不在线迁移/导入；
`esp_logs_get` 在同一快照返回有界正式 raw/error；`esp_error_parse_log` 固定一次项目作用域，
按正式 errors → 正式 raw → 有界兼容 event/Monitor 选择。正式 raw 完整比对登记 SHA-256，
兼容 event 只取最新 64 条并在 SQL 侧限制 message/payload。C3 专项
`7 passed`，main 全量 `120 passed`，test 显式加载 main 全量
`557 passed, 4 skipped`。合并后 test 自身源码仍为 `557 passed, 4 skipped`；
[main run 30437244226](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30437244226)
与 [test run 30437262633](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30437262633)
共 8 个远端矩阵 job 全部成功。该阶段的证据仍来自临时数据库/文件，本身不代表随后完成
的正式数据库升级。

发布封口与后续边界：

1. Marketplace 同步、用户重启、正式项目 v2→v3 升级和历史补投影均已完成；v2
   备份、正式 schema-v3 完整性、5 条 raw 文件摘要、1 条 error 和第二次幂等回放已核对。
2. 相对下载已用新输出名完成实板复验：21 字节源/目标逐字节及 SHA-256 一致，实际路径
   位于 workspace，版本化安装缓存同名文件数为 0。
3. exec/run-file `structured_error` 修复已发布；用户重启后的受控异常确认
   该 run 的 `esp_logs_get.errors` 恰好返回一条，且 `esp_error_parse_log` 来源从兼容
   event 变为正式 `sqlite_errors`。
4. GPIO34 的实体 KEY1 active-low 行为仍需用户分别在松开/按下状态观察。
5. 临时板端文件删除、当前版本 ESP-IDF build→flash→monitor 和随后恢复 MicroPython
   均按具体动作单独确认；蜂鸣器瞬时电流专项按用户决定延期，不计入本轮完成声明。
6. Monitor 空错误竞态已用确定性合同修复；合入 test 后本地 `583 passed, 4 skipped`，
   main/test 新一轮 8 个远端 job 全部成功。个人 Marketplace 源新版通过 validator、
   `120 passed` 和 `48/12/12` 枚举，活动版本仍需用户重启确认。
7. 后续 main 文档 run 暴露高频计数测试的旧完成条件仍未从 test 回同步：实时
   `bytes_received` 可短暂领先 `persisted_bytes` 一条 4096 字节记录。现已复用 test
   的既有双计数等待和 stop 终态复核，不改变生产锁；独立进程 `30/30`、main
   `120 passed`。
