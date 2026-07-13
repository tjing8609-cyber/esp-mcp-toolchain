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

## 测试与合入规则

- 测试文件统一维护在 `toolchain/tests/`，并由 `pyproject.toml` 中的 `testpaths` 自动发现。
- `test` 分支用于沉淀测试文件、测试目录和验证规则；新增功能进入主项目之前，必须先在 `test` 分支补齐或更新对应测试。
- 新增功能、修复和文档规则变更都必须通过全量测试：

```powershell
python -m pytest
```

- 全量测试未通过时，不得将新增功能合入主分支或发布到插件缓存。
- 涉及串口、GPIO、烧录、擦除、删除、full clean 等硬件或高风险动作时，必须先确认资料、端口和风险边界，再执行测试。

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

当前状态：ESP-IDF 和 MicroPython 基础调试闭环已进入可运行封装阶段，不再只是占位声明。`esp_project_build` 已封装本机 ESP-IDF 5.2.1 构建流程；`esp_backup_flash` 已封装整片 flash 备份，但最新 4 MiB 实板调用触发 MCP 300 秒超时，仍需补齐超时边界、进程清理和残缺文件处理；`esp_flash_firmware`、`esp_erase_flash`、`esp_project_clean`、`esp_file_delete` 已保留显式 `confirm=True` 高风险确认门并完成真实板卡验证；`esp_exec_code`、`esp_file_list`、`esp_file_read`、`esp_file_upload` 和 `esp_file_download` 已通过 MicroPython raw REPL 与 `mpremote` 在 `COM3` 上完成烟测；`esp_reset` 当前只实现 MicroPython `soft` 模式，ESP-IDF 可用的 `hard` 模式仍待实现；`esp_run_file` 已支持运行设备上已有的远程 `.py` 文件。仍保持占位或待增强的部分包括后台串口 monitor 和更多工程化查询能力。

### 第 4 阶段：hardwork 硬件资料上下文

目标：让模型在调试前可以读取板卡资料，避免凭空猜 GPIO、串口、烧录方式和硬件限制。

计划实现：

- `hardwork_list`
- `hardwork_get`
- `hardwork_set`
- `hardwork_search`
- 原理图、PCB、BOM、datasheet、串口说明文件的索引和摘要。

当前状态：已完成 processed 文档、JSON 索引、基础 list/get/set/search。

### 第 4.1 阶段：工程隔离、对话附件上传与硬件审查门禁

目标：让用户直接把原理图、PCB 图、BOM 或硬件说明附件贴入 Codex 对话框，由 Codex 调用 MCP 工具归档和整理；不同 Codex 工程的硬件资料、memory、日志、产物、数据库和串口选择必须隔离，硬件映射未完成前不得继续依赖 GPIO、串口、芯片或 flash 参数的操作。

计划实现：

- 建立项目上下文：以规范化后的 `workspace_root` 计算稳定 `project_id`，所有项目级工具必须显式绑定当前工程；缺少项目上下文时返回 `project_context_required`，不得回退到共享目录。
- 项目数据布局调整为 `data/projects/<project_id>/`，其下分别保存 `hardwork/raw/`、`hardwork/processed/`、`hardwork/index/`、`memory/`、`logs/`、`artifacts/`、数据库和项目元数据。
- 将串口选择、默认波特率和硬件审查状态纳入项目配置，避免不同工程互相继承端口或硬件结论。
- 新增 `hardwork_upload_attachment`：接收 Codex 对话附件对应的临时本地路径，校验路径、真实文件类型、扩展名和大小，计算 SHA-256 后复制到当前工程的 `hardwork/raw/`；用户不需要手动复制文件。
- 首批支持 PNG、JPEG 和 PDF。原始附件只读保留，不覆盖同名文件；相同内容按 SHA-256 去重，并记录来源、上传时间、资料类型和原始文件名。
- 第一次上传硬件资料后将当前工程标记为 `hardware_review_status=pending`，工具返回 `review_required=true`、附件路径、资料资源标识和必须完成的映射字段。
- 更新 MCP server instructions 和硬件审查 prompt，要求模型读取附件后调用 `hardwork_commit_mapping`。MCP 不能控制模型内部思考，但服务端状态机必须强制执行“上传 -> 阅读 -> 提交映射 -> 解锁硬件工具”的调用顺序。
- 新增 `hardwork_commit_mapping`：接收模型从附件中提取的结构化 GPIO、串口、板载外设、启动限制、复用功能、来源位置、置信度和待确认项。
- 首次映射只要求完成安全开发所需的基础初始化，不要求对大型原理图、PCB、BOM 和 datasheet 做一次性全量建档。
- 新增 `hardwork_mapping_patch`：模型在后续问答、查图或实板操作中发现新的稳定硬件事实后，必须在任务结束前增量回写；已有的无关映射不得被局部更新覆盖。
- GPIO 增量记录按 `gpio + function` 合并，串口按 `interface` 合并；相同事实可以补充来源或升级证据，关键字段冲突必须返回冲突列表并原子拒绝写入。
- 自动生成或更新 `gpio_map.md`、`serial_interface.md` 和 `hardware_mapping.json`，并同步 hardwork index/manifest。每条结论必须区分“原图确认”“实板测试确认”“模型推断”和“待确认”，不得把推断写成已确认事实。
- 增加硬件上下文门禁：映射未提交时，串口选择与串口操作、GPIO/板载外设操作、烧录、擦除和其他依赖芯片或 flash 参数的工具返回 `hardware_context_required`；hardwork 读取、附件读取和映射提交保持可用。
- SQLite 仓储层落地时，项目数据表必须包含 `project_id`，仓储查询强制按当前项目过滤，禁止无项目范围的全表读取。

