# 开发日志

本文件从旧版 README 的逐条进度记录重新整理而来。README 只保留用户入门信息；本文件记录实现过程和证据边界，`CHANGELOG.md` 继续记录用户可见的版本变化。

## 记录规则

- 每个里程碑说明完成内容、验证方式和仍未证明的范围。
- “软件验证”指本地 pytest、stdio MCP 烟测或模拟后端；“远端验证”指 GitHub Actions；“安装态验证”指 Codex 实际加载的插件缓存；“实板验证”指真实 ESP 与串口观测。
- 历史测试数量、端口、固件和安装版本只对当时快照有效，不能自动外推到当前代码或其他板卡。
- 擦除、烧录、恢复、删除和清理等高风险动作只记录已经明确授权并实际执行的范围。

## 2026-08-09：README 重新聚焦 Codex 插件

- 核对 GitHub 仓库 `tjing8609-cyber/esp-mcp-toolchain`，确认它由 manifest、skill 和 MCP Server 组成，是目标 Codex 插件仓库；本地独立 `esp-mcp` 仓库不在本次范围内。
- 发现 GitHub `main@ba44dbc` 的根 README 已被替换为电子钢琴说明，与插件 manifest 和仓库主体不一致。
- 将 README 重写为“解决的问题、项目的作用、快速启动、注意事项”四部分，删除开发计划、分支流程、仓库结构、当前进度和逐条开发日志。
- 把旧 README 中 2026-07-09 至 2026-07-30 的 73 条记录压缩为本文件的里程碑，并保留详细文档索引。
- 本次只修改文档；未改插件代码，未操作串口或板卡。发布提交仅包含 README 和本文件。

## 2026-07-30：v0.1.0 发布封口

