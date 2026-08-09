# ESP MCP Toolchain（Codex 插件）

## 解决的问题

ESP 开发通常同时涉及串口、ESP-IDF、esptool、MicroPython、板端文件、运行日志和硬件资料。把这些能力直接交给 Codex 时，容易出现工具分散、项目混用、危险操作缺少确认、日志无法追溯以及软件结果被误当成实板结果等问题。

本项目集中处理以下问题：

| 问题 | 常见风险 | 本项目的处理方式 |
| --- | --- | --- |
| 开发入口分散 | Codex 需要临时拼接多个命令，参数和结果格式不一致 | 通过 stdio MCP Server 提供结构化 tools、resources 和 prompts，并保留 Python CLI 入口 |
| 多个 ESP 项目相互污染 | 串口选择、日志、硬件资料和项目记忆可能写入错误工程 | 使用 `project_context_select` 绑定实际业务工作区，并按工作区生成独立 `project_id` 与数据目录 |
| 高风险操作容易误触 | 烧录、擦除、恢复、清理、板端删除或 target 变更可能破坏现有状态 | 对高风险工具设置显式确认门；未确认时只返回风险和下一步，不执行目标操作 |
| 硬件事实缺少来源 | GPIO、串口和 Flash 参数可能来自猜测、旧资料或其他板卡 | 通过 hardwork 模块归档资料、维护映射并区分资料确认、实板确认、模型推断和未确认 |
| 调试过程不可追溯 | 只看到终端最后几行，无法还原执行顺序、原始串口数据和错误来源 | 使用 project-scoped SQLite 保存 runs、events、raw logs 和 errors，JSONL 保留为审计与迁移镜像 |
| 插件运行环境不稳定 | MCP Server 可能误用全局 Python，导致依赖版本不一致 | 使用独立 Conda 环境 `esp-mcp-toolchain`；启动器找不到专用解释器时直接报错，不静默回退 |
| 软件证据与硬件证据混淆 | pytest、返回值或 UART 文本容易被扩大解释为 LED、蜂鸣器或供电已经正常 | 工具输出明确记录证据边界，要求把软件合同、串口观测和人工实板观察分别说明 |

本项目定位是通用 ESP 开发工具链，不是某个数字钢琴、机器人或传感器项目的业务固件，也不是任意 Shell、任意串口写入或任意文件系统访问入口。它解决的是“如何让 Codex 在明确边界内操作和记录 ESP 开发流程”，而不是替代硬件设计、业务代码和人工验收。

## 项目的作用

本仓库把 Codex 插件清单、ESP skill、FastMCP stdio Server、Python CLI、执行后端和项目级数据存储组合为一套本地工具链。

基本调用链如下：

```text
Codex
  -> .codex-plugin/plugin.json
  -> skills/esp-mcp
  -> .mcp.json
  -> scripts/run_mcp_server.py
  -> 专用 Conda Python
  -> toolchain/mcp_server.py
  -> tools / resources / prompts
  -> 串口、Raw REPL、mpremote、ESP-IDF、esptool、SQLite 与项目文件
```

关键组成及职责：

| 组成 | 作用 |
| --- | --- |
| `.codex-plugin/plugin.json` | 声明插件名称、版本、skill、app 和 MCP Server 入口 |
| `skills/esp-mcp/` | 告诉 Codex 先选择项目上下文、先做低风险检查，并对危险操作要求确认 |
| `.mcp.json` | 声明 `esp_mcp_toolchain` stdio Server，由 `scripts/run_mcp_server.py` 启动 |
| `scripts/run_mcp_server.py` | 定位 `esp-mcp-toolchain` Conda 环境或 `ESP_MCP_PYTHON` 指定的解释器，再启动 MCP Server |
| `toolchain/mcp_server.py` | FastMCP stdio 服务入口 |
| `toolchain/cli.py` | 提供串口、日志、hardwork 和 memory 等基础命令行入口 |
| `toolchain/esp_mcp_toolchain/tools/` | 实现项目上下文、串口、构建烧录、MicroPython、日志及高级工具 |
| `toolchain/esp_mcp_toolchain/backends/` | 封装 pyserial、Raw REPL、mpremote、ESP-IDF 和 esptool 等执行后端 |
| `toolchain/esp_mcp_toolchain/database/` | 管理 SQLite schema、运行记录、事件、原始日志和错误 |
| `hardwork/` 与 hardwork tools | 保存硬件资料模板、已审查映射和未解决事项 |
| memory tools | 保存与当前项目绑定的稳定事实，避免不同工作区共用同一份记忆 |
| `docs/` | 保存工具规格、权限策略、架构、开发状态、发布记录和独立开发日志 |

