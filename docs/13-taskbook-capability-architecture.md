# 任务书 12 项能力与提示词架构

更新时间：2026-07-30（Asia/Shanghai）

## 总体结构

当前源码公开：

- 48 个 MCP tools；
- 12 个 MCP resources；
- 12 套公开 prompts；
- 既有任务书公开名 `debug_error`、`build_flash_monitor`、`review_hardware_context` 保持；
  其余旧 prompt 名称不注册。

12 套 prompt 不是额外叠加在旧 4 套之上，而是按任务书 6 项基础能力和 6 项提高能力重新组成最终公开集合。每套都包含目标、前置检查、工具顺序、成功证据、安全边界、失败处理和最终报告。

## 能力矩阵

| 类别 | 能力 | 公开 prompt | 主要 tools | 当前软件状态 | 证据与限制 |
| --- | --- | --- | --- | --- | --- |
| 基础 | 文件传输 | `file_transfer` | `esp_file_upload`、`esp_file_read`、`esp_file_list` | 已发布并通过实板相对路径复验 | `mpremote 1.28.0` 已安装在独立 Conda 环境；上传会修改板上文件。主机相对路径绑定活动 workspace，工作区外路径在后端调用前拒绝。 |
| 基础 | 程序执行和停止 | `program_execution_control` | `esp_exec_code`、`esp_run_file`、`esp_program_stop` | 已完成活动循环实板验收 | 停止只发送两次 Ctrl-C；本轮活动循环观察到 `KeyboardInterrupt` 与 `>>>`，`stop_confirmed=True`。它不发送 Ctrl-D 或复位命令，只证明 `reset_command_sent=false`，并明确 `physical_reset_excluded=false`。 |
| 基础 | 微控制器复位 | `microcontroller_reset` | `esp_reset`、串口采集工具 | soft reset 已完成本轮实板验收 | 本轮同一调用捕获 `MPY: soft reboot`、MicroPython banner 与 `>>>`；公共字段仍保留 `reset_confirmed=false`、`output_causality_confirmed=false`。 |
| 基础 | 串口监控 | `serial_monitor` | `esp_serial_capture`、4 个后台 Monitor 工具 | 已实现 | 支持游标、原始字节持久化、状态机、停止清理和断连报告；UART 不能替代物理外设观察。 |
| 基础 | 运行日志检索 | `runtime_log_search` | `esp_logs_latest/get/query` | 已实现 | SQLite runs/events 是正式查询源，JSONL 只是审计镜像。 |
| 基础 | MicroPython 错误报告 | `debug_error` | `esp_error_parse_text/log`、exec/capture/Monitor | 已发布并通过正式 error 实板复验 | 新版插件的受控 `ValueError` run 中，`esp_logs_get.errors` 从 authoritative schema-v3 SQLite 恰好返回一条，error ID 为 `5d63306a-a820-5282-9728-f95bee726015`；`esp_error_parse_log` 只使用 `sqlite_errors`，历史兼容来源仍保留用于旧日志。 |
| 提高 | 固件自动烧录 | `build_flash_monitor` | `esp_project_build`、`esp_flash_firmware` | 软件合同与 UART-only 实板闭环通过；正式发布待办 | 普通 build 不得隐式 set-target/fullclean；全部五种 plan 在读取 cache 前和启动前检查 build 路径。target/cache 冲突需 `confirm_target_change=True`；烧录需 `confirm=True`。本轮使用新鲜 4 MiB 备份，烧录目标区段后取得 READY/连续 HEARTBEAT `0..8`，再从地址 0 写回完整备份并复核 MicroPython/mpremote；未做恢复后全片回读。 |
| 提高 | 远程文件管理 | `remote_file_management` | `esp_file_list/read/upload/download/delete` | 上传/读取/列表/下载/删除均通过实板复验 | 面向 MicroPython；主机下载目标遵守 workspace 边界且不覆盖已有文件。板端四个临时路径在 `confirm=True` 和删除前目录复核后逐个删除，本次会话最终实时工具返回只列出 `/boot.py`。 |
| 提高 | GPIO 状态在线查询 | `gpio_status_query` | `esp_gpio_status` | GPIO34 与实体 KEY1 两态实板通过 | 只读明确 pins，不调用 `Pin.IN`、`Pin.OUT` 或 `init` 改模式；用户控制松开时为 `1`、按住时为 `0`，两次 `mode_changed=false`。这证明 active-low 两态，不证明去抖、长期可靠性或电气波形；进入 raw REPL 会中断当前程序。 |
| 提高 | 硬件信息自动采集 | `review_hardware_context` | `esp_hardware_info` | 已实现 | passive 只接受当前已枚举串口，并合并 host USB descriptor 与 reviewed mapping。可选 MicroPython runtime 模式需要 `allow_program_interrupt=true`；证据不扩大为“物理复位已排除”。 |
| 提高 | 自动化回归测试 | `automated_regression_test` | `esp_regression_test` | 正向与 negative 实板合同通过 | 受审 safe + GPIO34 正向为 2/0/0，negative 独立返回预期失败；未运行 GPIO32 stateful 或 GPIO25/蜂鸣器。GPIO34 回归脚本显式设置 `Pin.IN`，不能冒充严格不改模式查询。 |
| 提高 | 执行性能分析 | `performance_analysis` | `esp_performance_profile` | 无硬件副作用实板插桩通过 | 7/7 样本成功，使用 `time.ticks_us` 和 `gc.mem_free`；这是 instrumented wall time，不是 sampling profiler，也不表示功耗、供电或长期泄漏。 |

