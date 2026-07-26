# 任务书 12 项能力与提示词架构

更新时间：2026-07-26（Asia/Shanghai）

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
| 基础 | 文件传输 | `file_transfer` | `esp_file_upload`、`esp_file_read`、`esp_file_list` | 已实现 | `mpremote 1.28.0` 已安装在独立 Conda 环境；上传会修改板上文件，必须使用明确路径。 |
| 基础 | 程序执行和停止 | `program_execution_control` | `esp_exec_code`、`esp_run_file`、`esp_program_stop` | 已实现 | 停止只发送两次 Ctrl-C；只有观察到 `>>>` 才返回 `stop_confirmed=True`。它不发送 Ctrl-D 或复位命令，只证明 `reset_command_sent=false`，并明确 `physical_reset_excluded=false`。 |
| 基础 | 微控制器复位 | `microcontroller_reset` | `esp_reset`、串口采集工具 | 已实现 | soft/hard 模式必须明确；启动成功仍需独立串口证据。 |
| 基础 | 串口监控 | `serial_monitor` | `esp_serial_capture`、4 个后台 Monitor 工具 | 已实现 | 支持游标、原始字节持久化、状态机、停止清理和断连报告；UART 不能替代物理外设观察。 |
| 基础 | 运行日志检索 | `runtime_log_search` | `esp_logs_latest/get/query` | 已实现 | SQLite runs/events 是正式查询源，JSONL 只是审计镜像。 |
| 基础 | MicroPython 错误报告 | `debug_error` | `esp_error_parse_text/log`、exec/capture/Monitor | 已实现 | 支持跨串口 chunk Traceback、raw REPL stderr、固定捕获和 Monitor；原始日志扫描限制在当前项目 logs 根目录与 `max_bytes`。 |
| 提高 | 固件自动烧录 | `build_flash_monitor` | `esp_project_build`、`esp_flash_firmware` | 已实现 | 烧录必须有本轮明确授权并保持 `confirm=True` 门；烧录后监控需单独启动。 |
| 提高 | 远程文件管理 | `remote_file_management` | `esp_file_list/read/upload/download/delete` | 已实现 | 面向 MicroPython；删除必须明确路径和 `confirm=True`。 |
| 提高 | GPIO 状态在线查询 | `gpio_status_query` | `esp_gpio_status` | 已实现 | 只读明确 pins，不调用 `Pin.IN`、`Pin.OUT` 或 `init` 改模式；进入 raw REPL 会中断当前程序，必须先给出 `allow_program_interrupt=true`。 |
| 提高 | 硬件信息自动采集 | `review_hardware_context` | `esp_hardware_info` | 已实现 | passive 只接受当前已枚举串口，并合并 host USB descriptor 与 reviewed mapping。可选 MicroPython runtime 模式需要 `allow_program_interrupt=true`；证据不扩大为“物理复位已排除”。 |
| 提高 | 自动化回归测试 | `automated_regression_test` | `esp_regression_test` | 已实现 | 只运行显式远程测试路径，最多 32 项；执行前必须给出 `confirm_execution=true`，并报告 passed/failed/skipped、stdout 和板上时长。 |
| 提高 | 执行性能分析 | `performance_analysis` | `esp_performance_profile` | 已实现 | 使用 `time.ticks_us` 和 `gc.mem_free` 插桩，最多 50 次；重复执行前必须给出 `confirm_repeated_execution=true` 并接受重复副作用。这是 instrumented wall time，不是 sampling profiler，也不表示功耗。 |

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
- 提示词/提高工具/架构专项：`25 passed`；串口生命周期、reset、Raw REPL、程序停止和错误检测关联门禁：`62 passed`；当前完整候选门禁：`226 passed in 29.35s`，均显式加载 `index` 主线源码。main→test 同步后的标准全量复跑仍待完成。
- 源码注册目标为 `48 tools / 12 resources / 12 prompts`；安装缓存仍待重装后在新任务枚举。
- 新硬件工具通过 fake serial、raw REPL 模拟和临时 SQLite/日志目录测试；测试不会访问真实开发板。
- 本次 2026-07-26 软件门禁没有重新读取或操作当前板卡；历史 COM3/CH9102 验收记录不能代替当前固件状态。MicroPython 程序停止、GPIO raw REPL、回归和性能执行类能力仍需独立实板验收。

## 发布边界

源码和测试完成后仍需依次通过：

1. 将 main 合并到 test，以 test 分支自身源码再次通过标准全量门禁。
2. 推送 main/test 并通过 GitHub Actions 矩阵。
3. 补齐 `erase_flash` 受管进程清理和复位参数的软件合同。
4. 通过 `plugin-creator` validator，只更新个人 marketplace 源和单一 cachebuster，由用户重启 Codex。
5. 在新 Codex 任务中核对 48/12/12 工具面。
6. MicroPython 执行类能力在明确当前固件和步骤后完成真实板卡验收。
