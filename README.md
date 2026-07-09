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

当前状态：已改用官方 MCP Python SDK 的 `FastMCP` 和 stdio transport。入口仍为 `python toolchain/mcp_server.py`，协议解析、初始化、能力协商、tools/resources/prompts 路由由 SDK 接管。

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

当前状态：相关工具接口已注册，后端实现仍是占位，暂不执行真实编译、烧录、文件传输或复位动作。

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
- 初始测试集。

最近一次本地验证：

```text
conda 环境：esp-mcp-toolchain
Python：3.12.13
官方 MCP client 连接 toolchain/mcp_server.py 并执行 initialize/list
MCP 烟测结果：30 tools / 8 resources / 4 prompts
python -m pytest
```

测试结果：

```text
12 passed
```

暂未完成：

- ESP-IDF `idf.py` 后端封装。
- `esptool.py` 烧录和擦除封装。
- `mpremote` 文件传输封装。
- raw REPL 执行封装。
- 后台串口 monitor。
- SQLite 仓储层落地。
- 针对真实 ESP 开发板的端到端验证。

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