未来迁移工具：

- `project_context_status`：显示当前 `workspace_root`、`project_id`、数据目录、审查状态和可迁移来源，不修改任何数据。
- `project_migrate_legacy_data`：把当前旧版共享 `hardwork/`、memory、日志、产物和配置迁入指定项目；默认只生成预览，实际迁移必须显式 `confirm=True`，保留来源清单和审计记录。
- `project_relocate`：工程目录移动或改名后，将旧 `workspace_root` 对应的数据绑定到新路径；必须验证旧项目标识，不自动猜测两个目录属于同一工程。
- `project_merge`：在用户明确指定源项目和目标项目后合并硬件资料或 memory；默认只预览冲突，实际合并必须显式确认，冲突项不得静默覆盖。
- `project_export` / `project_import`：以带 manifest 和 SHA-256 校验的归档包迁移项目上下文；导入前校验格式、版本和目标项目，默认不覆盖已有数据。
- `project_migration_verify`：迁移后检查文件数量、哈希、索引、SQLite project_id、映射资源和项目配置是否一致，并输出可审计报告。

测试与合入门槛：

- 增加两个或更多临时工程根目录的隔离测试，验证 hardwork、memory、日志、产物、SQLite 和串口配置不会串项目。
- 覆盖附件复制、临时路径失效、路径越界、伪造扩展名、大小限制、内容去重、同名不同内容和原始文件不覆盖。
- 覆盖首次上传进入待审查、未提交映射时门禁生效、提交映射后生成 Markdown/JSON 并解除门禁、后续资料上传不错误清空已确认结果。
- 覆盖旧数据迁移 dry-run、显式确认、冲突、回滚记录、工程改名重绑定、导入导出校验和跨项目合并预览。
- FastMCP 工具 schema、资源、prompt 和 stdio 握手必须通过测试；最终执行 `python -m pytest` 全量测试，通过后才允许合入主分支或更新个人插件缓存。

当前状态：项目上下文、项目级目录隔离、对话附件归档、首次基础映射、GPIO/串口增量回写和硬件工具门禁已经实现。Codex 必须先调用 `project_context_select(workspace_root)`；插件启动目录不能替代用户工程目录。hardwork、memory、日志、产物、SQLite 路径和串口配置均按 `project_id` 隔离。后续任务发现的新硬件事实通过 `hardwork_mapping_patch` 合并，旧版共享数据迁移、工程路径重绑定、项目合并、导入导出和迁移校验工具仍属于后续阶段。

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

截至 2026-07-13，已完成：

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
- 已创建 `test` 分支用于维护测试文件、测试目录和合入前验证规则；当前测试入口为 `toolchain/tests/`。

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

`main` 基线测试结果：