## 新增工具

本轮在原 43 个工具上新增 5 个正式工具：

1. `esp_program_stop`
2. `esp_gpio_status`
3. `esp_hardware_info`
4. `esp_regression_test`
5. `esp_performance_profile`

MicroPython 自动错误检测是在现有 exec、capture、Monitor 和 error parser 上形成闭环，不额外增加占位工具。

## 公开 prompt 列表

1. `file_transfer`
2. `program_execution_control`
3. `microcontroller_reset`
4. `serial_monitor`
5. `runtime_log_search`
6. `debug_error`
7. `build_flash_monitor`
8. `remote_file_management`
9. `gpio_status_query`
10. `review_hardware_context`
11. `automated_regression_test`
12. `performance_analysis`

只有上述 12 个名称注册为公开 prompt。`hardware_context_review`、`memory_write_policy` 和
`write_project_memory` 等其余旧名称不是隐藏别名，客户端应迁移到对应的新公开名称。

## 测试与实板边界

- 独立 Conda 解释器：`C:\Users\16224\anaconda3\envs\esp-mcp-toolchain\python.exe`。
- `mpremote 1.28.0` 已在该环境中通过模块版本和 CLI 版本核验；`.mcp.json` 经
  `scripts/run_mcp_server.py` 定位专用环境，定位失败时不会静默运行全局 Python。
- 相对路径修复前 15 个合同在旧 main 上全部失败；提交前 main 为 `119 passed in 17.64s`，test 显式加载 main 的完整门禁为 `243 passed in 31.50s`。
- 源码与 Marketplace 源均已核对为 `48 tools / 12 resources / 12 prompts`；
  用户重启后，当前 Codex 任务已从
  `0.1.0+codex.20260730084223` 安装路径加载 ESP skill 和 MCP tools。当前未提交
  确定性构建修复只有在本候选同时发布为 Codex 插件时，才需要进入下一版包并再次验收。
- 新硬件工具通过 fake serial、raw REPL 模拟和临时 SQLite/日志目录测试；测试不会访问真实开发板。
- 2026-07-30 已再次完成当前 4 MiB 备份、真实擦除、MicroPython v1.28.0 恢复，并用
  全新 21 字节载荷完成相对上传、读回和下载；目标实际落在 workspace，插件缓存无同名
  文件。随后活动程序停止、受控错误解析、GPIO34 查询、正向与 negative 回归、7 次性能
  插桩和 soft reset 均完成实板调用与 SQLite run 复核。
