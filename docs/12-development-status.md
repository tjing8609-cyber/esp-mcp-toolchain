# 当前开发状态

更新时间：2026-07-27 00:57（Asia/Shanghai）

## 当前分支

- 实现工作树：`index` / `main`。
- 测试工作树：`index-test` / `test`。
- 当前目标：完成任务书 6 项基础能力和 6 项提高能力，并形成 12 套 prompts + 48 个小工具的插件架构。
- 当前状态：启动器、串口生命周期、reset、Raw REPL/程序停止/错误检测和 12 套任务书能力已完成软件实现。P0 与 `erase_flash` P1 均已分别通过 main/test 共 8 个远端 job；P1 本地 main 全量为 `104 passed in 13.89s`，同步后 test 标准全量为 `228 passed in 28.76s`。个人 marketplace 源已更新并通过发布前验证，安装缓存重载和执行类实板验收尚未完成。

## 本轮已完成实现

- 在独立 Conda 环境安装并验证 `mpremote 1.28.0`；解释器为 `C:\Users\16224\anaconda3\envs\esp-mcp-toolchain\python.exe`，未修改全局 Python。`.mcp.json` 经 `scripts/run_mcp_server.py` 定位该环境，找不到专用解释器时不会静默使用全局 Python。
- 新增 `esp_program_stop`：只发送两次 Ctrl-C，不发送 Ctrl-D 或复位命令；只有观察到 `>>>` 才确认停止。结果只证明 `reset_command_sent=false`，并明确 `physical_reset_excluded=false`。
- MicroPython 异常检测形成闭环：
  - raw REPL stdout/stderr 自动解析；
  - 固定串口 capture 支持跨 chunk Traceback；
  - 后台 Monitor 暴露 `detected_error` 并只写一次结构化 SQLite 错误事件；
  - `esp_error_parse_log` 读取结构化报告和有界原始日志，路径强制限制在当前项目 logs 根目录。
- 新增四个提高工具：
  - `esp_gpio_status`：raw REPL 查询前要求 `allow_program_interrupt=true`；
  - `esp_hardware_info`：passive 只接受已枚举串口并合并 reviewed mapping，runtime 探测要求 `allow_program_interrupt=true`；
  - `esp_regression_test`：执行精确测试路径前要求 `confirm_execution=true`；
  - `esp_performance_profile`：重复执行前要求 `confirm_repeated_execution=true`。
- 工具注册表从 43 增加到 48；resources 保持 12。
- 公开 prompt 重组为任务书 12 套，每套包含目标、前置检查、工具顺序、成功证据、安全边界、失败处理和最终报告。
- 既有任务书公开名 `debug_error`、`build_flash_monitor`、`review_hardware_context` 保持；`hardware_context_review`、`memory_write_policy`、`write_project_memory` 等其余旧名称不注册。
- 日志完成 payload 增加异常、停止、回归和性能摘要字段，便于 SQLite 审计。
- 串口基础层统一采用打开前/后压低 DTR/RTS 的生命周期；reset 分离记录打开副作用、动作、输出和清理证据。
- Raw REPL 只在确认严格 `OK + stdout EOT + stderr EOT + >` 帧后报告完成，并验证源码、Ctrl-C 与退出写入没有短写。
- `erase_flash` 改为复用统一受管子进程执行器，显式固定 `--before default_reset --after hard_reset`；失败映射保留子进程输出和清理元数据，`confirm=True` 高风险门不变。

## 本地验证

- 测试工作树通过 `ESP_MCP_SOURCE_ROOT` 显式加载实现工作树源码。
- 提示词/提高工具/架构专项：`25 passed`。
- 串口生命周期、reset、Raw REPL、程序停止和错误检测关联门禁：`62 passed`。
- 显式加载 `main` 源码的完整候选门禁：`226 passed in 29.35s`。
- main→test 同步后，test 分支自身源码的标准全量门禁：`226 passed in 27.66s`。
- P0 main 本地全量：`104 passed in 14.70s`；main/test 共 8 个远端矩阵 job 全部成功。
- `erase_flash` P1 修复后：后端专项 `6 passed`、擦除工具专项 `8 passed`、main 全量 `104 passed in 13.89s`、跨工作树全量 `228 passed in 27.76s`。
- `erase_flash` P1 远端：[main run 30211040021](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30211040021) 与 [test run 30211040067](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30211040067) 共 8 个 job 全部成功。
- Marketplace 源直接枚举为 `48 tools / 12 resources / 12 prompts`；安装缓存枚举仍待用户重启和新任务验证。
- 测试使用 fake serial、raw REPL mock、临时项目目录和临时 SQLite；不访问真实开发板。

## 安全与实板状态

- 当前项目上下文为 `summer-holiday-1-2268049d8188`。
- 已审查的历史硬件映射仍为 ESP32-D0WD-V3、GPIO34 KEY1、GPIO32 低有效 LED、GPIO25 PWM 蜂鸣器、UART0 115200；本次纯软件步骤没有重新读取板端状态。
- 2026-07-26 的本轮门禁没有访问串口，也没有执行烧录、擦除、删除、复位、full clean 或驱动蜂鸣器。
- MicroPython 程序停止、GPIO raw REPL、板上回归和性能执行必须在明确的当前固件状态下单独验收，不能由 mock 测试或历史实板结果推断。

## 插件发布状态

- 当前仓库的既有本地插件 manifest 差异不属于本次提交；个人 marketplace 源已更新为 `0.1.0+codex.20260726165544`。
- Marketplace 源通过 plugin validator、main 发布测试 `104 passed in 14.19s` 和 `48 tools / 12 resources / 12 prompts` 直接枚举。
- 按用户约定，不直接改安装缓存；当前缓存仍为旧版 `0.1.0+codex.20260722153803`，由用户重启后再验证。本状态页不把 Marketplace 源枚举冒充为已安装插件枚举。

## 待完成

1. 用户重启后，在新任务核对安装插件版本和 48 tools / 12 resources / 12 prompts。
2. MicroPython 执行类专项只在明确当前固件和操作步骤后做实板门禁；擦除和烧录仍需按具体动作单独确认。
