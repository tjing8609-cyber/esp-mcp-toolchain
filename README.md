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
- `esp_serial_monitor_start`
- `esp_serial_monitor_stop`
- `esp_serial_monitor_status`
- `esp_serial_monitor_read`
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

当前状态：已改用官方 MCP Python SDK 的 `FastMCP` 和 stdio transport。Codex 从 `.mcp.json` 启动标准库 bootstrap `scripts/run_mcp_server.py`，它严格定位 `esp-mcp-toolchain` Conda 解释器后再运行 `toolchain/mcp_server.py`；定位失败会明确退出，不静默使用全局 Python。协议解析、初始化、能力协商、tools/resources/prompts 路由由 SDK 接管。

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

历史状态（截至 2026-07-20）：ESP-IDF 和 MicroPython 基础调试闭环已进入可运行封装阶段，不再只是占位声明。`esp_project_build` 已封装本机 ESP-IDF 5.2.1 构建流程；`esp_backup_flash` 已接入统一子进程管理、超时清理、`.part` 原子写入和精确长度校验，4 MiB 实板备份已通过；`esp_flash_firmware`、`esp_erase_flash`、`esp_project_clean`、`esp_file_delete` 已保留显式 `confirm=True` 高风险确认门并完成当时板卡验证；`esp_exec_code`、`esp_file_list`、`esp_file_read`、`esp_file_upload` 和 `esp_file_download` 已通过 MicroPython raw REPL 与 `mpremote` 在当时的 `COM3` 上完成烟测；旧版 `esp_reset` 的 hard 模式曾捕获启动日志；`esp_run_file` 已支持运行设备上已有的远程 `.py` 文件。后台串口 Monitor 已完成当时的软件、CI、插件缓存和 `COM3` 门禁。2026-07-26 新增的 reset/Raw REPL 安全改动尚未重新实板验收；`erase_flash` 的受管进程树清理和显式复位参数已于 2026-07-27 通过本地及远端软件门禁，但真实擦除仍待验证，因此历史结果不能作为当前候选的实板结论。

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
- SQLite 的 runs/events 表包含 `project_id`，仓储查询强制按当前项目过滤，禁止无项目范围的全表读取；hardwork 和 memory 当前仍使用项目隔离的文件仓储。

未来迁移工具：

- `project_context_status`：显示当前 `workspace_root`、`project_id`、数据目录和迁移历史，不修改任何数据，也不自动猜测可迁移来源。
- `project_migrate_legacy_data`：已实现。调用者必须明确提供旧插件或仓库根目录；默认只生成带 SHA-256 和冲突分类的预览，`confirm=True` 后只复制缺失文件，相同文件跳过、不同文件不覆盖，并写入迁移审计和回滚清单。
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

当前状态：项目上下文、项目级目录隔离、对话附件归档、首次基础映射、GPIO/串口增量回写、硬件工具门禁和旧版共享数据显式迁移已经实现。Codex 必须先调用 `project_context_select(workspace_root)`；插件启动目录不能替代用户工程目录。hardwork、memory、日志、产物、SQLite 路径和串口配置均按 `project_id` 隔离。后续任务发现的新硬件事实通过 `hardwork_mapping_patch` 合并。工程路径重绑定、项目合并、导入导出和迁移校验工具仍属于后续阶段。

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

### 第 6 阶段：SQLite 正式日志库和检索增强

定位：SQLite 保存 runs/events 的正式状态并承担查询；JSONL 保留为审计镜像和旧会话迁移输入，串口原始字节继续使用分块文件。

本阶段范围：

- `<storage_root>/<project_id>/esp_mcp.sqlite`
- runs / events / raw_logs / errors 表。
- hardwork_items / hardwork_audit 表。
- memory_items / memory_audit 表。
- 日志导出和检索增强。

当前状态：SQLite 已在本地主线成为 runs/events 的正式状态与查询源，JSONL 保留为审计镜像。schema v3-A 已补齐 raw/error 约束、仓储和显式 v2→v3 迁移；v3-B2/B3 已完成固定 capture 与 Monitor 终态证据投影；v3-B4.1 已提供既有终态 event 的原子证据补投影，B4.2/B4.3 已提供纯只读历史 Monitor 与固定 capture resolver。v3-B4.4 已在源码和临时 schema-v3 数据库完成：持久 `historical_raw_claims`、事务内 profile/sequence 门禁、项目级 lease、两次解析、跨 run raw 所有权预检、Monitor run lease、独立 marker、幂等续跑和中断状态读取均已接通。正式项目数据库和当前安装插件仍保持 v2，尚未执行升级或正式历史补投影；v3-C raw/error 查询接入尚未开始。hardwork 和 memory 的当前运行时仓储仍使用原有文件实现。

## 当前进度

截至 2026-07-29，已完成：

- 仓库结构初始化。
- GitHub 远端同步，主分支为 `main`。
- Python 包结构和 CLI 入口。
- 官方 MCP Python SDK 接入，使用 `FastMCP` + stdio transport。
- tools / resources / prompts 注册骨架；当前源码面为 `48 tools / 12 resources / 12 prompts`。
- 按任务书形成 6 项基础 + 6 项提高共 12 套公开提示词，每套绑定明确工具顺序、成功证据、安全边界和失败处理；公开提示词数量严格为 12。
- 新增 `esp_program_stop`、`esp_gpio_status`、`esp_hardware_info`、`esp_regression_test`、`esp_performance_profile`，并把实时/持久日志异常检测接入执行、固定采集和后台 Monitor。
- 通过官方 MCP client 完成 stdio 连接烟测。
- 新增 conda 环境文件 `environment.yml`，环境名为 `esp-mcp-toolchain`。
- 串口枚举、串口选择、串口状态检查。
- 串口固定时长捕获的基础实现。
- JSONL 审计镜像写入和旧 session 迁移。
- MicroPython Traceback 文本解析。
- hardwork processed 文档和索引基础实现。
- memory JSONL 存储和 audit 基础实现。
- SQLite schema v3-A 与正式日志仓储：project-scoped runs/events/raw_logs/errors、复合外键、查询索引、事务 sequence、UUID/时间戳严格幂等、终态约束和显式可回滚迁移。
- SQLite v3-B2 原子 completion 证据投影：固定 capture 只登记当前项目 `logs/raw` 内经大小、普通文件、reparse 和实际 SHA-256 校验的正式文件；capture 的 result/structured error 与 `esp_program_stop` 的明确失败使用 completion UUID 区分 occurrence，并与 event、sequence 在同一事务提交。
- SQLite v3-B3 Monitor 终态产物对账：精确核对 manifest 与磁盘 chunk 集、原子登记 raw/error/run/event、计算确定性 bundle SHA-256，并以独立 `sqlite-artifacts-v1.json` sidecar、进程级 run lease 和描述符复核抵御重复恢复、并发恢复、ABA 与路径替换。
- SQLite v3-B4.1 历史证据补投影原语：只允许向既有终态 run 的最后一个 `complete` event 原子补入 raw/error；不创建 run/event、不改变 event 或 sequence，并对项目/run/event 归属、重试、并发和失败回滚执行严格合同。
- SQLite v3-B4.2 历史 Monitor resolver：只从当前项目 `logs/serial/<run_id>` 读取 manifest 和 finalized chunks；v1 旧绝对路径仅作跨平台词法校验，实际文件位置始终由当前 run 目录派生。resolver 使用单一安全 fd 计算 manifest 摘要，复核目录链、chunk 精确集合/连续编号/长度/SHA-256 和 B3 ownership，返回不可变的待投影证据，不获取 lease、不连接或写入 SQLite。
- SQLite v3-B4.3 历史固定 capture adapter：显式绑定安全的 session basename、run 和 event UUID，严格解析 legacy single-event，以及恰好由唯一 UUID 的 `prepare → complete` 构成且 task/source/port 元数据一致的 native JSONL；合法失败事件允许一致的 `selected_port` 为端口名或 `null` 且 completion payload 省略 port，成功事件仍必须提供一致端口。旧绝对 `raw_path` 只作 Windows/POSIX 词法校验，实际文件只由当前项目 `logs/raw/<basename>` 派生并在同一安全 fd 上计算长度/SHA-256。legacy source 或旧式文件名登记为 `serial_capture_legacy_text`，只有 native source 与 UUID 排他文件名同时成立才可声明 `serial_capture_raw`；legacy `phase=unknown` 候选明确标记为不具备 B4.1 投影资格。
- SQLite v3-B4.4 历史项目协调器：schema-v3 持久 claim 以 `(project_id, path)` 形成唯一所有权并通过复合外键绑定同一 run/event；协调器先以 SQLite URI `mode=ro` 拒绝非 v3 项目，再持项目级非阻塞 lease 扫描 B4.2/B4.3 候选。不同 `(run_id, event_uuid)` 共用 raw path 会在任何补投影前失败；capture 两次解析必须指纹一致，Monitor 在自己的 run lease 内再次解析并在释放前调用严格 B4.1。独立 `sqlite-historical-artifacts-v1.json` marker 记录 running/completed/failed，中断和“SQLite 已提交但 marker 发布失败”均可观测、可幂等修复。
- Codex skill 文件和示例工作流。
- Codex 插件 manifest 补齐 `name`、`version`、`description`、`author`、`homepage`、`repository`、`license`、`keywords`、`skills`、`apps`、`mcpServers` 和 `interface`。`hooks.json` 已创建；`hooks` 未写入 `plugin.json`，因为当前插件验证器会拒绝该字段，优先保证插件可见和可验证。
- `.mcp.json` 使用 Codex 插件标准的 `mcpServers` 结构，并通过 `scripts/run_mcp_server.py` 把实际服务固定到独立 Conda 环境。
- MCP resources 增加 `esp://tools/directory` 和 `esp://tools/registry`，用于让 Codex 读取 tools 目录和注册工具表。
- 未实现工具的占位返回结构已统一为可调用成功态，包含 `tool_name`、`tools名称` 和 `implemented: false`；已实现工具返回 `implemented: true` 并包含后端、端口、路径或执行输出等结构化字段。
- 历史记录（2026-07-20）：SQLite 曾同步到个人 marketplace 源版本 `0.1.0+codex.20260720110129`，当时 validator、源目录 `99 passed` 和 MCP `43 tools / 12 resources / 4 prompts` 枚举通过。当前会话加载的安装插件为 `0.1.0+codex.20260727064819`，工具面仍为 `48 tools / 12 resources / 12 prompts`；本工作树的新 B4.4 源码尚未同步到 Marketplace 或安装缓存。
- 初始测试集。
- 开发流程使用现有 `index` / `index-test` 双工作树：产品实现和文档提交到 `main`，`test` 分支的分支专属提交只维护测试文件和测试规则；本地门禁可从 `index-test` 显式加载 `index` 的主线源码。GitHub Actions 只检出被推送的单个分支，因此推送 test 前必须把固定、已验证的 main 合入 test，不能用本地跨工作树绿灯代替 test 分支自身的远端合同。当前测试入口为 `toolchain/tests/`。
- `project_migrate_legacy_data` 的测试契约已覆盖只读预览、显式确认、相同文件跳过、不同文件冲突不覆盖、非法来源拒绝、审计记录、审计写入失败回滚和 MCP schema。
- 已实现 `project_migrate_legacy_data`：支持只读预览、显式确认、SHA-256 比对、冲突不覆盖、复制或审计失败回滚和原子 JSONL 审计；不会递归迁移旧 `data/projects/`。
- 后台串口 Monitor 已完成：四个 MCP 工具、正式状态机、不可变项目绑定、游标读取、有界缓冲、原始字节分块日志、跨进程串口锁和退出清理均已有自动化测试。
- 历史实板记录（2026-07-13）：后台串口 Monitor 曾完成 `COM3` 启动、游标读取、停止清理和同端口重新打开验收；当时固件的 UART0 运行时控制台 `115200` 实测事实已写入项目硬件映射。
- 历史修复记录（2026-07-13）：后台串口 Monitor 针对 CH9102 稀疏输出改为非阻塞读取、先查询 `in_waiting`、单次最多 1024 字节并有界休眠；当时回归和真实按键门禁通过。
- 历史发布记录（2026-07-20）：SQLite 日志闭环曾接通本地主线、GitHub 和个人 marketplace 源。48/12/12 候选的 P0 与 `erase_flash` P1 均已分别取得 main/test 共 8 个远端成功 job；`0.1.0+codex.20260726165544` 安装缓存已完成重载和工具面核对。
- 2026-07-27 经明确授权完成 `COM3` 4 MiB 备份、整片擦除和官方 MicroPython v1.28.0 BIN 恢复；随后完成启动 banner、运行时硬件信息、20 条串口标记和三个验收文件的上传/读取/列表。相对下载误写安装缓存后立即暂停实板验收，未把远程文件管理及其余未执行能力记为通过。

