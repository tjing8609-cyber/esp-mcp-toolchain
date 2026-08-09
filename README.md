# ESP MCP Toolchain（Codex 插件）

## 解决的问题

本项目解决的是 Codex 在本机进行 ESP 开发时缺少统一、安全、可追溯操作入口的问题。

- 串口、ESP-IDF、esptool、mpremote、MicroPython REPL、日志和硬件资料原本分散在不同命令与目录中，难以让 Codex 稳定调用。
- 普通对话上下文不能可靠保存工作区、板卡映射、历史日志和已验证事实，插件更新后也容易丢失项目状态。
- 烧录、擦除、恢复、清理和板端删除具有真实副作用，需要明确确认、路径边界和可核查证据。
- 软件测试、传输字节数和串口文本不能自动证明 LED、蜂鸣器、供电等物理行为，需要把软件证据与实板证据分开记录。

本仓库提供的是通用 ESP 开发工具链，不是电子钢琴等具体业务固件，也不提供任意 Shell 或任意文件系统访问能力。

## 项目的作用

本仓库以 Codex 插件形式组织能力：`.codex-plugin/plugin.json` 声明插件、skill 和 MCP Server，`.mcp.json` 通过 `scripts/run_mcp_server.py` 在专用 Conda 环境中启动 FastMCP stdio 服务。

v0.1.0 的公开能力面为 48 个 tools、12 个 resources 和 12 套 prompts，主要覆盖：

- 选择并隔离实际业务工作区，迁移旧版项目数据。
- 枚举、选择、采集和后台监控串口。
- 编译 ESP-IDF 工程，以及受确认门保护的清理、烧录、备份、擦除和恢复。
- 管理 MicroPython 板端文件，执行或停止程序，并进行复位。
- 归档硬件附件、维护经过审查的 GPIO/串口映射和项目稳定事实。
- 记录与查询运行、事件、原始日志和错误；SQLite 是正式查询源，JSONL 保留为审计与迁移镜像。
- 在显式确认边界内执行 GPIO 查询、硬件信息采集、回归测试和性能分析。

它负责把 Codex 的请求转换为结构化、可审计的 ESP 操作；业务固件设计、接线正确性和真实硬件效果仍需由项目代码、硬件资料与实板验证共同确认。

## 快速启动

1. 克隆仓库并进入目录：

   ```powershell
   git clone https://github.com/tjing8609-cyber/esp-mcp-toolchain.git
   Set-Location .\esp-mcp-toolchain
   ```

2. 使用项目自带脚本创建或更新独立 Conda 环境，不要安装到全局 Python：

   ```powershell
   .\scripts\setup_env.ps1
   conda activate esp-mcp-toolchain
   ```

3. 先做无硬件写入的 CLI 检查：

   ```powershell
   python .\toolchain\cli.py --help
   python .\toolchain\cli.py port-list
   ```

4. 按 [Codex 官方插件说明](https://developers.openai.com/codex/build-plugins)，在 Codex 中调用 `$plugin-creator`，让它在保留现有 manifest、skill 和 MCP 配置的前提下把本目录加入本地 Marketplace；随后刷新 Codex，安装 `esp-mcp-toolchain`，并在新任务中测试。仓库地址本身不等于已经完成插件安装。

5. 插件加载后，先让 Codex 绑定实际业务工作区，再从只读检查开始。例如：

   ```text
   选择当前 ESP 项目工作区，核对项目状态，然后列出可用串口；不要烧录、擦除或删除任何内容。
   ```

正常通过 Codex 使用时，`.mcp.json` 会启动 stdio 服务，不需要在普通终端中手工保持 `run_mcp_server.py` 运行。

## 注意事项

- 每个项目级工作流必须先调用 `project_context_select`，参数应是实际业务工作区根目录，不能用插件安装目录代替。
- 涉及 GPIO、串口、芯片、Flash 参数或板载外设前，应先读取并审查硬件资料；资料确认、实板确认、模型推断和未确认事实不能混为一谈。
- 烧录、擦除、恢复、清理、板端删除和 ESP-IDF target 变更等操作需要针对当次目标单独明确确认。上传、执行和复位同样可能改变板端状态。
- 不要让 Thonny、其他串口监视器或多个 MCP 进程同时占用同一串口，也不要把某个历史 COM 端口写成通用默认值。
- 启动器找不到 `esp-mcp-toolchain` 专用 Conda Python 时会直接失败，不会静默退回全局 Python；必要时可用 `ESP_MCP_PYTHON` 指定解释器。
- 默认项目数据位于 `%USERPROFILE%\.codex\esp-mcp-toolchain\data\projects\<project_id>`；使用 `ESP_MCP_DATA_ROOT` 改址前应先确认迁移和隔离影响。
- pytest、GitHub Actions、工具返回 `ok=true`、写入字节数或串口输出都只证明各自覆盖的证据范围，不等于真实板卡的全部物理行为已经验证。
- 本项目可确认的是 GitHub 源码与 v0.1.0 Release；不要据此声称已经发布到公共插件目录或支持通过 GitHub URL 一键安装。
- 开发历史已从 README 移至 [开发日志](docs/17-development-log.md)。用户可见版本变化见 [CHANGELOG](CHANGELOG.md)，当前实现与证据边界见 [开发状态](docs/12-development-status.md) 和 [v0.1.0 发布说明](docs/16-release-notes-v0.1.0.md)。
