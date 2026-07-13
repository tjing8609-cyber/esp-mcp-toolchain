# 当前开发状态

更新时间：2026-07-13 17:15（Asia/Shanghai）

## 当前分支

- 分支：`feature/serial-monitor`
- 目标：完成后台串口 Monitor，不在本任务中启动 SQLite、日志查询增强或项目迁移体系开发。
- 合入状态：未合入 `main`。

## 已完成的软件验证

- 四个 MCP 工具已注册：start、stop、status、read。
- 状态机覆盖 `STARTING`、`RUNNING`、`STOPPING`、`STOPPED`、`FAILED`、`DISCONNECTED`。
- Monitor 启动时固定项目和日志路径；切换活动项目不会改变正在运行会话的写入目标。
- 读取接口使用 `seq` / `after_seq` 游标、有界缓冲、`dropped_before_seq` 和最大返回字节数。
- 原始串口字节按块持久化，支持 text、base64 和 both 三种读取表示。
- 跨进程锁、进程所有权、端口身份记录、stdin EOF 清理、内部异常清理和强制终止后的陈旧锁恢复已有自动化测试；另有回归测试保证活跃的其他 MCP 进程不会被误判为陈旧会话。
- 当前源码 MCP 枚举结果：43 tools / 12 resources / 4 prompts。
- 当前全量测试结果：99 passed；Monitor 专项：27 passed。

## 硬件门禁

- 当前枚举到的 `COM6`、`COM7` 都是蓝牙串口，`likely_esp=false`。
- 没有检测到此前验证过的 ESP USB 串口，因此没有尝试打开任何串口，也没有猜测端口。
- 真实板卡验收待完成：连接板卡后重新枚举，核对端口身份，启动 Monitor，产生已知串口输出，使用游标读取，停止会话并确认端口可重新打开。

## 插件同步

- 功能分支和个人 marketplace 源均通过 plugin validator，源版本为 `0.1.0+codex.20260713091610`。
- 从 marketplace 源直接启动 stdio MCP 得到 43 tools / 12 resources / 4 prompts，四个 Monitor 工具与 read schema 完整。
- 当前 PowerShell 无权执行 Codex 桌面版 MSIX 内置 `codex.exe`，因此没有完成正式 `codex plugin add`；现有安装缓存仍为 `0.1.0+codex.20260713051437`。新工具是否出现在 Codex 当前插件缓存中仍需在可用 CLI 或重启后的新任务验证。

## 后续顺序

1. 完成 Monitor 真板门禁并通过 CI 后，才可合入 `main`。
2. SQLite schema 与仓储层。
3. 日志查询增强。
4. 工程路径重绑定、项目合并、导出、导入和完整性校验；该迁移体系继续暂停。

当前任务提交功能分支后暂停。