[v0.1.0](https://github.com/tjing8609-cyber/esp-mcp-toolchain/releases/tag/v0.1.0) 的公开 MCP 能力面为 48 个 tools、12 个 resources 和 12 套 prompts。12 项主要能力如下：

| 类别 | 能力 | 作用 |
| --- | --- | --- |
| 基础 | 文件传输 | 在工作区边界内上传、读取和列出 MicroPython 板端文件 |
| 基础 | 程序执行和停止 | 通过 Raw REPL 执行代码或文件，并用受控 Ctrl-C 停止程序 |
| 基础 | 微控制器复位 | 执行 soft 或 hard reset，并记录串口输出与证据限制 |
| 基础 | 串口监控 | 支持固定时长采集以及可启动、读取、查询状态和停止的后台 Monitor |
| 基础 | 运行日志检索 | 按 run、时间、级别、阶段、工具和文本条件查询项目日志 |
| 基础 | MicroPython 错误报告 | 从文本、SQLite 事件和受限原始串口日志中识别并整理错误 |
| 提高 | 固件构建与烧录 | 构建 ESP-IDF 工程，并在明确确认后执行烧录、备份、擦除或恢复 |
| 提高 | 远程文件管理 | 列出、读取、上传、下载和受确认保护地删除板端文件 |
| 提高 | GPIO 状态查询 | 在明确允许中断程序后读取指定 GPIO，不把读值扩大为电气结论 |
| 提高 | 硬件信息采集 | 汇总主机枚举信息、已审查硬件映射和可选 MicroPython 运行时信息 |
| 提高 | 自动化回归 | 在确认执行范围后运行明确列出的板端回归文件 |
| 提高 | 性能分析 | 对确认过的 MicroPython 目标重复采样耗时和堆变化，不冒充功耗分析 |

项目数据按工作区隔离。默认目录为：

```text
%USERPROFILE%\.codex\esp-mcp-toolchain\data\projects\<project_id>
```

其中 SQLite 是 runs、events、raw logs 和 errors 的正式查询源；JSONL 用于审计、兼容与迁移；原始串口字节、构建产物、Flash 备份、hardwork 和 memory 分别保存在项目目录下的受控位置。可使用 `ESP_MCP_DATA_ROOT` 改变数据根目录，但改址前应先确认旧数据迁移和项目隔离影响。

## 快速启动

准备条件：

- 已安装 Git。
- 已安装 Anaconda 或 Miniconda；不要把依赖安装到全局 Python。
- `pyproject.toml` 要求 Python 3.10 或更高，仓库的 `environment.yml` 固定使用 Python 3.12。
- 只查看帮助、日志结构或串口列表时不需要连接开发板；执行板端操作时需要兼容的 ESP 设备和对应驱动。

1. 克隆仓库：

   ```powershell
   git clone https://github.com/tjing8609-cyber/esp-mcp-toolchain.git
   Set-Location .\esp-mcp-toolchain
   ```

2. 创建或更新独立 Conda 环境。

   Windows PowerShell：

   ```powershell
   .\scripts\setup_env.ps1
   conda activate esp-mcp-toolchain
   ```

   macOS 或 Linux：

   ```bash
   ./scripts/setup_env.sh
   conda activate esp-mcp-toolchain
   ```

   两个安装脚本都会执行 `conda env update -f environment.yml --prune`，环境名固定为 `esp-mcp-toolchain`。

3. 进行不写入开发板的基础检查：

   ```powershell
   python .\toolchain\cli.py --help
   python .\toolchain\cli.py port-list
   ```

   `port-list` 只枚举本机串口，不会自动选择串口、上传文件、复位、烧录或擦除。

4. 作为 Codex 插件使用。

   克隆仓库只获得源码，不等于插件已经安装。仓库已经包含 `.codex-plugin/plugin.json`、`skills/` 和 `.mcp.json`。在 Codex 中可让 `$plugin-creator` 检查这个现有目录并把它加入本地个人 Marketplace，提示词示例：

   ```text
   使用 $plugin-creator 检查当前 esp-mcp-toolchain 目录，
   将它作为现有本地插件加入个人 Marketplace；
   保留已有 plugin.json、skills 和 .mcp.json，不要重新生成或覆盖。
   ```

   完成本地 Marketplace 注册和安装后，刷新 Codex，并在新任务中加载插件。不要把“GitHub 仓库可以克隆”写成“粘贴 GitHub URL 即可完成插件安装”。

5. 首次使用先绑定实际业务工作区，再做只读检查：

   ```text
   调用 project_context_select 绑定我的实际 ESP 项目根目录，
   然后读取 project_context_status，并只列出可用串口；
   不要上传、执行、复位、烧录、擦除、清理或删除任何内容。
   ```

   这里的工作区应是你的业务工程目录，不是插件源码目录或 Codex 插件缓存目录。

6. 仅在源码调试时手工启动 MCP Server：

   ```powershell
   python .\scripts\run_mcp_server.py
   ```

   该命令启动的是 stdio 服务，会等待 MCP 客户端通过标准输入输出通信，不是交互式终端程序。正常通过 Codex 使用插件时，不需要另外打开终端长期运行它。

## 注意事项

- 必须先用 `project_context_select` 选择真实业务工作区，并用 `project_context_status` 核对。没有项目上下文时，项目级工具会拒绝执行。
- 不要把插件仓库、插件安装缓存或其他 ESP 工程误选为业务工作区；它们会生成不同的 `project_id` 和独立数据目录。
- 串口名称不是通用常量。先运行 `esp_port_list`，核对设备描述、VID/PID 和实际接线后，再用 `esp_port_select` 选择。
- 不要让 Thonny、其他串口监视器、ESP-IDF Monitor 或多个 MCP 进程同时占用同一串口。
- 涉及 GPIO、串口接口、Flash 布局、芯片型号和供电前，先读取并审查 hardwork 资料。资料确认、实板确认、模型推断和未确认必须分开记录。
- 烧录、擦除、恢复、清理、板端删除和 ESP-IDF target 变更必须针对当次设备、路径和参数单独确认。历史确认不能自动沿用。
- 文件上传、程序执行、停止、reset、GPIO 查询、回归和性能分析也可能中断当前程序或改变板端状态；即使某个工具没有统一的 `confirm` 参数，也不能把它当作无副作用操作。
- Flash 备份成功只证明读取到了指定范围的数据；恢复命令成功也不等于恢复后的整片 Flash 已与备份逐字节一致，除非另外完成全范围回读和哈希比较。
- Raw REPL、mpremote 或上传工具返回字节数成功，只证明传输层完成。需要时还应读取板端文件、比较哈希并复位观察启动输出。
- pytest 和 GitHub Actions 只能证明覆盖到的软件合同；UART 文本只能证明串口链路和固件输出。它们都不能单独证明 LED 真的发光、蜂鸣器可听、供电稳定或物理复位原因。
- `scripts/run_mcp_server.py` 找不到 `esp-mcp-toolchain` 专用 Conda Python 时会失败，不会静默使用全局 Python。必要时可用 `ESP_MCP_PYTHON` 指定已确认的解释器。
- 更改 `ESP_MCP_DATA_ROOT` 前要确认旧项目数据的位置和迁移方案，避免同一业务工程出现两套互不相通的日志、hardwork 和 memory。
- SQLite 是当前正式查询源，JSONL 是审计与迁移镜像；排查问题时不要只读取某一个 `latest.json` 就判断完整历史。
- 本插件不是任意 Shell 或任意文件系统代理。主机文件路径应受当前 workspace 边界约束，板端路径也应在调用前明确核对。
- 当前 GitHub 根目录的 `main.py` 和 `config.py` 是数字钢琴业务文件，不是插件入口，也不代表 ESP MCP Toolchain 只服务于数字钢琴；不要自动把它们上传到其他开发板。
- 本仓库可确认的是 GitHub 源码和 v0.1.0 Release。除非已核对当前 Codex Marketplace 状态，否则不要声称插件已经发布到公共插件目录。
- 用户可见版本变化记录在 [CHANGELOG.md](CHANGELOG.md)；实现与证据状态见 [docs/12-development-status.md](docs/12-development-status.md)；开发过程见 [docs/17-development-log.md](docs/17-development-log.md)。这些内容不再堆入 README。
