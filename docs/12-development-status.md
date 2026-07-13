# 当前开发状态

更新时间：2026-07-13 21:57（Asia/Shanghai）

## 当前分支

- 分支：`main`
- 目标：修复后台串口 Monitor 在 CH9102 实板上出现的污染读取；不在本任务中启动 SQLite、日志查询增强或项目迁移体系开发。
- 当前状态：修复实现、跨分支全量门禁和真实板卡门禁已通过，等待提交、推送和新版插件重载。

## 已完成的软件验证

- 四个 MCP 工具已注册：start、stop、status、read。
- 状态机覆盖 `STARTING`、`RUNNING`、`STOPPING`、`STOPPED`、`FAILED`、`DISCONNECTED`。
- Monitor 启动时固定项目和日志路径；切换活动项目不会改变正在运行会话的写入目标。
- 读取接口使用 `seq` / `after_seq` 游标、有界缓冲、`dropped_before_seq` 和最大返回字节数。
- 原始串口字节按块持久化，支持 text、base64 和 both 三种读取表示。
- 串口使用非阻塞模式，读取前查询 `in_waiting`，单次最多读取 1024 字节；无数据时等待 5 ms，避免固定 `read(4096)` 在当前 Windows CH9102 场景返回污染缓冲。
- 跨进程锁、进程所有权、端口身份记录、stdin EOF 清理、内部异常清理和强制终止后的陈旧锁恢复已有自动化测试；另有回归测试保证活跃的其他 MCP 进程不会被误判为陈旧会话。
- 当前源码 MCP 枚举结果：43 tools / 12 resources / 4 prompts。
- 当前跨分支全量测试结果：101 passed；Monitor 专项：29 passed。测试来自 `index-test`，实现来自 `index`。
- GitHub Actions 已确认提交 `60a3a83` 的 Windows/Linux、Python 3.10/3.12 四个任务全部成功；4 条 Node.js 20 弃用警告来自 Actions 运行时，不是测试失败。
- 功能分支头 `962a382` 的 Windows/Linux、Python 3.10/3.12 四个任务也已全部成功。
- `main` 合入提交 `e67dd7f` 的四个矩阵任务全部成功，用时 1 分 28 秒。

## 硬件门禁

- 重新枚举后确认 `COM3` 为 `USB-Enhanced-SERIAL CH9102`，VID:PID=`1A86:55D4`、序列号 `54AC011277`、location=`1-8`，`likely_esp=true`；`COM6`、`COM7` 仍是蓝牙串口，未打开。
- `monitor_20260713_173011_acd850be` 在 `115200` 捕获两条 ESP-IDF 启动记录，共 7,306 字节，包含 `POWERON_RESET`、项目名 `esp_idf_key_led_buzzer` 和 ready 信息。
- 首次读取返回 `seq=1..2`、`next_after_seq=2`；随后使用 `after_seq=2` 立即读取返回空记录，没有重复旧内容，也没有缓冲丢弃。
- 停止结果为 `STOPPED`、`cleanup_complete=true`、`worker_alive=false`，7,306 字节全部持久化，`last_error=null`。
- 随后以 `monitor_20260713_173300_7c343c91` 立即重新打开并停止同一 `COM3`，确认句柄和锁已释放。
- 当前 ESP-IDF 固件的 UART0 运行时控制台 `115200` 已作为 `board_test_confirmed` 事实增量写入当前项目硬件映射；不把该固件参数表述为所有固件的板级默认值。
- 后续全量复测发现原固定大块读取存在污染：`monitor_20260713_194149_b9df143f` 有非法 UTF-8 记录，`monitor_20260713_194522_9bbed9a6` 又记录 215,952 字节旧片段和异常数据，因此旧门禁不能单独作为数据完整性结论。
- 修复后自动复位门禁 `monitor_20260713_201602_a7c32955` 捕获 3,653 字节、62 条记录，`decode_error=0`、单条最大 68 字节，并完整停止和持久化。
- 最终按键门禁 `monitor_20260713_215648_f1366541` 捕获两次独立按压，每次均有完整 5 组 LED/蜂鸣器 on/off、结束和释放日志；共 1,466 字节、41 条记录，无解码错误、替换字符、缓冲丢弃或未持久化字节。

## 插件同步

- 修复后的仓库源码通过 plugin validator，源码版本为 `0.1.0+codex.20260713135819`。
- 当前任务仍加载缓存版本 `0.1.0+codex.20260713091610`；新版 marketplace 源、缓存和 stdio MCP 将在提交后同步验证。
- Codex 重启后还需确认 `0.1.0+codex.20260713135819` 在当前模型工具面可见，并使用新版工具做一次短验收。

## 后续顺序

1. SQLite schema 与仓储层；本任务不自动开始。
2. 日志查询增强。
3. 工程路径重绑定、项目合并、导出、导入和完整性校验；该迁移体系继续暂停。

Monitor 修复的本地软件与硬件门禁已完成；下一动作是提交并推送 `main` / `test`、更新个人插件，然后重启 Codex 验收新版工具。