```text
47 passed
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

### 2026-07-09 21:25 - 建立 test 分支开发规范

- 从 `main` 创建 `test` 分支，用于维护测试文件、测试目录和合入前验证规则。
- 明确 `toolchain/tests/` 是当前全量测试目录，新增功能必须先补齐或更新测试。
- 新增 `docs/11-development-rules.md`，规定新增功能只有通过 `python -m pytest` 全量测试后，才可以合入主项目。

### 2026-07-11 22:02 - 实现工程隔离和硬件附件审查闭环

- 新增 `project_context_select` 和 `project_context_status`，使用规范化工作区路径和 SHA-256 生成稳定 `project_id`；未选择工程时，项目级工具和资源返回 `project_context_required`。
- hardwork、memory、日志、产物、SQLite 路径和串口配置迁入 `data/projects/<project_id>/` 的项目级目录，测试确认两个工程之间不可互读。
- 新增 `hardwork_upload_attachment` 和 `hardwork_attachment_list`，Codex 可把对话附件临时路径交给工具，由工具校验 PNG/JPEG/PDF 文件头、扩展名、大小和来源路径后归档到当前项目。
- 新增 `hardwork_commit_mapping`，生成 `gpio_map.md`、`serial_interface.md` 和 `hardware_mapping.json`，并要求每项结论记录证据类型、来源位置和置信度。
- 首次上传后进入 `hardware_review_status=pending`；映射提交前，依赖串口、GPIO、芯片或 flash 参数的工具返回 `hardware_context_required`，提交后解除门禁。
- 后续新增硬件附件只标记建议复核，不会错误清空已经确认的映射状态。
- `python -m pytest` 全量测试通过，共 `47 passed`；官方 MCP 客户端 stdio 烟测完成 initialize、36 个工具枚举、项目上下文选择和状态读取。

### 2026-07-11 22:37 - 增加硬件映射增量回写

- 保留首次上传后的有限基础初始化，不要求大型硬件资料一次性全量建档。
- 新增 `hardwork_mapping_patch` 和 `esp://hardwork/mapping`；后续问答、原理图复查或实板操作发现新事实时，模型必须先读取已有结构化映射，再增量回写缺失事实或更强证据。
- GPIO 以 `gpio + function`、串口以 `interface` 为稳定键合并，局部补充 LED、按键或蜂鸣器时保留已有 UART 等无关事实。
- 支持将 `schematic_confirmed` 升级为 `board_test_confirmed`；关键字段冲突返回 `hardware_mapping_conflict`，整次更新不写盘。
- `python -m pytest` 全量验证通过，共 `50 passed`。

### 2026-07-11 23:17 - 修复项目上下文跨 MCP 调用丢失

- 实机工具链验证发现 `project_context_select` 在单次调用中成功，但后续 MCP 请求因异步上下文隔离重新返回 `project_context_required`。
- 将活动项目选择从调用级 `ContextVar` 调整为带锁的 MCP 服务进程级状态，使“选择工程 -> 读取 hardwork -> 操作端口”的连续调用保持同一 `project_id`。
- 项目数据仍按 `project_id` 分目录隔离；当前版本要求每个工作流开始时重新选择并核验工程。同一 MCP 服务进程并发操作多个工作区尚不支持，后续需要为所有项目级工具增加显式 `project_id` 调用参数。

### 2026-07-11 23:47 - 修复 ESP-IDF 子进程卡死并更新五次外设示例

- 实际 MCP 构建测试发现 `idf.py` 子进程继承 MCP stdio 后可能长期等待，工具终止后还会遗留子进程；构建、fullclean 和 flash 共用的后端均受影响。
- ESP-IDF 子进程改为 `stdin=DEVNULL`，超时后终止完整进程树，避免占用构建目录或串口。
- 已配置目标为 ESP32 时只执行 `idf.py build`，不再每次重复 `set-target esp32 build`。
- `examples/esp_idf_key_led_buzzer` 更新为 KEY1 GPIO34 触发、GPIO32 LED 低有效、GPIO25 以 2 kHz LEDC PWM 间断鸣叫，共五次并等待按键释放。
- 修复后的后端在 ASCII 工作区真实构建成功，固件大小 `0x2ee90`，app 分区剩余 82%。

### 2026-07-12 00:25 - 补齐 Codex MCP 的 ESP-IDF Windows 平台环境

- 新后端不再卡死后，MCP 构建暴露 `idf_tools.py unknown platform`；同一命令在普通 PowerShell 中成功。
- 根因是 Codex MCP 精简环境缺少 Windows 的 `PROCESSOR_ARCHITECTURE`，导致 ESP-IDF 5.2.1 无法识别下载工具平台。
- `_build_env` 在 Windows 下补齐 `OS`、`SYSTEMROOT` 和与 Python 位数一致的 `PROCESSOR_ARCHITECTURE`，不修改全局环境。
- 后续 MCP 实测继续暴露精简环境缺少 `IDF_TOOLS_PATH` 和 `IDF_PYTHON_ENV_PATH`，导致 ESP-IDF 错误查找用户目录下不存在的 `.espressif` 环境；后端现根据本机已验证的 IDF 路径和 Python 环境路径为子进程补齐。

### 2026-07-12 12:38 - 增加 BIN 镜像恢复工具

