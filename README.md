# 通用 ESP MCP 工具链

本仓库用于开发一个通用型 ESP MCP 工具链，让 Codex / AI 编程助手可以通过 stdio MCP tools 操作本地 ESP 开发环境。

项目路线：

```text
Python CLI -> stdio MCP Server -> Codex / AI 编程助手调用
```

本仓库只做通用工具链，不绑定具体业务项目，例如数字钢琴、传感器项目、机器人项目等。业务固件代码应放在独立仓库或 `examples/` 之外的业务项目中。

## 项目边界

本仓库负责：

- 检测、选择和检查串口。
- 编译 ESP 项目。
- 烧录固件。
- 上传、下载、读取和列出板端文件。
- 复位开发板。
- 通过 REPL 执行短代码或运行文件。
- 捕获串口输出。
- 保存、读取和检索调试日志。
- 解析常见错误和 MicroPython Traceback。
- 管理硬件资料上下文，也就是 `hardwork/`。
- 管理项目内稳定事实记忆，也就是 `data/memory/`。
- 向 MCP 客户端暴露 tools、resources 和 prompts。

本仓库不负责：

- 编写具体业务固件。
- 替用户做业务架构决策。
- 暴露任意 shell 执行能力。
- 任意访问电脑文件系统。
- 在没有确认的情况下执行删除、擦除、烧录等高风险动作。

## 快速开始

克隆仓库：

```bash
git clone https://github.com/tjing8609-cyber/esp-mcp-toolchain.git
cd esp-mcp-toolchain
```

创建或更新 conda 环境：

```powershell
.\scripts\setup_env.ps1
conda activate esp-mcp-toolchain
```

运行 CLI：

```powershell
python toolchain/cli.py port-list
python toolchain/cli.py port-select COM3
python toolchain/cli.py port-status
python toolchain/cli.py logs-latest
python toolchain/cli.py hardwork-list
python toolchain/cli.py memory-search baudrate
```

运行 MCP stdio 入口：

```powershell
python toolchain/mcp_server.py
```

运行测试：

```powershell
python -m pytest
```

## 仓库结构

```text
esp-mcp-toolchain/
├── README.md
├── environment.yml
├── pyproject.toml
├── requirements.txt
├── .codex/
├── .codex-plugin/
├── .mcp.json
├── toolchain/
│   ├── cli.py
│   ├── mcp_server.py
│   ├── esp_mcp_toolchain/
│   │   ├── tools/
│   │   ├── resources/
│   │   ├── prompts/
│   │   ├── backends/
│   │   ├── database/
│   │   ├── store/
│   │   ├── hardwork/
│   │   └── memory/
│   └── tests/
├── hardwork/
│   ├── raw/
│   ├── processed/
│   └── index/
├── data/
│   ├── logs/
│   ├── memory/
│   └── artifacts/
├── skills/
├── docs/
├── scripts/
└── examples/
```

## 开发计划

### 第 1 阶段：Python CLI 可用

目标：先做命令行工具，确保核心动作可以在本机独立运行和调试。

计划实现：

- `esp_port_list`
- `esp_port_select`
- `esp_port_status`
- `esp_serial_capture`
- `esp_logs_latest`
- `esp_logs_get`
- `esp_logs_query`
- `esp_error_parse_text`

当前状态：已完成基础骨架和部分可运行实现。

### 第 2 阶段：stdio MCP Server 可用

目标：让 Codex / AI 编程助手能通过 MCP 生命周期调用工具链。

计划实现：

- `initialize`
- `tools/list`
- `tools/call`
- `resources/list`
- `resources/read`
- `prompts/list`
- `prompts/get`
- `shutdown`

当前状态：已改用官方 MCP Python SDK 的 `FastMCP` 和 stdio transport。入口仍为 `python toolchain/mcp_server.py`，协议解析、初始化、能力协商、tools/resources/prompts 路由由 SDK 接管。Codex 插件 manifest 已补齐展示元数据，并通过个人 marketplace 加载到本机 Codex 插件目录。