- 固化首个 GitHub 版本，公开能力面为 48 tools、12 resources、12 prompts。
- 完成独立 Conda 启动、严格串口生命周期、Raw REPL 完整帧、程序停止、SQLite v3 日志链、12 套任务书能力和确定性 UART-only 构建规则。
- 当时的本地门禁包括 main 全量 `120 passed`，test 分支 `587 collected / 583 passed / 4 skipped / 0 failed`；4 项 skip 是 Windows 普通文件 symlink 权限边界，不是功能失败。
- 实板封口覆盖经授权的 4 MiB 备份、UART-only 烧录与 READY/HEARTBEAT、KEY1 只读状态、MicroPython/mpremote 恢复和指定临时文件删除。
- 恢复后没有再次完整读取 4 MiB Flash，因此不声称恢复后的全片哈希与备份完全一致；UART-only 结果也不证明蜂鸣器、供电或其他电气行为。
- 发布依据见 [v0.1.0 Release](https://github.com/tjing8609-cyber/esp-mcp-toolchain/releases/tag/v0.1.0) 和 [发布说明](16-release-notes-v0.1.0.md)。

## 2026-07-28 至 2026-07-29：SQLite v3 正式证据链

- 为 runs、events、raw logs 和 errors 建立 schema v3 约束、稳定标识、复合索引和严格幂等规则。
- 将 completion event 与 raw/error artifacts 放入同一事务，任一冲突时整体回滚，避免“任务成功但证据只写了一半”。
- 增加 Monitor 与固定 capture 的历史证据解析、项目级协调、持久 claim、并发 lease、sidecar 深度核验和可中断续跑。
- `esp_logs_get` 增加正式 raw/error 详情与截断元数据；`esp_error_parse_log` 按 SQLite errors、已验证 raw、旧兼容路径的顺序选择来源。
- 正式数据库升级前先在副本演练并保留备份；这些数据库结论不等于本阶段重新操作了真实板卡。

## 2026-07-26 至 2026-07-27：安全串口、Raw REPL 与实板恢复

- 串口统一采用打开前后控制 DTR/RTS 的生命周期，并把打开副作用、动作输出和清理结果分开记录。
- Raw REPL 只有收到完整 `OK + stdout EOT + stderr EOT + >` 帧才报告完成，并检测短写、跨 chunk Traceback 和自定义异常。
- 增加程序停止、GPIO 状态、硬件信息、自动回归和性能分析等能力，任务书接口达到 48/12/12。
- 将 `erase_flash` 接入受管进程树与显式复位参数；软件门禁通过不代表真实擦除已经执行。
- 2026-07-27 在明确授权下完成 COM3 的 4 MiB 备份、整片擦除和官方 MicroPython 恢复。后续相对下载暴露主机路径错误后停止扩大实板范围，并转入路径边界修复。

## 2026-07-20 至 2026-07-22：SQLite 日志与任务书能力架构

- 引入 project-scoped SQLite runs/events 仓储、迁移、结构化日志过滤和错误查询；JSONL 保留为审计镜像。
- 在临时副本验证后再执行正式迁移，记录行数、冲突和回滚边界。
- 按任务书整理 6 项基础能力和 6 项提高能力，形成 12 套 prompts，并扩展相应工具、资源和 schema 测试。
- 当时的本地、远端和 Marketplace 结果属于对应提交快照，不能替代后来 v0.1.0 的最终证据。

## 2026-07-11 至 2026-07-13：项目隔离、硬件审查与后台 Monitor

- 增加 `project_context_select` / `project_context_status`，用规范化工作区生成稳定 `project_id`，将 hardwork、memory、日志、产物、SQLite 和串口选择按项目隔离。
- 增加对话附件归档、基础硬件映射提交和增量补丁；硬件工具在映射审查完成前保持门禁。
- 把默认运行数据迁移到稳定的用户目录，避免插件 cachebuster 更新后项目状态随版本目录丢失。
- 增加显式旧数据迁移：默认只预览，确认后只复制缺失文件；同内容跳过、冲突文件不覆盖，并写审计与回滚清单。
- 后台串口 Monitor 建立状态机、游标读取、有界缓冲、原始字节分块、跨进程串口锁和退出清理；当时曾完成 COM3 实板验证，但该历史结果不代表当前端口或当前固件状态。

## 2026-07-09：官方 MCP SDK 与 Codex 插件可见性

- stdio MCP Server 切换到官方 MCP Python SDK 的 FastMCP，由 SDK 处理初始化、能力协商和 tools/resources/prompts 路由。
- 补齐 `.codex-plugin/plugin.json`、`.mcp.json`、skill 与资源注册，使 Codex 能识别插件并通过 stdio 启动服务。
- 增加专用 Conda 环境和启动器；找不到 `esp-mcp-toolchain` 解释器时失败关闭，不污染或静默使用全局 Python。
- 封装串口、ESP-IDF、esptool、mpremote 和 Raw REPL 的初始后端，并在当时范围内完成软件与部分实板烟测。

## 2026-07-07：项目骨架

- 建立 Python CLI、MCP Server、工具/资源/prompt 注册、hardwork、memory 和 JSONL 日志的基础目录。
- 明确项目边界：提供通用 ESP 开发工具链，不承载具体业务固件，不开放任意 Shell，也不在未确认时执行高风险动作。

## 文档索引

- [CHANGELOG.md](../CHANGELOG.md)：用户可见的 Added、Changed、Fixed 和版本验证记录。
- [开发路线图](10-development-roadmap.md)：历史阶段规划。
- [开发规则](11-development-rules.md)：分支、测试与证据规则。
- [当前开发状态](12-development-status.md)：v0.1.0 时点的实现和验证边界。
- [Bug 修复记录](14-bug-fix-notes.md)：缺陷原因、修复与复发防护。
- [发布冻结检查](15-release-readiness.md)：v0.1.0 发布门禁。
- [v0.1.0 发布说明](16-release-notes-v0.1.0.md)：正式能力和证据摘要。