- 新增高风险工具 `esp_restore_flash`，用于把本地 `.bin` 备份写回 ESP Flash，补齐 `esp_backup_flash` 只有读取、没有恢复的工具链缺口。
- 工具要求显式 `confirm=True`，校验输入文件位于当前项目、文件存在且非空，并在写入前计算 SHA-256；可通过 `expected_sha256` 阻止镜像哈希不匹配时写入。
- 恢复调用复用带 `stdin=DEVNULL` 和超时进程树清理的 esptool 子进程封装，返回输入路径、地址、字节数和 SHA-256。
- 真实硬件流程已完成 ESP-IDF 五次 KEY1/LED/PWM 蜂鸣器测试，并通过既有 4 MiB 镜像恢复 MicroPython v1.18、板上文件和本地程序运行。

### 2026-07-12 12:55 - 修复插件更新后的项目上下文丢失

- 实测发现默认运行时数据仍位于版本化插件缓存的 `data/projects/`，每次 cachebuster 安装后会造成 hardwork、memory、日志、串口配置和活动项目指针不可见。
- 默认数据根目录迁移到稳定的 `%USERPROFILE%/.codex/esp-mcp-toolchain/data/projects/`，不再随插件版本缓存变化；`ESP_MCP_DATA_ROOT` 覆盖行为保持不变。
- 选择项目时扫描源码目录和个人插件历史缓存中的同 `project_id` 数据，按“只复制缺失文件、不覆盖已有目标”规则迁移，并返回迁移来源和复制文件数。

### 2026-07-12 14:02 - 重新归档硬件资料并验证项目隔离

- 将 PCB 和原理图附件归档到当前工作区的独立项目上下文，生成 GPIO、串口和板卡摘要资料；英文工作区与旧中文路径工作区得到不同 `project_id`，未发生跨项目自动合并。
- 基础映射确认 KEY1 为 GPIO34、绿色 LED 为 GPIO32 低有效、蜂鸣器为 GPIO25 PWM、UART0 为 GPIO1/GPIO3。
- 实测发现 `hardwork_commit_mapping` 会接受缺少 `function` 或 `interface` 的条目，可能生成空白 Markdown，并使后续 `hardwork_mapping_patch` 无法建立稳定键；该输入校验缺口列入下一轮修复。

### 2026-07-12 14:14 - 完成插件实板闭环并记录稳定性缺口

- 在 ASCII 工作区通过 MCP 编译 `examples/esp_idf_key_led_buzzer`，固件大小 `0x2ee90`，app 分区剩余 82%。
- 重新枚举串口后确认 `COM3` 为 CH9102 USB 串口，使用 `esp_flash_firmware(confirm=True)` 写入 ESP32-D0WD-V3；bootloader、partition table 和 app 三个区段均通过哈希校验。
- 串口捕获完整记录 KEY1 触发后的五次 LED/PWM 蜂鸣器开关，以及 `sequence done` 和按键释放后重新就绪日志。
- 烧录前调用 `esp_backup_flash` 读取 4 MiB 时超过 MCP 300 秒调用上限，未生成可验证备份文件；本次不计为备份成功。
- `esp_reset(mode="hard")` 返回 `implemented=false`，确认当前只支持 MicroPython `soft` 复位；ESP-IDF 硬复位仍需查明可靠 DTR/RTS 时序后实现。

暂未完成：

- `hardwork_commit_mapping` 对 GPIO `function` 和串口 `interface` 的强制校验，以及 FastMCP 嵌套输入 schema 的明确字段约束。
- `esp_backup_flash` 的 MCP 超时边界、超时进程清理和残缺备份处理。
- `esp_reset(mode="hard")` 的 ESP-IDF/通用硬复位实现与实板验证。
- 旧版共享数据迁移、工程路径重绑定、项目合并、导入导出和迁移完整性校验工具。
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
- 项目级数据必须绑定明确的 `workspace_root` 和 `project_id`；缺少项目上下文时不得写入共享目录，也不得猜测项目归属。
- Codex 对话附件由模型把临时本地路径传给 `hardwork_upload_attachment`，工具负责校验并复制到当前项目，用户不需要手动整理插件目录。
- 硬件资料首次上传后，必须完成附件阅读和 GPIO/串口映射提交，才能解除硬件相关工具门禁。
- 后续任务从资料或实板操作中获得新的稳定硬件事实时，必须调用 `hardwork_mapping_patch` 增量回写；不能只在回答中展示而不更新映射。
- 工程迁移、合并、覆盖和重绑定属于高风险数据操作，默认只做预览，实际执行必须保留显式确认和审计记录。
- 项目稳定事实写入 `memory` 时必须带 `source` 和 `confidence`。
- 项目环境使用 conda 虚拟环境 `esp-mcp-toolchain`，不在项目根目录创建 `.venv`，也不直接修改全局 Python 环境。
- 新增功能必须先在 `test` 分支补齐或更新测试，再进入主项目开发流。
- 功能合入前必须运行 `python -m pytest` 全量测试；未通过时不得合入主分支。
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
- `docs/11-development-rules.md`