### 第 3 阶段：基础 ESP 调试闭环

目标：支持 ESP-IDF 和 MicroPython 的常见开发闭环。

ESP-IDF 方向：

- `esp_project_build`
- `esp_flash_firmware`
- `esp_reset`
- `esp_serial_capture`
- `esp_error_parse_log`

MicroPython 方向：

- `esp_file_upload`
- `esp_file_download`
- `esp_file_list`
- `esp_file_read`
- `esp_run_file`
- `esp_serial_capture`
- `esp_error_parse_log`

当前状态：ESP-IDF 和 MicroPython 基础调试闭环已进入可运行封装阶段，不再只是占位声明。`esp_project_build` 已封装本机 ESP-IDF 5.2.1 构建流程；`esp_backup_flash` 已封装整片 flash 备份；`esp_flash_firmware`、`esp_erase_flash`、`esp_project_clean`、`esp_file_delete` 已保留显式 `confirm=True` 高风险确认门并完成真实板卡验证；`esp_exec_code`、`esp_file_list`、`esp_file_read`、`esp_file_upload`、`esp_file_download` 和 `esp_reset` 已通过 MicroPython raw REPL 与 `mpremote` 在 `COM3` 上完成烟测；`esp_run_file` 已支持运行设备上已有的远程 `.py` 文件。仍保持占位或待增强的部分包括后台串口 monitor 和更多工程化查询能力。

### 第 4 阶段：hardwork 硬件资料上下文

目标：让模型在调试前可以读取板卡资料，避免凭空猜 GPIO、串口、烧录方式和硬件限制。

计划实现：

- `hardwork_list`
- `hardwork_get`
- `hardwork_set`
- `hardwork_search`
- 原理图、PCB、BOM、datasheet、串口说明文件的索引和摘要。

当前状态：已完成 processed 文档、JSON 索引、基础 list/get/set/search。

### 第 5 阶段：项目内 memory

目标：保存项目内稳定事实，供后续调试复用。

计划实现：

- `memory_write`
- `memory_read`
- `memory_search`
- `memory_update`
- `memory_delete`
- memory audit 冲突记录。

当前状态：已完成 JSONL 版本的写入、读取、搜索、更新、删除和冲突审计雏形。

### 第 6 阶段：SQLite 索引和日志库增强

目标：在 JSONL 稳定后升级为 SQLite 索引 + 原始日志文件的组合。

计划实现：

- `data/esp_mcp.sqlite`
- runs / events / raw_logs / errors 表。
- hardwork_items / hardwork_audit 表。
- memory_items / memory_audit 表。
- 日志导出和检索增强。

当前状态：已写入 `schema.sql` 和初始化脚本，仓库默认仍以 JSONL 作为第一版运行时存储。

## 当前进度

截至 2026-07-09，已完成：

- 仓库结构初始化。
- GitHub 远端同步，主分支为 `main`。
- Python 包结构和 CLI 入口。
- 官方 MCP Python SDK 接入，使用 `FastMCP` + stdio transport。
- tools / resources / prompts 注册骨架。
- 通过官方 MCP client 完成 stdio 连接烟测。
- 新增 conda 环境文件 `environment.yml`，环境名为 `esp-mcp-toolchain`。
- 串口枚举、串口选择、串口状态检查。
- 串口固定时长捕获的基础实现。
- JSONL 日志写入、读取、检索。
- MicroPython Traceback 文本解析。
- hardwork processed 文档和索引基础实现。
- memory JSONL 存储和 audit 基础实现。
- SQLite schema 初稿。
- Codex skill 文件和示例工作流。
- Codex 插件 manifest 补齐 `name`、`version`、`description`、`author`、`homepage`、`repository`、`license`、`keywords`、`skills`、`apps`、`mcpServers` 和 `interface`。`hooks.json` 已创建；`hooks` 未写入 `plugin.json`，因为当前插件验证器会拒绝该字段，优先保证插件可见和可验证。
- `.mcp.json` 改为 Codex 插件标准的 `mcpServers` 包裹结构。
- MCP resources 增加 `esp://tools/directory` 和 `esp://tools/registry`，用于让 Codex 读取 tools 目录和注册工具表。
- 未实现工具的占位返回结构已统一为可调用成功态，包含 `tool_name`、`tools名称` 和 `implemented: false`；已实现工具返回 `implemented: true` 并包含后端、端口、路径或执行输出等结构化字段。
- 本机个人 marketplace 已创建在 `C:\Users\16224\.agents\plugins\marketplace.json`，插件源已复制到 `C:\Users\16224\plugins\esp-mcp-toolchain`，并通过 `codex plugin add esp-mcp-toolchain@personal-plugins` 安装启用。Codex 安装缓存位于 `C:\Users\16224\.codex\plugins\cache\personal-plugins\esp-mcp-toolchain\0.1.0`。
- 初始测试集。

