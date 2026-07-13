# 当前开发状态

更新时间：2026-07-13 17:41（Asia/Shanghai）

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
- GitHub Actions 已确认提交 `60a3a83` 的 Windows/Linux、Python 3.10/3.12 四个任务全部成功；4 条 Node.js 20 弃用警告来自 Actions 运行时，不是测试失败。

## 硬件门禁

- 重新枚举后确认 `COM3` 为 `USB-Enhanced-SERIAL CH9102`，VID:PID=`1A86:55D4`、序列号 `54AC011277`、location=`1-8`，`likely_esp=true`；`COM6`、`COM7` 仍是蓝牙串口，未打开。
- `monitor_20260713_173011_acd850be` 在 `115200` 捕获两条 ESP-IDF 启动记录，共 7,306 字节，包含 `POWERON_RESET`、项目名 `esp_idf_key_led_buzzer` 和 ready 信息。
- 首次读取返回 `seq=1..2`、`next_after_seq=2`；随后使用 `after_seq=2` 立即读取返回空记录，没有重复旧内容，也没有缓冲丢弃。
- 停止结果为 `STOPPED`、`cleanup_complete=true`、`worker_alive=false`，7,306 字节全部持久化，`last_error=null`。
- 随后以 `monitor_20260713_173300_7c343c91` 立即重新打开并停止同一 `COM3`，确认句柄和锁已释放。
- 当前 ESP-IDF 固件的 UART0 运行时控制台 `115200` 已作为 `board_test_confirmed` 事实增量写入当前项目硬件映射；不把该固件参数表述为所有固件的板级默认值。

## 插件同步

- 功能分支和个人 marketplace 源均通过 plugin validator，源版本为 `0.1.0+codex.20260713091610`。
- 从 marketplace 源直接启动 stdio MCP 得到 43 tools / 12 resources / 4 prompts，四个 Monitor 工具与 read schema 完整。
- Codex 重启后已加载缓存版本 `0.1.0+codex.20260713091610`；四个 Monitor 工具在当前任务可见，并已用于上述真实板卡验收。

## 后续顺序

1. Monitor 软件、CI 和真实板卡门禁均已通过；由用户审核并决定是否合入 `main`。
2. SQLite schema 与仓储层。
3. 日志查询增强。
4. 工程路径重绑定、项目合并、导出、导入和完整性校验；该迁移体系继续暂停。

当前任务更新文档并提交功能分支后暂停，不自动开始 SQLite、日志查询增强或迁移体系。