最近一次本地验证：

```text
Conda 环境：esp-mcp-toolchain
Python：3.12.13
mpremote：1.28.0（仅在项目专属 Conda 环境中验证）
测试工作树：index-test / test
实现源码：index / main（由 ESP_MCP_SOURCE_ROOT 显式加载）
任务书提示词/提高工具/架构专项：25 passed
串口生命周期、reset、Raw REPL、程序停止和错误检测关联门禁：62 passed
相对路径修复前红灯合同：15 failed in 2.60s（test 加载 main@5985230）
相对路径修复后专项：main 32 passed in 4.15s；test 跨工作树 48 passed in 9.26s
相对路径修复后全量：main 119 passed in 17.64s；test 跨工作树 243 passed in 31.50s
SQLite v3-B2 红灯：11 failed, 1 passed
SQLite v3-B2 原子投影专项：15 passed in 1.61s
SQLite v3-B3 Monitor 终态产物专项：43 passed, 2 skipped
SQLite v3-B4.1 红灯复审：4 failed, 7 passed；最终专项：11 passed in 1.78s；SQLite 相关：146 passed, 2 skipped in 50.21s
SQLite v3-B4.2 resolver：初始红灯 42 failed（仅缺入口）；独立复审补强后 58 passed in 1.66s；既有 Monitor 回归 28 passed in 41.00s
SQLite v3-B4.3 historical capture adapter 合同：对 main `b3a4989` 的初始预期红灯为 40 failed, 1 skipped；独立复审再固定 legacy 现代文件名不得冒充精确 raw，以及 native 必须严格为唯一 UUID 的 `prepare → complete` 两条记录、固定 task/source、前后一致的端口元数据。第二轮复审补入合法失败事件可保持 `selected_port=COM3/None` 且省略 completion payload port、成功事件仍必须有一致端口的合同；当前加载 main 工作树实现为 50 passed, 1 skipped。skip 仅表示当前 Windows 账户不能创建 symlink 测试夹具；合同同时冻结当前项目安全重派生、B4.1 资格和零 SQLite/lease/marker 副作用。
SQLite v3-B4.3 capture adapter：初始红灯 40 failed, 1 skipped；独立复审补入精确 raw 来源绑定和 native 全记录身份合同后曾得到 6 failed, 40 passed, 1 skipped；第二轮复审的合法失败端口合同曾得到 2 failed, 48 passed, 1 skipped。全部修复后专项 50 passed, 1 skipped in 1.31s；main 全量 120 passed in 49.93s
B4.4 仓储基础合同：初始红灯为 5 failed, 11 passed in 1.94s；后续补强精确 event/run profile 形状、复合 event 所有权外键、B4.3 candidate→repository 直连和 Monitor 事件时间戳的有界容差。当前显式加载 main 工作树源码的 repository/Monitor/capture 三文件合同为 133 passed, 1 skipped in 5.56s；正式项目数据库仍为 schema v2，本记录不代表已执行正式迁移
B4.4 项目协调器合同：入口缺失时 9 failed；实现项目级非阻塞 lease、两次 resolver、跨 run raw 所有权预检、Monitor run lease、独立原子 marker、幂等续跑和提交后 marker 修复后为 9 passed。复审新增 Busy 可重试、损坏锁元数据可观测及跨 run 复用同 UUID 仍算两个所有者三项合同，先得到预期 3 failed, 9 passed，修复后 12 passed；B4.1-B4.4 组合为 145 passed, 1 skipped
当前软件全量：main 120 passed in 49.70s；合入固定 main 后，test 分支自身源码 534 passed, 4 skipped in 253.66s。该结果是本地软件与临时数据库证据，不是正式项目 v2 数据库升级或实板验收
MCP 源码枚举：48 tools / 12 resources / 12 prompts
覆盖：独立 Conda 启动器、安全串口生命周期、reset 因果证据、严格 Raw REPL 完整帧、短写处理、程序停止证据、跨 chunk/custom exception、SQLite event/raw/error 原子事务、Monitor 终态 chunk 精确产物集、并发 lease/ABA、旧 stale UUID 兼容、历史终态 event 既有行补投影、v1/v2 历史 Monitor 纯文件解析、镜像/sidecar 深度核验与原始日志受限扫描、12 套提示词、GPIO/运行时中断确认、回归执行确认、性能重复执行确认、真实 FastMCP Schema、项目隔离，以及主机相对路径不依赖 MCP 当前目录的合同
真实硬件：4 MiB 备份 SHA-256 为 `23F1A7424286FED0BA59A1E6883DB4195CDF344F696B628C314892B24585B6B9`；擦除 run `erase_flash_20260727_131837_f672becc` 成功；MicroPython v1.28.0 恢复 run `restore_flash_20260727_131918_88af58ec` 完成并通过写入哈希校验；启动 banner 和 runtime 探测成功；Monitor 收到 20 条有序标记且停止清理无丢失
历史样本只读验证：正式项目 22 个 v1 Monitor manifest 为 14 个 `resolved`、8 个 `no_artifacts`、0 个错误；4 个固定 capture 为 1 个 native `resolved`、3 个 legacy `ineligible`，全部是旧 writer 的 `serial_capture_legacy_text`。两次检查均未连接正式 SQLite，项目 189 个文件的路径、长度、mtime 和 SHA-256 前后无差异
当前 SQLite 边界：B4.4 已完成源码、临时 schema-v3 数据库和软件合同；B4.2/B4.3 resolver 仍是纯只读候选生成器，只有 B4.4 在项目/run lease 与严格仓储门禁内执行补投影。正式项目数据库经 `mode=ro` 核对仍为 schema v2，未启动协调器、迁移或正式历史补投影；当前 Marketplace/安装缓存也未包含本工作树改动，没有访问 COM3 或操作板卡
跳过边界：Windows 本地 3 项 skip 来自普通文件 symlink 创建权限（WinError 1314）及既有平台权限边界；目录 junction 与合成 fd/reparse 拒绝合同已执行。GitHub Linux 两套环境实际创建 symlink 并验证 fail-closed：恢复预检立即拒绝，不创建 sidecar、不写 SQLite、不改 manifest 或外部目标
未完成硬件门禁：程序停止、错误解析、GPIO34 只读、板上回归、性能分析、软复位、临时板端文件删除及日志闭环仍需按明确步骤继续；`build_flash_monitor` 只支持 ESP-IDF，不能用本次 Raw BIN 恢复冒充通过
远端与插件：B4.3 [main run 30356471000](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30356471000) 首次仅 Windows/Python 3.10 的既有 Monitor 跨进程用例在固定 8 秒 ready 窗口超时，另外 119 项及 3 个 job 成功；该用例本地独立进程重复 10/10 通过后，只重跑失败 job，run attempt 2 整体成功。[test run 30356899571](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30356899571) 的 4 个 job 首次全部成功。最终共核验 8 个 Windows/Linux、Python 3.10/3.12 job；没有修改范围外代码，也未更新 Marketplace 源或安装缓存
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

### 2026-07-13 12:31 - 修复硬件映射输入校验和 MCP schema

- 为 GPIO 和串口映射增加结构化 TypedDict：GPIO 条目强制要求 `gpio + function`，串口条目强制要求 `interface`，证据字段在 MCP schema 中公开固定枚举。
- `hardwork_commit_mapping` 在写入 Markdown、JSON 和硬件审查状态前验证稳定键，缺少必填字段时原子返回 `invalid_hardware_mapping`，不再生成空白映射。
- `hardwork_mapping_patch` 复用相同的结构化条目 schema；Codex 通过 FastMCP `tools/list` 可以直接看到嵌套字段、必填项和证据枚举。
- `test` 分支增加运行时拒绝和 MCP schema 回归测试；使用测试分支全量测试加载主线实现，执行 `python -m pytest` 得到 `63 passed`。
- 本地 FastMCP 枚举验证为 `38 tools / 12 resources / 4 prompts`。

### 2026-07-13 12:48 - 稳定 Flash 备份并实现硬复位

- `esp_backup_flash` 改用 ESP-IDF 共用的受管子进程封装，子进程不再继承 MCP stdin，超时后会终止进程树。
- 备份先写入同名 `.part` 文件；失败、超时或长度不符时删除残片并保留已有目标文件，只有字节数与请求值完全一致时才原子替换正式 BIN。
- `COM3` 实板读取 4 MiB 成功，耗时约 100.7 秒，输出 4,194,304 字节，未残留 `.part`；备份 SHA-256 为 `12954cd7873a90e1e9c501ef0b9da7e730c434ca4f70781ccdcf733428895a3a`。
- `esp_reset(mode="hard")` 使用 DTR 保持 GPIO0 高电平、RTS 低脉冲复位 EN，并捕获两秒启动输出；实板日志包含 `POWERON_RESET`、项目 `esp_idf_key_led_buzzer` 和 `ready`。
- MCP stdio 烟测确认仍为 38 个工具，`esp_reset.mode` schema 枚举为 `soft`、`hard`；`test` 分支完整测试集加载主线实现得到 `68 passed`。
- 原理图初始 DTR/RTS 解释与实板成功时序存在冲突，已作为单独的 `board_test_confirmed` 串口映射和待复核项写入当前工程硬件资料，暂不凭推测覆盖原始记录。

### 2026-07-13 13:05 - 建立旧版共享数据迁移测试契约

- `test` 分支新增 `project_migrate_legacy_data` 测试，限定来源必须由调用者明确提供，默认只读预览，`confirm=True` 才能写入当前项目隔离目录。
- 迁移范围限定为旧版 `hardwork/`、memory、日志、产物、项目配置和 SQLite；明确排除旧 `data/projects/`，避免把其他工程数据递归混入当前工程。
- 测试要求缺失文件可复制、相同文件跳过、不同文件报告冲突且不覆盖，并为实际复制内容写入可审计的回滚清单。
- 当前基线执行得到 `4 failed`，失败原因均为工具和 MCP 注册尚未实现；下一步只在 `main` 编写产品代码。

### 2026-07-13 13:11 - 补充迁移审计失败回滚测试

- 新增审计日志写入失败场景，要求已经复制到目标项目的本次新增文件全部删除，并返回 `legacy_migration_failed` 和 `rolled_back`。
- 回滚只允许删除本次独占创建的文件，不得触碰迁移前已经存在的文件；测试继续使用 pytest 临时目录。
- 当前迁移契约基线更新为 `5 failed`；下一步在 `main` 将审计写入纳入同一失败回滚边界。

### 2026-07-13 13:14 - 实现旧版共享数据显式迁移

- 新增 `project_migrate_legacy_data(source_root, confirm=False)`；来源目录必须由调用者明确给出，工具不会自动猜测两个工程或目录属于同一项目。
- 默认 dry-run 只计算文件数量、字节数、SHA-256、目标路径和 `copy` / `identical` / `conflict` 分类，不创建迁移文件或审计记录。
- `confirm=True` 时只迁移旧版 `hardwork/`、memory、日志、产物、项目配置和 SQLite；旧 `data/projects/` 明确排除，避免把其他项目递归混入当前项目。
- 复制使用独占创建，已有相同文件跳过、已有不同文件报告冲突且不覆盖；复制或审计写入失败时删除本次已复制文件并返回 `rolled_back`。
- 成功执行以临时文件和原子替换方式更新 `migration_audit.jsonl`，记录来源、目标项目、统计、冲突和带 SHA-256 的回滚清单，不留下半行 JSON。
- `test` 分支全量测试加载主线实现得到 `73 passed`；MCP stdio 烟测枚举 39 个工具，临时目录中的 dry-run 与 synthetic `confirm=True` 迁移、内容校验和审计生成均通过。

### 2026-07-13 16:52 - 实现后台串口 Monitor 候选

- 新增 `esp_serial_monitor_start`、`esp_serial_monitor_stop`、`esp_serial_monitor_status` 和 `esp_serial_monitor_read`，读取接口使用单调递增 `seq`、`after_seq` 游标、有界等待和最大返回字节数。
- Monitor 启动时固定 `project_id`、工作区和日志目录；后续切换当前项目不会改变已有会话的写入目标。
- 会话使用 `STARTING`、`RUNNING`、`STOPPING`、`STOPPED`、`FAILED`、`DISCONNECTED` 状态机；原始字节分块落盘并支持 text、base64 和 both 表示。
- 增加进程内与跨进程串口锁、进程所有权和端口身份记录、陈旧锁恢复，以及 stdin EOF、正常 shutdown、可捕获信号、`atexit` 和内部异常的有界清理；恢复前会确认原所有进程已经结束，强制终止仍依赖操作系统释放句柄和下次启动恢复。
- 新增真实 MCP 子进程测试，验证关闭 stdin 后限时退出、串口可重新打开且没有陈旧锁；同时覆盖强制终止恢复、跨进程冲突、断连、磁盘故障、配额、UTF-8 分片和二进制日志。
- 功能分支当前为 `43 tools / 12 resources / 4 prompts`，全量测试 `99 passed`，Monitor 专项 `27 passed`。
- 当次只枚举到 `COM6`、`COM7` 两个蓝牙串口，没有检测到 ESP USB 串口，因此未打开任何端口；真实板卡验收留待板卡重新连接后完成。
- 协作流程改为功能分支同时维护代码、测试和文档，并增加 CI、CHANGELOG、开发状态页和 ADR；历史 `test` 分支保留但停止承载新开发。

### 2026-07-13 17:36 - 完成 Monitor 真实板卡门禁

- Codex 重启后确认插件缓存版本为 `0.1.0+codex.20260713091610`，四个 Monitor 工具在当前模型工具面中可见并可调用。
- 重新枚举并按 VID/PID、序列号和 location 确认 `COM3` 为 `USB-Enhanced-SERIAL CH9102`；`COM6`、`COM7` 仍是蓝牙串口，未打开。
- `monitor_20260713_173011_acd850be` 在 `115200` 捕获两条 ESP-IDF 启动记录，共 7,306 字节；日志包含 `POWERON_RESET`、项目名 `esp_idf_key_led_buzzer` 和 GPIO34/32/25 ready 信息。
- 使用返回游标再次读取时没有重复旧记录，且没有缓冲丢弃；停止后 7,306 字节全部落盘、worker 退出、清理完成。
- 随后以 `monitor_20260713_173300_7c343c91` 立即重新打开并停止同一 `COM3`，确认串口句柄和锁已释放。
- 通过 `hardwork_mapping_patch` 将“当前 ESP-IDF 固件的 UART0 运行时控制台为 `115200`”作为 `board_test_confirmed` 事实写入项目映射；基础板级默认波特率仍保留待确认语义。
- GitHub Actions 已确认实现提交 `60a3a83` 在 Windows/Linux、Python 3.10/3.12 四个组合全部成功；另有 4 条 Actions 运行时 Node.js 20 弃用警告，不影响本次测试结论。

### 2026-07-13 18:00 - 合入后台串口 Monitor 至 main

- 用户审核后批准合入；`origin/main` 是 `feature/serial-monitor` 的直接祖先，合并前没有分叉或冲突。
- 合入范围包含 Monitor 实现、配套文档、CI，以及此前隔离在历史 `test` 分支但按新规范应与实现共同进入主线的回归测试。
- 功能分支头 `962a382` 已通过本地 `99 passed`、插件 validator、MCP 枚举、四平台 CI 和 `COM3` 真实板卡门禁。
- 合入提交已推送到 `origin/main`；本任务结束后暂停，不自动开始 SQLite、日志查询增强或迁移体系。

### 2026-07-13 18:05 - 确认 main 合入 CI

- `main` 合入提交 `e67dd7f` 的 GitHub Actions 状态为 `Success`，Windows/Linux、Python 3.10/3.12 四个任务全部完成，用时 1 分 28 秒。
- CI 有 4 条 Actions 运行时 Node.js 20 弃用警告，不是测试失败；后续可单独升级 `actions/checkout` 和 `actions/setup-python`，本次不扩大修改范围。
- Monitor 合入流程已完成；本地 `main`、远端 `origin/main` 和主分支 CI 均已核实。

### 2026-07-13 21:57 - 修复 CH9102 Monitor 污染读取

- 全量实板复测发现 `monitor_20260713_194149_b9df143f` 出现一条非法 UTF-8 记录，重复测试 `monitor_20260713_194522_9bbed9a6` 又在约 3 秒内记录 215,952 字节旧片段和污染数据；该吞吐明显超过 `115200 8N1` 的有效载荷能力，原门禁结论失效。
- 故障触发边界定位到 Windows CH9102 / pyserial 稀疏流量与固定超时 `read(4096)` 的组合，但不把驱动内部原因表述为已经证明；代码改用非阻塞串口、`in_waiting` 实际待收长度、1024 字节单次上限和 5 ms 空闲等待。
- `test` 分支新增“只读实际待收字节”和“限制单次读取量”两条回归；两条测试在修复前稳定失败，修复后通过。`index-test` 测试加载 `index` 源码执行全量门禁得到 `101 passed`，Monitor 专项为 `29 passed`。
- 自动复位门禁 `monitor_20260713_201602_a7c32955` 捕获 3,653 字节启动日志，62 条记录均无解码错误，单条最大 68 字节，停止后全部持久化并释放串口。
- 最终按键门禁 `monitor_20260713_215648_f1366541` 捕获两次真实按键序列；每次均包含 5 组 LED/蜂鸣器 on/off、结束和释放日志。共 1,466 字节、41 条记录，`decode_error=0`、替换字符为 0、无丢弃或未持久化字节，停止清理完整。
- 本次只执行串口读取和一次硬复位，没有烧录、擦除、清理或修改开发板固件。

### 2026-07-13 22:10 - 推送修复并同步 marketplace 源

- `main` 实现与文档提交 `409d0ec`、`test` 回归提交 `75a5c84` 已推送到 GitHub；测试分支同步主线后再次执行常规 `python -m pytest`，结果为 `101 passed`。
- 已把 `main` 同步到个人 marketplace 源 `C:\Users\16224\plugins\esp-mcp-toolchain`；源版本为 `0.1.0+codex.20260713135819`，plugin validator 通过，Monitor 后端哈希与仓库一致，从该源直接枚举得到 `43 tools / 12 resources / 4 prompts`。
- 当前 Codex 桌面执行上下文运行安装包内 `codex plugin add` 时被 Windows 返回 `Access is denied`，因此没有伪造或手工覆盖插件缓存；需要在可执行 CLI 的终端重新安装并重启 Codex 后完成当前模型工具面验收。

### 2026-07-13 22:14 - 补齐 test 分支远端门禁

- GitHub Actions 原配置只监听 `main` 和 `feature/**`，与恢复后的双工作树规则不一致；现已把 `test` 加入 push 触发分支，后续主线实现和测试分支都必须通过 Windows/Linux、Python 3.10/3.12 矩阵。

### 2026-07-13 22:34 - 完成新版插件重载和当前模型实板验收

- Codex 重启后确认安装缓存 `0.1.0+codex.20260713135819` 已生成，缓存中的 Monitor 后端 SHA-256 与 `main` 和 marketplace 源一致；当前模型能够直接调用新版项目上下文、串口枚举和 Monitor 工具。
- 最终人工确认门禁 `monitor_20260713_223126_87fc393e` 在 `COM3`、`115200` 捕获一次完整按键序列：5 组 LED/蜂鸣器 on/off、`sequence done` 和按键释放；用户同时确认 LED 与蜂鸣器实物动作正常。
- 该会话共接收并持久化 733 字节、24 条有序分片，所有记录 `decode_error=false`，没有缓冲丢弃或未持久化字节；停止后为 `STOPPED`、`worker_alive=false`、`cleanup_complete=true`。
- 随后通过 `monitor_20260713_223337_a7fc4184` 再次打开并停止同一串口，`COM3` 最终为 available 且 not busy。协调按键时间时产生的较早会话不作为最终人工门禁结论。
- 本轮没有烧录、擦除、清理、删除或修改开发板固件。

### 2026-07-20 16:20 - 落地 SQLite 正式日志仓储

- 按审核后的字段合同完成 schema v2：runs/events 使用 project-scoped 复合键、复合外键、JSON 对象约束、规范 UUID 和常用查询索引。
- SQLite 成为 `esp_logs_latest/get/query` 的正式查询源；JSONL 保留为审计镜像，旧 session 文件使用稳定快照、SHA-256 marker 和 UUIDv5 可重复导入。
- run 状态机限制终态改写和终态新事件；sequence 在 `BEGIN IMMEDIATE` 事务中分配。v1 的 hardwork/memory 表会真正重建为复合主键，而不是只追加 project_id 列。
- 同步工具接入统一 start/prepare/complete/finish 生命周期；后台 Monitor 固化完整 LogScope，并在 worker 终止时写入 failed 或 cancelled。
- 动作或状态变更完成后若日志收尾失败，返回真实业务结果、`logging_persisted=false` 和 `logging_warning`，不会把已经成功的烧录、擦除、删除、端口选择或配置写入误报为失败。
- 最终审查补齐镜像故障容错、Monitor 异常收尾与 stale 对账、native run 导入隔离和默认端口冻结；6 条新增契约全部通过。
- `test` 分支新增跨工作树来源门禁和 SQLite 契约；定向 `33 passed`，跨工作树全量 `134 passed`。本轮未执行任何真实硬件动作。
- 当前项目 19 份旧 JSONL 已在临时数据库完成迁移演练，共导入 32 个事件，外键检查为空；正式项目数据库尚未写入。

暂未完成：

- 本次 SQLite 实现和测试尚未提交、推送，也尚未取得新的 GitHub Actions 结果。
- 当前项目数据目录尚未正式创建 SQLite；迁移后仍需重复导入核对幂等、状态分布和外键。
- 个人 marketplace 源和 Codex 安装缓存仍是上一个 Monitor 版本，尚未包含 SQLite 改动。
- 日志导出、聚合和 run_id 前缀等后续增强。
- 工程路径重绑定、项目合并、导入导出和迁移完整性校验工具；该体系继续暂停。
- 更多板卡和固件项目的端到端验证；当前既有真实验证范围不因本次纯存储改动扩大。

下一步计划：

- 提交 `main` 实现/文档和 `test` 契约测试，同步分支后再次运行全量门禁。
- 推送 GitHub 并确认 Windows/Linux、Python 3.10/3.12 矩阵。
- 正式迁移当前项目旧日志并核对重复导入结果。
- 同步 marketplace 源、重新加载插件并核对 SQLite schema/查询工具；完成后暂停，不自动开始项目迁移体系。

### 2026-07-20 19:05 - 完成本地提交、正式迁移和 marketplace 源同步

- `main` SQLite 实现提交为 `08bce0b`；`test` 契约提交为 `c59b509`，同步主线合并为 `9bbd265`。最终跨工作树门禁为 `134 passed in 21.44s`。
- 当前项目已创建 `esp_mcp.sqlite` schema v2。首轮导入 19 份旧 JSONL、32 个事件；第二轮为 0 / 0 / 0，状态分布为 12 cancelled、2 failed、5 succeeded，19 个 marker，外键检查为空。
- 插件 cache-buster 更新为 `0.1.0+codex.20260720110129`；个人 marketplace 源只做受控文件覆盖，不删除额外文件。源目录 validator、`99 passed` 和 `43 tools / 12 resources / 4 prompts` 枚举通过，`esp_logs_query` 已暴露全部结构化过滤字段。
- marketplace 源的 `data` 仍为 56 个文件、8,408,399 字节，根 `hardwork` 仍为 13 个文件；未追踪运行数据没有被删除，受控 hardwork 内容也未改变。
- 当前 Codex 任务不会热加载新插件缓存；需通过 Plugin Management 重载后在新任务核对。GitHub 仓库为公开仓库，推送仍等待明确的公开上传授权。
- 本轮没有烧录、擦除、删除、full clean 或其他真实硬件动作。

### 2026-07-20 19:19 - 完成 GitHub 同步和远端矩阵

- 获得公开上传授权后，使用原子推送将 `main` 的 `8c7aad7` 和 `test` 的 `78951a4` 同步到 GitHub；推送后两工作树分别为 `main...origin/main`、`test...origin/test`，均无未提交改动。
- `main` push workflow [#12](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/29738048371) 和 `test` push workflow [#11](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/29738048362) 均为 `completed / success`。
- Windows/Linux、Python 3.10/3.12 共 8 个 job 全部成功；每个 job 的依赖安装和完整 `python -m pytest` 步骤均成功，没有失败、跳过或取消步骤。
- SQLite 本地实现、正式迁移、marketplace 源同步和 GitHub 远端门禁均已完成；剩余发布边界仅为通过 Plugin Management 重载插件，并在新任务核对新缓存工具面。

### 2026-07-22 23:26 - 完成任务书 12 套提示词与工具架构软件门禁

- 在独立 Conda 环境 `esp-mcp-toolchain` 中补齐并验证 `mpremote 1.28.0`；新增严格的 Conda bootstrap，插件服务不会静默落到全局 Python。
- 将公开提示词固定为 6 项基础 + 6 项提高共 12 套，源码面为 `48 tools / 12 resources / 12 prompts`；不通过隐藏别名扩张 prompt 数量。
- 新增程序 Ctrl-C 停止、GPIO 查询、硬件信息、板上回归、插桩性能分析，以及执行/采集/Monitor 自动异常报告。GPIO 和 runtime 要求接受程序中断；回归与性能分别要求显式确认执行和重复副作用。
- FastMCP 实际生成的枚举、数值范围、数组上下限和确认字段已测试；当时任务书专项为 `48 passed`，当时完整跨工作树门禁为 `182 passed in 28.88s`。
- COM3 已恢复枚举；只读串口采样为 0 字节，源码版 passive `esp_hardware_info` 成功读取 CH9102 描述和 reviewed mapping，`esp_error_parse_log` 对该 run 返回 `has_error=false`。
- 当时板上为 ESP-IDF 固件；MicroPython 程序停止、GPIO raw REPL、运行时采集、回归和性能执行尚未实板验收。当时没有明确烧录授权，因此未烧录、擦除、删除、复位、full clean 或驱动蜂鸣器。
- 本条记录只说明本地候选状态；提交、公开推送、远端 CI 和新版插件缓存验收尚未完成。

### 2026-07-26 - 完成安全串口生命周期基础层

- 串口统一改为零参构造，先禁用流控并将 DTR/RTS 置为非活动态，再显式打开；驱动打开后会再次压低控制线。
- 端口探测保留原始异常，并报告打开阶段、是否可能发生物理复位以及关闭清理结果；兼容原有 `port_can_open` 返回合同。
- 独立 Conda 环境中的串口生命周期专项为 `7 passed`；测试使用模拟串口，本步骤未访问板卡。

### 2026-07-26 - 完成 Raw REPL、程序停止和错误检测闭环

- Raw REPL 仅在严格确认 `OK + stdout EOT + stderr EOT + >` 完整帧后报告执行完成，保留不完整 stdout/stderr 和逐阶段协议证据。
- Ctrl-C 程序停止、源码发送和 Raw REPL 退出均检查串口短写；`sent` 与 `confirmed` 分开记录，不把主机写入成功冒充板端状态。
- 执行、固定采集和后台 Monitor 共用增量 MicroPython 异常检测；该错误检测子集为 `45 passed`，包含串口生命周期与 reset 的较宽关联门禁为 `62 passed`，完整候选门禁为 `226 passed`。

### 2026-07-26 - 完成 12 套任务书提示词和提高能力工具面

- 公开提示词固定为 6 项基础能力加 6 项提高能力，每套提示词声明工具顺序、确认要求、成功证据和失败分支。
- 新增 GPIO 状态、硬件信息、板上回归与性能分析工具；执行型能力保留程序中断与重复副作用确认。
- 提示词、提高工具和任务书架构专项为 `25 passed`；本步骤仍只验证软件合同，未触发板端执行。

### 2026-07-27 00:05 - 完成 main/test 同步后的标准软件门禁

- `main` 的已确认实现与文档合并到 `test` 提交 `381c5cc`，未覆盖 test 分支的 4 个回归提交。
- 清除跨工作树源码覆盖后，直接在 test 分支自身源码运行标准 `python -m pytest`，结果为 `226 passed in 27.66s`。
- 本门禁仍是软件结论；本步骤未访问真实板卡、未更新个人 marketplace，也尚未取得本轮远端 CI。

### 2026-07-27 00:26 - 修复 GitHub Actions 测试隔离问题

- 首次公开推送后的 main 四组 CI 均在旧串口测试桩失败；test 的 Windows 3.10/3.12 已通过，Linux 3.10/3.12 因测试修改全局 `os.name` 后在项目上下文清理阶段实例化 `WindowsPath` 失败。
- main 保留并验证已有的 Raw REPL/reset 假串口适配；Windows 进程树测试改用局部 `monkeypatch.context()`，在测试返回前恢复平台状态。
- 项目专属 Conda Python 3.12.13 下，main 为 `104 passed in 14.70s`，test 为 `226 passed in 28.08s`，test 加载 main 源码的跨工作树门禁为 `226 passed in 27.36s`。
- 这些结果只证明本地软件门禁；远端矩阵复跑、`erase_flash` 受管进程改造、个人 marketplace 同步和新版插件验收仍待完成。

### 2026-07-27 00:31 - 确认 P0 远端矩阵并补充 Bug 学习记录

- main [run 30210462578](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30210462578) 与 test [run 30210462530](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30210462530) 的 Windows/Linux、Python 3.10/3.12 共 8 个 job 全部成功。
- 新增 `docs/14-bug-fix-notes.md`，记录旧测试桩失配、共享 `os.name` 污染、局部 monkeypatch 修复方式和本地/远端验证证据；后续 Bug 修复提交继续记录 Cause、Fix、Verification 和 Residual risk。
- P0 已完成；下一步是测试先行改造 `erase_flash` 受管进程。本阶段仍未访问板卡，也未更新个人 marketplace。

### 2026-07-27 00:37 - 完成 erase_flash 受管进程软件门禁

- 根因是擦除后端仍直接调用 `subprocess.run()`，绕过已有的受管子进程树终止和清理证据；命令还依赖 esptool 默认复位行为，没有把前后复位语义固定在可审计参数中。
- test 分支先增加精确命令、超时、启动失败、清理元数据和 `confirm=False` 不调用后端的契约；旧 main 在新增后端契约下得到 `3 failed, 3 passed`，确认门专项为 `2 passed`。
- main 改为复用 `run_managed_command()`，显式传入 `--before default_reset --after hard_reset`，把公共错误映射为擦除领域错误，同时保留 stdout、stderr、returncode 和进程树清理字段。
- 修复后后端专项为 `6 passed`、擦除工具专项为 `8 passed`、main 全量为 `104 passed in 13.89s`，test 加载 main 源码的跨工作树门禁为 `228 passed in 27.76s`。
- 以上只证明模拟进程的软件合同；本步骤未连接串口、未实际擦除板卡，P1 远端矩阵也仍待推送验证。

### 2026-07-27 00:46 - 确认 erase_flash P1 远端矩阵

- main [run 30211040021](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30211040021) 与 test [run 30211040067](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30211040067) 的 Windows/Linux、Python 3.10/3.12 共 8 个 job 全部成功。
- 远端结果验证了受管擦除后端、确认门和 test 分支丰富回归在四种环境组合下兼容；它不构成真实板卡擦除或复位证据。
- 下一步只更新个人 marketplace 源并运行 validator/cachebuster；安装缓存由用户重启 Codex 后另行验收。

### 2026-07-27 00:57 - 更新个人 Marketplace 源

- 从已推送的 `main@2271e13` 精确同步 30 个 tracked 文件到 `C:\Users\16224\plugins\esp-mcp-toolchain`；没有镜像删除，也没有触碰 Marketplace 源的 `.git`、`data`、缓存、板卡产物或 Codex 安装缓存。
- Marketplace 源在项目专属 Conda Python 3.12.13 下运行 main 发布测试，结果为 `104 passed in 14.19s`；直接创建 FastMCP Server 枚举得到 `48 tools / 12 resources / 12 prompts`。
- `plugin-creator` validator 在同步前后均通过；唯一一次 cachebuster 更新把源版本从 `0.1.0+codex.20260722153803` 改为 `0.1.0+codex.20260726165544`，版本只含一个 `+codex` 后缀。
- 已安装缓存仍为旧版 `0.1.0+codex.20260722153803`。按约定不直接覆盖缓存、不代替用户重启；新任务需先核对安装插件的 48/12/12 工具面。

### 2026-07-27 01:02 - 修复 Monitor STARTING 测试竞态

- 最终发布记录提交的 main Windows/Python 3.10 job 在 `test_monitor_stop_while_starting_is_bounded` 偶发失败；同次 test 四组和 main 另外三组均成功。
- 根因不是生产 Monitor 状态机，而是测试用“1 秒内轮询到会话”代替线程同步。慢 runner 可能仍在执行 SQLite 初始化或尚未调度启动线程，`monitors` 为空时测试直接索引并产生 `IndexError`。
- 假串口现在进入 `open()` 时设置 `open_started` 事件；测试等待该确定性事件后断言唯一会话处于 `STARTING`，启动 stop 线程后再明确等待 `STOPPING`，最后才释放 open gate。两个等待都保留 3 秒有界失败，不依赖机器速度。
- 修复后该用例独立进程重复 `20/20` 通过，main 全量为 `104 passed in 16.67s`。本修改未访问串口，也没有改变生产 Monitor 行为；远端复跑仍待当前提交验证。

### 2026-07-27 13:20 - 完成 COM3 备份、擦除和 MicroPython 恢复

- 在用户明确授权前先读取 hardwork 映射并核对 `COM3` 空闲状态；备份完整 4 MiB flash 到项目数据目录，SHA-256 为 `23F1A7424286FED0BA59A1E6883DB4195CDF344F696B628C314892B24585B6B9`。
- `esp_erase_flash(confirm=true)` run `erase_flash_20260727_131837_f672becc` 返回成功，随后使用 `esp_restore_flash(confirm=true)` 从地址 `0x1000` 写入官方 ESP32_GENERIC MicroPython v1.28.0，run `restore_flash_20260727_131918_88af58ec` 完成写入哈希校验。
- 动作后捕获到 `MicroPython v1.28.0 on 2026-04-06` 启动 banner；runtime 探测确认 4 MiB flash、160 MHz CPU 和可用文件系统。hard reset 工具仍保留 `reset_confirmed=false` / `output_causality_confirmed=false` 的严格证据边界。

### 2026-07-27 13:28 - 实板下载验收暴露 MCP 当前目录路径缺陷

- 后台 Monitor 收到 20 条有序 MicroPython 标记，停止时记录 570 字节、0 dropped、0 unpersisted；三个明确验收文件的上传、逐字节读取和根目录列表均通过。
- `esp_file_download` 对相对输出路径返回 `ok=true`、21 字节且未截断，但项目工作区没有目标文件；同名文件实际写入版本化 Codex 插件安装缓存。根因是上传、下载和本地运行直接使用 `Path(...)`，相对路径继承 MCP 进程 `cwd`。
- 发现后立即暂停远程文件管理及后续板端验收；没有擅自删除安装缓存中的误写文件，也没有把该能力记为通过。

### 2026-07-27 14:04 - 完成本地主机路径边界软件修复

- test 分支先增加 15 个路径域合同；旧 `main@5985230` 得到预期的 `15 failed in 2.60s`。合同模拟插件缓存作为当前目录，覆盖 mpremote / Raw REPL 上传下载、本地脚本执行、父目录逃逸和工作区外绝对路径。
- main 的五个本地路径入口统一复用 `safe_project_path()`：相对路径绑定当前 `workspace_root`，越界在任何读取、建目录、写入或后端调用前返回 `unsafe_local_path`，板端 `remote_path` 语义不变。
- 修复后 main 专项为 `32 passed in 4.15s`，test 跨工作树专项为 `48 passed in 9.26s`，提交前 main 全量为 `119 passed in 17.64s`，test 加载 main 源码的全量门禁为 `243 passed in 31.50s`。
- 以上仅是本地软件结论；新提交、GitHub CI、Marketplace 同步、cachebuster 和重启后实板相对下载复验仍待完成。

### 2026-07-27 20:55 - 持久化 reset 有界原始输出证据

- 根因是 `esp_reset` 虽然读取动作后串口字节，却只把解码后的兼容字段 `text` 返回给调用方；`logged_task` 的完成事件白名单不会保存该字段，因此调用结束后无法从 SQLite 复核原始输出。
- `logged_task` 新增按工具声明的静态 `result_payload_keys` 白名单，并拒绝非 tuple 或空字符串键；没有把所有工具的 `text` 全局写入日志，避免扩大输出或敏感数据落库范围。
- `esp_reset` 现在把最多 65,536 字节的长度、SHA-256、Base64、替换解码文本、严格 UTF-8 解码状态、捕获是否完成和是否达到上限写入当前 run 的 completion 事件。`reset_output_capture_completed=false` 明确区分捕获失败与成功捕获到 0 字节。
- 本地定向门禁为 `58 passed in 5.71s`，main 全量为 `119 passed in 15.18s`，test 加载 main 源码的全量门禁为 `247 passed in 28.82s`。本切片只使用假串口和临时 SQLite，没有访问 `COM3`、复位、擦除或烧录板卡。
- 剩余边界不变：持久化输出不能独立证明输出由本次复位导致，`reset_confirmed=false` 与 `output_causality_confirmed=false` 仍被保留；Monitor 持有串口时的同句柄复位取证属于后续独立切片。

### 2026-07-27 21:05 - 修正 ESP-IDF 示例的 4 MiB Flash 描述

- 实物完整备份为 4,194,304 字节，但示例的旧本地 `sdkconfig`、`flasher_args.json` 和 bootloader 镜像头均声明 2 MB；根因是仓库没有受版本控制的 `sdkconfig.defaults`，构建结果继承了机器上的旧生成配置。
- 新增 `sdkconfig.defaults`，固定 ESP32、DIO、40 MHz、4 MB，并保留 single-app 分区布局；没有启用烧录时改写镜像头的选项，也没有把 4 MiB 误写成 4 MB 应用分区。
- 因 defaults 不会覆盖已有 `sdkconfig`，本机只精确更新被忽略配置中的三处 2 MB→4 MB 后执行普通 build；没有删除配置、没有运行 `set-target` 或 fullclean。
- `esp_project_build` run `build_20260727_210357_c45f0c14` 成功；生成命令包含 `--flash_size 4MB --flash_freq 40m --flash_mode dio`，esptool 离线解析 bootloader 为 `Flash size: 4MB / 40m / DIO`，SHA-256 为 `620A1ABEDBFF62995143824B5918B91689DFBB9601E46320D1E16D4DD40CE457`。
- 配置专项为 `2 passed in 0.43s`，main 全量为 `119 passed in 13.99s`，test 加载 main 源码的全量门禁为 `249 passed in 28.88s`。
- 该步骤没有访问或烧录 `COM3`。当前板上固件仍是前一版 2 MB 头镜像；真实启动警告消失必须等后续单独烧录和监控验收，不能由本次构建结论替代。

### 2026-07-27 21:14 - 持久化性能分析样本与汇总统计

- `esp_performance_profile` 原先只在调用返回值中提供 `samples`、`timing_us` 和 `memory_delta_bytes`，SQLite completion 事件只保存 iterations/profile_kind 等通用字段，导致历史 run 无法复核样本。
- 该工具现在复用 `logged_task` 的局部结果白名单，把最多 50 个工具生成的结构化样本、时间/堆变化汇总和 `sampling_profiler=false` 写入当前 run；通用 `stdout`、原始 marker 与内联 `code` 仍不持久化。
- 板端异常 `repr` 最多保留 256 字符；主机在 JSON 解析前拒绝超过 128 KiB 的 marker，只接收固定样本字段，并校验样本数、连续序号、布尔/整数类型、时长上限、32 位堆范围及 after-before 差值。未知字段会被丢弃，巨整数不会进入 `statistics.fmean`。
- 新合同覆盖成功/失败样本、超长异常、最坏控制字符、超大 marker、巨整数和畸形样本。性能专项为 `24 passed in 3.47s`，相关定向门禁为 `65 passed in 6.64s`，main 全量为 `119 passed in 13.97s`，test 加载 main 源码的全量门禁为 `256 passed in 29.70s`。
- 本切片没有访问板卡或重复执行用户程序；历史 run 不会被反向补写。截断不是秘密检测，目标代码不得把凭据写入异常信息。该工具仍是插桩 wall-time/heap delta，不是采样 profiler，也不能推断功耗或电流。

### 2026-07-27 21:43 - 建立分层 MicroPython 自动回归套件

- `examples/micropython_project/regression/manifest.json` 现在跟踪 safe、hardware_readonly、stateful、negative 四层共四个脚本；默认档只有无硬件/网络/文件写入的运行时烟测。GPIO34 只读和 GPIO32 LED 状态脚本必须显式选择，negative 必须单独运行，整套明确排除 GPIO25、蜂鸣器和 PWM。
- manifest 只是本地受审选择源，不会由工具自动解析、自动发现或自动上传。调用方仍须审查脚本、上传到 manifest 的精确 `remote_path`、展示最终路径和副作用，并在取得明确确认后把路径列表传给 `esp_regression_test`。
- `esp_regression_test` 继续在即时响应中返回逐项 stdout，但 SQLite completion 只增加 `result_summaries=[path,ok,duration_us,error_kind]`。异常最多保留 256 字符，结构化 marker 最多 16 KiB，主机验证状态、时长类型和 `capture_ms` 上限；深层 JSON 的 `RecursionError` 也被收敛为 `probe_result_invalid`。
- 响应现在明确 `reset_command_sent=false` 与 `physical_reset_excluded=false`：工具没有显式发送复位命令，但 Raw REPL 串口会话不能被描述为已经排除物理复位。
- 初始静态/SQLite 合同在旧实现上得到预期 `2 failed`；补充安全合同后为预期 `9 failed, 3 passed`；复审发现的深层 JSON 崩溃也先由单测复现再修复。最终相关定向门禁为 `74 passed in 7.30s`，main 全量为 `119 passed in 14.06s`，test 加载 main 源码的全量门禁为 `265 passed in 30.57s`。
- 以上均为软件合同和模拟 Raw REPL 结果；没有访问 `COM3`、上传或执行板端脚本，也没有驱动 GPIO。真实 safe/只读/stateful/negative 验收须在板上恢复 MicroPython 后分别确认。

### 2026-07-27 22:27 - 收紧 Flash 备份与恢复的主机路径边界

- `esp_backup_flash` / `esp_restore_flash` 只接受当前 workspace 或当前项目经校验的
  `artifacts/flash`；artifact 与 staging 目录中的 symlink/junction 会被拒绝。
- 备份使用项目私有 UUID staging，完成长度和 SHA-256 校验后再以“不覆盖已有 final”
  的方式发布。发布冲突或文件系统不支持 hard link 时返回完整镜像的 `recovery_path`，
  不把安全失败伪装成成功，也不删除唯一可恢复副本。
- 恢复继续要求 `confirm=True`，并先把源镜像复制到每次调用独占的 UUID staging，再交给 esptool；
  这样源文件在校验后被替换不会改变实际交给后端的镜像。
- 最终本地软件门禁：Flash 定向 `36 passed, 1 skipped`、main 全量 `119 passed`、
  test 显式加载 main 源码 `287 passed, 1 skipped`。跳过项是本机缺少目录 symlink
  创建权限；同一拒绝分支有不依赖该权限的确定性测试。本步骤没有访问 COM3、备份、
  擦除、烧录或恢复板卡，不能作为新的实板结论。

### 2026-07-27 23:28 - 完成 SQLite v3-A 仓储与可回滚迁移

- schema v3 为 `raw_logs` / `errors` 增加路径、SHA-256、行列号、可恢复状态约束，
  并建立按 run、kind 和时间查询的复合索引。
- 新增两组底层仓储 API：新记录使用稳定 UUIDv5，error ID 额外包含稳定 occurrence key，
  使同一次发生可重试、相同异常的不同发生可分别记录；完整相同的重试返回去重结果，
  同 ID 不同内容明确报冲突，不存在或跨项目的 run 会在写入前拒绝。
- v2→v3 不再用 `CREATE TABLE IF NOT EXISTS` 直接盖版本号，而是在单个
  `BEGIN IMMEDIATE` 事务中重建两张表、严格复制、核对行数和外键，最后才写 v3 marker。
  复制约束失败或最终外键检查失败时，表名、数据、v2 marker 和 `user_version=2`
  会整体恢复。
- 合同先在旧实现上得到预期 `20 failed`；完成实现并补齐假 v3 结构验证、v2 缺列/额外列
  拒绝、迁移后置约束、重复/并发升级、晚失败回滚、v1 直升与重复异常 occurrence 覆盖后，
  v3-A 专项为 `33 passed`，SQLite 合并定向为 `68 passed`，main 全量为
  `119 passed`，test 显式加载 main 为 `320 passed, 1 skipped`。
- 本阶段只操作临时 SQLite。正式项目数据库仍保持 v2，现有运行插件也仍只支持 v2；
  event/Monitor 写入、历史数据对账和 MCP 查询接入属于后续 v3-B/v3-C，不能把本阶段
  描述为日志闭环已经完成。本阶段没有访问 COM3 或执行任何板端动作。

### 2026-07-28 00:06 - 修复固定串口 capture 的覆盖与字节失真

- 原 capture 文件名只有秒级时间；同一 `session_name` 在同一秒执行两次时会复用路径，
  后一次可能覆盖前一次证据。
- 原实现先把串口 bytes 用替换模式解码为文本，再把文本编码后写盘；非法 UTF-8 字节会变成
  U+FFFD，`bytes_read` 也会统计替换文本的 UTF-8 长度，不能代表真实串口字节。
- 现在文件名加入随机 UUID 后缀并以排他 `xb` 模式创建，写入后 flush + fsync；原始文件
  直接保存收到的 bytes，文本只作为可读视图，`bytes_read` 精确统计原始长度。
- `logs/raw` 在打开串口前准备；目录不可用时不触碰串口。写入、flush、fsync 或 close
  失败时返回 `serial_capture_persist_failed`，保留真实字节数、文本、清理事实和
  属于本次调用的 `recovery_path`，不把未确认持久化的文件称为正式 `raw_path`；open
  失败或碰撞耗尽不会把不存在的路径、别人的既有文件误报为 recovery。
- 新增合同先稳定复现“15 个原始字节被报告为 19”和同秒路径相同两个红灯；修复后定向
  `25 passed`，main 全量 `119 passed`，test 显式加载 main 全量
  `327 passed, 1 skipped`。合同还强制 UUID 碰撞/耗尽、fsync/close 失败和读取失败；
  测试使用假串口和临时目录，没有访问 COM3。
- 这一小步只修复 v3-B 入库前的文件证据正确性；`raw_logs` / `errors` 原子投影、
  Monitor chunk 对账与正式查询仍按后续小提交完成。

### 2026-07-28 00:50 - 完成 SQLite v3-B2 原子 completion 证据投影

- 原实现只能先写 completion event，再由其他代码分别登记 raw/error；中途失败会留下
  “任务完成但证据缺失”的假完整记录，调用方还可能自行提供跨项目 ID 和时间。
- 新增不可变 `EventArtifacts` 和 `append_event_with_artifacts()`；event、可信 raw、
  occurrence-aware error 和 sequence 在同一个 `BEGIN IMMEDIATE` 中提交，任一步失败
  全部回滚。旧 `append_event` 二元组调用保持兼容。
- `logged_task` 只按工具显式策略构建证据，并让 completion UUID/时间贯穿 event、raw 和
  error。构建或提交失败时不写 completion-only event，也不篡改已经发生的业务结果；
  审计缺口通过 `logging_persisted=false` 和 warning 返回。
- 固定 capture 只登记当前项目 `logs/raw` 中经 reparse、普通文件、真实大小和 SHA-256
  核验的正式文件；`recovery_path` 永不登记为 raw。持久化失败与已捕获 traceback 可以
  分别形成 result/structured 两条错误。
- `esp_program_stop` 只登记明确的 `ok=false` 结果；正常 Ctrl-C 停止中预期出现的
  `KeyboardInterrupt` 不作为运行时错误。
- 初始红灯为 `11 failed, 1 passed`；最终专项 `15 passed in 1.61s`，main 全量
  `119 passed in 14.54s`，test 显式加载 main 全量
  `342 passed, 1 skipped in 35.69s`。两轮独立终审均为 P0=0、P1=0；没有迁移正式
  v2 数据库，没有访问 COM3。
- 本切片只完成固定 capture/程序停止的 completion 投影；Monitor chunk、历史对账和
  raw/error 查询分别在 v3-B3、v3-B4、v3-C 后续小提交中完成。

### 2026-07-28 13:03 - 完成 SQLite v3-B3 Monitor 终态产物对账

- 原 Monitor 在 chunk 最终重命名与 manifest 更新之间崩溃时会留下孤立 `.bin`；恢复逻辑
  也没有证明 manifest、磁盘和 SQLite 三方是同一精确集合。现在只在 stale 活动态收养
  合法孤立 chunk，终态拒绝额外文件，并重新核算每个文件和 bundle 的字节数、SHA-256。
- 原 `sqlite_reconciled` 同时承担旧生命周期和新 artifact 含义，单看布尔 marker 还可能把
  伪造或半完成状态当成成功。现在保留其旧语义，新增独立、版本化
  `sqlite-artifacts-v1.json`，并对终态 event、run、raw/error、JSONL、latest 和完整
  canonical marker 做逐项深度校验。
- 原路径校验与实际读取之间存在替换窗口。chunk 和 JSON 均改为受限打开后基于 fd 读取，
  读前/读后 `fstat` 复核普通文件、身份和长度；Windows reparse 与旧绝对路径采用保守拒绝。
- 原恢复锁只覆盖局部写入，两个恢复者可能同时扫描同一 run；删除锁文件还会产生 ABA。
  现在 run lease 覆盖扫描、修复、冻结、fd 复核、SQLite 事务、镜像和 sidecar 全流程；
  Windows 锁允许共享读写但禁止删除，释放时不 unlink，从而消除旧句柄与新文件并存。
- 旧版本 stale completion 的 UUID 和 `ended_at` 与当前算法不同。恢复会先严格核对历史
  SQLite 内容和最后事件；完全一致时复用旧 UUID/时间，不生成第二条 completion，
  冲突时拒绝修改。
- 持久 `RUNNING` 的 stop 不再虚报 `already_terminal=true`，而是先恢复为 FAILED 再对账；
  首个运行期错误冻结、close 重试、失败报告有界化、JSONL 同 UUID 冲突、latest/状态假成功
  等崩溃窗口也都加入合同。
- 最终 v3-B3 专项为 `43 passed, 2 skipped`，main 全量为
  `119 passed in 40.95s`，test 显式加载 main 全量为
  `385 passed, 3 skipped in 192.71s`，`compileall` 通过。测试只使用临时项目、临时
  SQLite 和模拟对象；没有升级正式数据库、访问 COM3、更新 Marketplace 或改安装缓存。
- 本切片不包含 v3-B4 通用历史对账和 v3-C raw/error 查询接入；两项继续按后续小提交完成。

### 2026-07-28 14:33 - 修复 v3-B3 双分支 CI 暴露的三类合同问题

- main 首轮远端 run `30330801910` 在 Linux/Python 3.10 teardown 报
  `Bad file descriptor`。根因是 `_safe_binary_reader` 把 fd 交给
  `fdopen(closefd=True)` 后又在外层执行 `os.close`；并发下同一整数 fd 可能已经被系统
  复用，第二次关闭会误关另一线程的新文件。
- 修复后，`fdopen` 成功即由 file object 单独拥有关闭责任；只有 `fdopen` 构造失败时，
  原始 descriptor 才显式关闭一次。成功转移和构造失败各有独立测试合同。
- test 首轮 run `30330806829` 失败不是 v3-B2 缺失，而是 test 产品代码只同步到 B2，
  新增 B3 合同时没有同步 B3 产品实现。GitHub 只检出 test 自身，无法使用本机另一工作树；
  因此把固定 `main@98d9403` 合入 test，并继续由 main 单点维护 README。
- Linux 实际 symlink 测试又发现旧断言把“进入终态对账后的投影失败”误套到“恢复预检即
  拒绝”的路径。生产 fail-closed 行为保持不变；测试改为断言恢复错误、manifest 字节不变、
  `artifact_marker=None`、无 sidecar、无 SQLite raw 且外部目标不变。
- 最终本地 Conda 门禁为 main `119 passed`、test `387 passed, 3 skipped`，显式跨工作树
  同为 `387 passed, 3 skipped`；main run `30333882504` 和 test run `30334699560`
  共 8 个远端 job 全部成功。
- 本步骤没有访问 COM3、升级正式 v2 数据库、修改 Marketplace 源或安装缓存。

### 2026-07-28 15:17 - 完成 SQLite v3-B4.1 既有终态 event 证据补投影

- 新增 `reconcile_existing_event_artifacts()`，只向已存在、已结束 run 的最后一个
  `complete` event 补入 raw/error；返回原 event 且 `event_inserted=false`，不写
  run/event、不改变 `next_sequence_no`。同 bundle 精确重试返回 `inserted=false`，
  两个并发调用最终只插入并保留一组记录。
- 第一轮实现只检查 run 已终态，导致该 run 的 `prepare` event 或后续还有更高序号的旧
  `complete` event 也能补证据；同时先检查 run 状态再验证 event 归属，会在错误 UUID
  尚未通过作用域校验时暴露运行状态。修复后先验证 project/run/event 绑定，再检查 run，
  并同时要求 `phase=complete`、`sequence_no=next_sequence_no-1`。
- 第一轮异常边界漏掉 `EventRepositoryError`，且 `commit()` 位于包装块之外，因此非法
  artifact 时间戳和提交失败会泄出底层异常。现在 raw、error、时间规范化和 commit 均位于
  同一 `BEGIN IMMEDIATE` 投影边界，统一抛 `artifact_projection_failed`、保留 cause，
  并由外层完整回滚。
- 独立复审补强后的红灯为 `4 failed, 7 passed`；修复后专项 `11 passed in 1.78s`，
  SQLite 相关 `146 passed, 2 skipped in 50.21s`，main 全量
  `119 passed in 49.32s`，test 显式加载 main 全量
  `398 passed, 3 skipped in 239.99s`。复审结论为 P0=0、P1=0。
- 本切片只完成 B4.1 仓储原语；B4.2-B4.4 的历史 manifest/chunk resolver、
  capture/JSONL adapter 和项目级启动/状态入口尚未开发。本步骤只使用临时 SQLite，
  未访问 COM3、未升级正式数据库、未更新 Marketplace 或安装缓存。

### 2026-07-28 15:46 - 修复 B4.1 远端矩阵暴露的 Monitor 清理竞态测试

- B4.1 首轮远端中，main 的 Windows/Python 3.12 在
  `test_monitor_disconnect_preserves_buffer_and_terminal_reason` 失败；同一 main 的另外
  3 个 job 和 test 的 4 个 job 成功。失败发生在既有 Monitor 测试，不是 B4.1 仓储断言。
- 根因是旧测试看到 `DISCONNECTED` 后只轮询 1 秒便要求 `worker_alive=false`。生产 worker
  会先发布断连终态，再在 `finally` 中关闭串口、释放 lease、关闭日志并执行 SQLite/JSONL
  终态对账；因此 `DISCONNECTED` 表示终止原因已确定，不是线程清理完成屏障。
- 测试改为调用公开 `esp_serial_monitor_stop(timeout_ms=5000)` 等待 worker join，并同时
  断言终态保持 `DISCONNECTED`、`worker_alive=false`、`log_store_closed=true` 和
  `cleanup_complete=true`；生产状态机没有修改。
- 新增 Event 门控用例，故意阻塞终态对账：零超时 stop 必须返回
  `monitor_cleanup_timeout` 且 worker 仍存活，释放门控后再次 stop 必须完整退出。自动 fixture
  也会断言每项测试前后没有残留 worker，避免线程泄漏污染后续用例。
- 两项针对性回归为 `2 passed in 1.31s`，独立进程连续重复 `30/30`，Monitor 文件
  `23 passed in 35.85s`，main 全量 `120 passed in 50.72s`，同步后的 test 标准全量
  `399 passed, 3 skipped in 249.96s`。
- 修复后的 [main run 30340384047](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30340384047)
  与 [test run 30340395467](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30340395467)
  共 8 个 job 全部成功。这些均为假串口与临时项目的软件测试，没有访问 COM3。

### 2026-07-28 16:52 - 完成 SQLite v3-B4.2 历史 Monitor 纯文件 resolver

- 历史 v1 manifest 把 chunk 保存为原工作区绝对路径。工程移动后直接使用该路径会找不到文件，
  也会把不可信历史字符串变成文件系统访问目标；此前项目没有单独的只读解析层来判断 chunk
  完整性、B3 ownership 和目录替换。
- 新增 `resolve_historical_monitor_artifacts()`：显式要求既有 event UUID，只读取当前项目
  `logs/serial/<run_id>/manifest.json` 和由 chunk ID 派生的当前文件。v1 旧路径只把反斜杠
  规范为 `/` 后校验本地盘与 `serial/<run_id>/<chunk>` 后缀，绝不调用
  `Path/stat/open/resolve/exists`；v2 只接受规范 `name` 且禁止 `path`。
- manifest 从同一安全 fd 完成大小、UTF-8/JSON、文件身份和 SHA-256 复核；resolver 前后
  比较 project/log/serial/run 目录链，并验证终态、时间、精确 chunk 集、连续 ID、
  `persisted_bytes`、长度和 SHA-256。B3 sidecar 或旧 ownership 字段会 fail-closed；
  释放后保留的 `.sqlite-artifacts.lock` 不等于 ownership，因为 B4.4 必须持该 lease
  重新运行 resolver。
- resolver 返回 `resolved` 或显式 `no_artifacts` 候选和不可共享修改的 `last_error` 快照，
  不获取 lease、不扫描其他项目、不连接 SQLite，也不写 manifest、sidecar、JSONL 或 latest。
  数据库 native profile、持 lease 二次解析和 B4.1 调用仍属于 B4.4。
- 初始 42 条合同在入口缺失时全部按预期红灯；独立复审补入陈旧/缺失
  `process_owner`、caller `run_id` 越界、祖先 reparse、目录身份变化和持久 lease 后，
  再加入合法 POSIX v1 历史路径和 Windows 根相对路径拒绝，B4.2 专项为
  `58 passed in 1.66s`。既有 Monitor 回归为
  `28 passed in 41.00s`，最终源码 main 全量为 `120 passed in 51.67s`；合入固定
  main 后，test 分支自身源码全量为 `457 passed, 3 skipped in 249.69s`。
- 对正式项目执行只读兼容检查：22 个 v1 manifest 中 14 个 `resolved`、8 个
  `no_artifacts`、0 个错误，Monitor 文件与正式 SQLite 文件元数据前后不变。此次没有打开
  COM3、执行板端程序、写正式 SQLite、升级 schema、更新 Marketplace 或安装缓存。

### 2026-07-28 17:28 - 修复 Windows Monitor lease 零长度竞态

- B4.2 的 test 分支 GitHub run `30345364620` 仅在 Windows/Python 3.10 的既有
  Monitor 并发对账测试失败；其余 7 个双分支矩阵 job 成功。没有用重跑掩盖该失败。
- 本机独立进程在第 4 次复现同一结果，并用确定性测试确认根因：持锁线程截断并重写
  `.sqlite-artifacts.lock` 元数据时，竞争线程会在真正申请 byte-range lock 之前看到
  零长度文件、写入占位字节，并在 `flush()` 得到 `PermissionError [Errno 13]`。该异常
  被包装为不可恢复的普通对账失败，而不是应有的 recoverable busy。
- 修复删除加锁前的零长度占位写，空文件直接申请 byte-range lock；Windows 允许锁定 EOF
  之后的区域，取得 lease 后才执行已有的 truncate/write/flush/fsync。这样真实权限或磁盘
  错误仍保持普通失败，只有实际锁竞争返回 busy。
- test 分支先固化了预期红灯合同；修复后的确定性 busy/release/reacquire 检查通过，双线程
  独立循环 2000 次得到 2004 次成功、1996 次 busy、0 次普通失败；新增合同和原并发测试
  `2 passed`，原并发测试独立 pytest 进程 `100/100`，main compileall 与全量
  `120 passed in 51.01s`，合入固定 main 后 test 分支自身源码
  `458 passed, 3 skipped in 256.55s`。两轮只读审查均为 P0=0、P1=0；修复后的
  [main run 30347587842](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30347587842)
  与 [test run 30347592644](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30347592644)
  共 8 个 job 全部成功，且没有重跑失败 job。本步骤没有访问 COM3、板卡或正式项目数据库。

### 2026-07-29 13:57 - 完成 SQLite v3-B4.4 历史项目协调器

- 原 B4.2/B4.3 只生成单个候选，没有项目级执行者；租约外 profile 预检、run-scoped raw ID
  和独立 marker 都不能在进程退出后形成 SQLite 内的唯一所有权。Monitor 的历史秒级时间与
  SQLite 规范时间还可能存在不足一秒的差异。
- schema v3 新增持久 `historical_raw_claims`，并以复合外键证明 claim 的 event 属于同一
  project/run；严格仓储入口只接受来源可证明的 event 十字段和 run 六字段，Monitor 仅对
  event `ts` 使用不超过 1 秒的容差，其他字段仍精确比较。
- 新增项目级持久非阻塞 lease 和版本化 marker store。协调器只读探测 schema，v2 在创建
  lock/marker 前拒绝；v3 扫描 capture/Monitor，先拒绝不同 `(run_id, event_uuid)` 的共享
  raw，再两次解析。Monitor 的第二次解析与 B4.1 调用均在该 run lease 内。
- SQLite 逐 candidate 原子提交，项目级任务允许失败后幂等续跑；最终 marker 发布失败不会
  回滚已经提交的 SQLite，而会返回 `database_persisted=true/marker_persisted=false`，
  下次精确重试补齐 marker。释放后遗留 running marker 会报告 `interrupted`。
- Windows 原子替换在并发读者存在时可能短暂返回 WinError 5/32；store 只对这两类共享冲突
  做最多 1 秒的有界重试。Busy 的 `recoverable` 会映射为 `retryable=true`，损坏锁元数据
  通过 status 明确返回 `ok=false`。
- 项目协调器 12 项合同、B4.1-B4.4 组合 `145 passed, 1 skipped`、main 全量
  `120 passed`、test 跨工作树全量 `531 passed, 4 skipped`。正式项目数据库仍为 v2，
  本步骤没有迁移正式库、访问 COM3、更新 Marketplace/安装缓存或修改用户 plugin manifest。

### 2026-07-29 14:43 - 完成 v3-C1 日志查询只读边界

- 缺陷根因：`esp_logs_latest/get/query` 原先在查询前调用写入型数据库准备，导致缺库查询
  创建 SQLite，v2 查询静默升级到 v3，并可能在线导入 JSONL；损坏库或坏 JSON 还会泄漏
  底层异常。
- 新增独立 `mode=ro`、`query_only` 连接和 schema 能力探测；get/latest 的关联读取在
  同一只读事务快照内完成。v2 只读 runs/events，v3 必须具备当前查询所需结构；未知或
  不完整 schema 明确拒绝，查询路径不调用初始化、迁移或 importer。
- 缺库保持兼容语义：latest 为空、get 为 `run_not_found`、query 为空结果，且不创建
  数据库或日志目录；该结论以已经完成 `project_context_select` 为前置。损坏结构和持久化 JSON 统一返回不可恢复的
  `log_database_invalid`。
- test 分支先得到 `4 failed` 红灯，修复后扩展为 `10 passed`；既有日志/项目上下文回归
  `6 passed`，main 全量 `120 passed in 49.27s`。SQLite WAL 只读连接可能按官方协议创建协调用 `-wal/-shm`，因此只严格
  保证主数据库、schema 和应用文件不变，不对仍可能写入的日志库误用 `immutable=1`。
- 锁定、忙碌、权限和 I/O 可用性问题返回可恢复的 `log_database_unavailable`；只有
  格式/内容损坏返回不可恢复的 `log_database_invalid`，避免把瞬时竞争误报为永久损坏。
  若数据库路径已存在但为目录或其他非普通文件，也按异常存储状态拒绝，不能冒充缺库。
- 本步骤只使用 pytest 临时项目，没有读取或修改正式 SQLite、访问 COM3、更新
  Marketplace/安装缓存，也没有纳入用户的 plugin manifest 差异。
- 固定 `main@187fced` 合入 test 后，C1 专项 `10 passed in 0.47s`，test 分支自身源码
  全量 `544 passed, 4 skipped in 246.95s`。

### 2026-07-29 15:19 - 固化 v3-C2 正式日志详情合同

- 缺陷根因：`esp_logs_get` 只返回 run/events，已经写入 schema v3 的 raw_logs/errors
  无法通过公开日志详情读取；若直接复用现有 getter，又会另开写型连接且无行数/文本上界。
- test 分支先加入四项预期红灯：v3 正式 artifact 与跨项目隔离、最新窗口和截断元数据、
  v2 明确无正式 artifact 能力且不迁移、非法持久 raw 路径 fail-closed。
- 合同保持原有 `ok/project_id/run_id/run/events`，新增
  `query_source/raw_logs/errors/artifact_capability/artifact_truncation`。raw/error 必须在
  C1 的同一只读事务内查询；列表使用 project/run 双过滤、稳定 `LIMIT + 1`，错误大文本
  在 SQL 侧截断并逐字段标记。
- 旧实现基线为 `4 failed in 0.89s`，四项均命中字段/校验能力缺失。本提交只建立测试门禁，
  没有查询正式 SQLite、扫描 artifact 文件、访问 COM3 或修改 Marketplace/安装缓存。

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
- 产品实现和文档提交到 `main`；`test` 分支的分支专属提交只维护测试文件、测试目录和验证规则，门禁由 `index-test` 测试加载 `index` 源码执行。
- 功能合入前必须通过本地 `python -m pytest` 全量测试和 GitHub Actions；依赖真实硬件时还必须通过硬件门禁。
- README 维护稳定能力和里程碑；用户可见变化写入 CHANGELOG，当前门禁写入开发状态页，架构决定写入 ADR。
- 提交信息要写明当次提交完成的工作和修改内容。
- 提交前运行 `python -m pytest`。

## 相关文档

- `docs/00-overview.md`
- `docs/01-mcp-lifecycle.md`
- `docs/02-tool-spec.md`
- `docs/04-prompt-spec.md`
- `docs/05-hardwork-module.md`
- `docs/06-memory-module.md`
- `docs/07-database-design.md`
- `docs/10-development-roadmap.md`
- `docs/11-development-rules.md`
- `docs/12-development-status.md`
- `docs/13-taskbook-capability-architecture.md`
- `docs/14-bug-fix-notes.md`
- `docs/adr/0001-feature-branch-workflow.md`
- `docs/adr/0002-serial-monitor-architecture.md`
- `docs/adr/0003-sqlite-log-authority.md`
- `CHANGELOG.md`