最近一次本地验证：

```text
conda 环境：esp-mcp-toolchain
Python：3.12.13
官方 MCP client 连接 toolchain/mcp_server.py 并执行 initialize/list
MCP 烟测结果：31 tools / 10 resources / 4 prompts
MCP tools/call 烟测：已实现工具返回 `implemented=true`，未实现分支仍返回名称占位字段
插件验证：源码目录和个人插件目录均通过本地 plugin validator
Codex 插件安装状态：插件源已同步到 `C:\Users\16224\plugins\esp-mcp-toolchain`；当前进程执行 `codex plugin add` 受 WindowsApps 权限限制
python -m pytest
```

测试结果：

```text
40 passed
```

开发日志（同一天按提交时间分开）：

### 2026-07-09 12:16 - 接入官方 MCP SDK

- 将 stdio MCP Server 切换到官方 MCP Python SDK，使用 `FastMCP` 和 stdio transport。
- 由 SDK 接管 MCP 初始化、能力协商、tools/resources/prompts 路由。
- 保留入口 `python toolchain/mcp_server.py`，便于 Codex 和本地客户端调用。

### 2026-07-09 14:02 - 补齐 Codex 插件可见性和 MCP 注册

- 补齐 `.codex-plugin/plugin.json` 中的 `name`、`version`、`description`、`author`、`homepage`、`repository`、`license`、`keywords`、`skills`、`apps`、`mcpServers` 和 `interface`。
- 创建 `hooks.json`，但暂不把 `hooks` 写入 `plugin.json`，因为当前插件验证器会拒绝该字段；优先保证插件可见和可验证。
- `.mcp.json` 改为 Codex 插件标准的 `mcpServers` 包裹结构。
- 新增 `esp://tools/directory` 和 `esp://tools/registry`，让 Codex 能读取 tools 目录和注册工具表。

### 2026-07-09 14:14 - 记录个人 marketplace 安装路径

- 本机个人 marketplace 位于 `C:\Users\16224\.agents\plugins\marketplace.json`。
- 插件源同步到 `C:\Users\16224\plugins\esp-mcp-toolchain`。
- Codex 安装缓存位于 `C:\Users\16224\.codex\plugins\cache\personal-plugins\esp-mcp-toolchain\...`。

### 2026-07-09 16:36 - 封装 ESP 工具后端并完成真实板卡验证