- 实板异常 run 暴露 exec/run-file 的结构化报告只写 complete event、没有进入正式
  `errors`。源码已为两项增加 `structured_error` completion artifact；旧实现红灯为
  `2 failed, 1 passed`，修复后相关合同与独立审查通过。新版插件重启后的
  `exec_code_20260730_150437_0d9c65aa` 已确认正式 error 恰好一条，解析来源仅为
  `sqlite_errors`。
- 无蜂鸣器闭环使用独立 `esp_idf_uart_smoke`，不复用启动即绑定 GPIO25/LEDC 的
  key/LED/buzzer 示例。首次 host build 的 176,896 字节
  `AA9E9AFA...09A9` 只保留为历史；本次实板接受的是关闭编译时间戳后的
  176,816 字节 app，SHA-256
  `4017628FA6BDFD2453C6518299F60D0ACF2A15BD3C43D466DE7CA8EF365D8CA2`。
  软件合同锁定唯一应用源、READY/HEARTBEAT 和十项 board defaults。
- 第二次闭环使用 SHA-256
  `F28649C0194A67C951E5DFCB8BC690B526ABD1CFDA50D94BE2027F5DCA66CE89`
  的新鲜 4 MiB 备份；目标区段烧录后，启动捕获包含 READY/HEARTBEAT `0,1`，
  7 秒固定捕获包含连续 HEARTBEAT `2..8`，随后从地址 0 写回完整 4 MiB 备份并恢复
  MicroPython Raw REPL/mpremote 文件访问；未做恢复后全片回读。临时文件按四个精确
  路径逐个删除，本次会话最终实时工具返回只列出 `/boot.py`。
- `esp_project_build` 的 target 预检现形成五种 plan；三种 destructive plan 在未确认时
  不启动 idf.py。SQLite completion 保留 plan、confirmed、command started、
  command completed、partial possible 和 target verified，不能把 planned、timeout 或
  子进程失败写成已完成清理。全部五种 plan 在读取 cache 前和 spawn 前检查 build 路径；
  该阶段定向 `39 passed in 2.30s`、main 全量 `120 passed in 60.59s`、test 显式加载
  当前 main `582 passed, 4 skipped in 297.08s`，独立复审 P0=0、P1=0。
- 当前未提交候选的最终本地门禁为 UART-only 专项 `5 passed in 0.30s`、main
  `120 passed in 61.67s`、test 跨工作树
  `583 passed, 4 skipped, 0 failed in 332.84s`。远端绿灯仍必须来自候选提交后的
  新 Actions。

## 发布封口

已完成的发布步骤与封口后的可选事项如下：

1. 既有功能提交、双分支 Actions、Marketplace
   `0.1.0+codex.20260730084223` 和用户重启验收均已完成。
2. KEY1 两态、板端临时文件删除和当前确定性 UART-only 严格实板闭环均已完成；
   它们不再是发布待办。
3. 当前工作树新增 `CONFIG_APP_COMPILE_TIME_DATE=n`、对应 test 合同和最终文档；
   最终本地全量已完成，内容仍处于 `[Unreleased]`，必须精确提交并取得新 Actions。
4. 仅发布 GitHub 仓库时无需更新 Marketplace。若同时发布 Codex 插件，才更新个人
   Marketplace 源并使用一次 cachebuster，不直接改安装缓存；用户重启后再核对版本和
   48/12/12。
5. 用户已确认蜂鸣器瞬时电流/掉电属于业务固件问题，不是 ESP MCP 工具链缺陷；本项目
   不再安排该专项，也不将其作为发布门禁。Release Notes 可记录此范围归属，但普通回归、
   UART 或性能结果仍不得扩展为蜂鸣器电气证据。
6. 版本号、Git tag 和正式 Release 由用户在
   `docs/15-release-readiness.md` 清单全部满足后决定。
