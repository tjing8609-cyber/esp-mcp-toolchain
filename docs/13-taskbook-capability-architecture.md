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
| 基础 | MicroPython 错误报告 | `debug_error` | `esp_error_parse_text/log`、exec/capture/Monitor | 实板解析通过；正式 error 投影修复待重载复验 | 受控 `ValueError` 的即时报告与兼容 event 解析正确；实板发现 exec/run-file 未 opt-in `structured_error`，源码与合同已修复。新版插件重载前不能把正式 `sqlite_errors` 来源记为通过。 |
| 提高 | 固件自动烧录 | `build_flash_monitor` | `esp_project_build`、`esp_flash_firmware` | 已实现 | 烧录必须有本轮明确授权并保持 `confirm=True` 门；烧录后监控需单独启动。 |
| 提高 | 远程文件管理 | `remote_file_management` | `esp_file_list/read/upload/download/delete` | 上传/读取/列表/下载已发布并通过实板复验；删除待确认 | 面向 MicroPython；主机下载目标遵守 workspace 边界且不覆盖已有文件，板端删除必须明确路径和 `confirm=True`。 |
| 提高 | GPIO 状态在线查询 | `gpio_status_query` | `esp_gpio_status` | GPIO34 实板查询通过 | 只读明确 pins，不调用 `Pin.IN`、`Pin.OUT` 或 `init` 改模式；本轮返回有效电平 0，但没有同步人工按键观察，不能证明实体 KEY1 动作。进入 raw REPL 会中断当前程序。 |
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
- 源码与已安装插件均已核对为 `48 tools / 12 resources / 12 prompts`；当前 Marketplace
  与运行缓存均为包含路径修复的 `0.1.0+codex.20260729114414`。
- 新硬件工具通过 fake serial、raw REPL 模拟和临时 SQLite/日志目录测试；测试不会访问真实开发板。
- 2026-07-30 已再次完成当前 4 MiB 备份、真实擦除、MicroPython v1.28.0 恢复，并用
  全新 21 字节载荷完成相对上传、读回和下载；目标实际落在 workspace，插件缓存无同名
  文件。随后活动程序停止、受控错误解析、GPIO34 查询、正向与 negative 回归、7 次性能
  插桩和 soft reset 均完成实板调用与 SQLite run 复核。
- 实板异常 run 暴露 exec/run-file 的结构化报告只写 complete event、没有进入正式
  `errors`。源码已为两项增加 `structured_error` completion artifact；旧实现红灯为
  `2 failed, 1 passed`，修复后相关合同与独立审查通过。当前安装缓存尚未包含这次修复，
  因此正式 `sqlite_errors` 来源仍待重启后实板复验。

## 发布边界

当前路径修复和剩余实板验收依次通过：

1. 提交 main 实现/文档和 test 合同，合并后运行完整 test 门禁。
2. 原子推送 main/test 并通过 GitHub Actions 四平台矩阵。
3. 通过 `plugin-creator` validator，只更新个人 Marketplace 源和一次 cachebuster，不直接改安装缓存。
4. 用户重启后核对 48/12/12，并用新输出名验证相对下载落在所选 workspace；已完成。
5. 在明确授权边界下完成其余 MicroPython 能力；板端删除和 ESP-IDF 烧录保持单独确认。