- `esp_exec_code` 已通过 MicroPython raw REPL 实现，并在 `COM3` 上完成烟测。
- `esp_file_list`、`esp_file_read`、`esp_file_upload`、`esp_file_download` 已通过 raw REPL 实现，真实板卡小探针文件测试通过。
- `esp_reset` 已实现 MicroPython 软复位，真实烟测捕捉到 `MPY: soft reboot` 和 MicroPython banner。
- `esp_project_build` 已封装本机 ESP-IDF 5.2.1 构建流程，`examples/esp_idf_key_led_buzzer` 可成功构建。
- `esp_flash_firmware`、`esp_project_clean`、`esp_file_delete`、`esp_erase_flash` 已加入显式 `confirm=True` 高风险确认门。
- `esp_logs_query` 已修复为多词匹配，可以跨 `message`、`data.raw_path` 等事件字段匹配，例如 `low_risk_probe COM3 Captured`。
- 已在 MicroPython 备份存在的前提下完成高风险验证：删除板上探针文件、clean 后重建、烧录 ESP-IDF 示例、整片擦除 flash、再恢复 `data/artifacts/flash/micropython_backup_20260709_151815.bin`。
- 恢复后通过 raw REPL 验证 MicroPython 正常响应 `restore_probe` / `final_restore_probe`。
- 当前真实板卡事实：`COM3` 枚举为 `USB-Enhanced-SERIAL CH9102`，芯片为 ESP32-D0WD-V3；GPIO32 LED 为低电平点亮，GPIO25 蜂鸣器可用 PWM 驱动，GPIO0 是 BOOT 按键。

### 2026-07-09 17:07 - 增加备份工具和 mpremote 工程化封装

- 新增 `esp_backup_flash`，使用 `esptool read_flash` 将整片 flash 备份到 `data/artifacts/flash/`；真实烟测读取 4MB 成功。
- 检测到当前项目 Python 环境缺少 `mpremote` 后，已安装 `mpremote 1.28.0`，并写入 `requirements.txt`、`environment.yml` 和 `pyproject.toml`。
- `esp_file_list`、`esp_file_read`、`esp_file_upload`、`esp_file_download`、`esp_file_delete` 已接入 `mpremote` 后端，raw REPL 后端保留为备用。
- `mpremote` 首次进入 raw REPL 失败时增加一次自动重试，解决板子刚复位后启动 banner 干扰的问题。
- `esp_run_file` 的远程文件运行分支已实现，使用 `mpremote exec "exec(open(remote).read())"` 运行设备上已有文件，而不是误用只支持本地脚本的 `mpremote run`。
- 真实板卡烟测完成：上传 `/codex_mpremote_probe.py`、读取、下载、运行并删除，远程运行输出 `mpremote_remote_probe`。

暂未完成：

- 后台串口 monitor。
- SQLite 仓储层落地。
- `esp_logs_query` 已支持多词匹配，后续还可以继续扩展时间范围、run_id 前缀、字段过滤等查询能力。
- 更多板卡和更多固件项目的端到端验证；当前真实验证覆盖 `COM3` 上的 ESP32-D0WD-V3 板、MicroPython 备份/恢复、ESP-IDF 示例 build/flash、整片擦除后恢复。

## 协作约定

- 新功能优先从 `toolchain/esp_mcp_toolchain/tools/` 增加工具入口。
- 与外部命令相关的实现放到 `toolchain/esp_mcp_toolchain/backends/`。
- 不要把任意 shell 执行能力暴露成 MCP tool。
- 高风险动作必须保留确认机制，包括烧录、擦除、删除和 full clean。
- 硬件原始资料放入 `hardwork/raw/`，工具只写 `hardwork/processed/` 和 `hardwork/index/`。
- 项目稳定事实写入 `memory` 时必须带 `source` 和 `confidence`。
- 项目环境使用 conda 虚拟环境 `esp-mcp-toolchain`，不在项目根目录创建 `.venv`，也不直接修改全局 Python 环境。
- 每次代码或文档变更后，都要更新 README 中的开发进程并推送到 GitHub。
- 提交信息要写明当次提交完成的工作和修改内容。
- 提交前运行 `python -m pytest`。

## 相关文档

- `docs/00-overview.md`
- `docs/01-mcp-lifecycle.md`
- `docs/02-tool-spec.md`
- `docs/05-hardwork-module.md`
- `docs/06-memory-module.md`
- `docs/07-database-design.md`
- `docs/10-development-roadmap.md`
