# esp-mcp-toolchain v0.1.0 发布说明

日期：2026-07-30（Asia/Shanghai）

> 状态边界：本源码快照已冻结为首个 GitHub 版本 `v0.1.0`，但 final branch-local
> 全量门禁、main/test 推送后的新 GitHub Actions、tag 和正式 Release 仍待完成。
> 本文件是发布说明草案，不表示 tag 或 Release 已经存在。

## 发布范围

`v0.1.0` 完成任务书要求的 6 项基础能力和 6 项提高能力：

- 基础：文件传输、程序执行和停止、微控制器复位、串口监控、运行日志检索、
  MicroPython 错误报告。
- 提高：固件自动烧录、远程文件管理、GPIO 状态在线查询、硬件信息自动采集、
  自动化回归测试、执行性能分析。

最终公开 MCP 面为：

- 12 套 prompts；
- 48 个 tools；
- 12 个 resources。

独立 Conda 环境提供 `mpremote 1.28.0`；启动器找不到专用解释器时不会静默退回全局
Python。

## SQLite v3 正式日志链

project-scoped SQLite schema v3 是 runs、events、raw logs 和 errors 的正式查询权威。
completion event 与 raw/error artifacts 支持原子写入、稳定身份、严格幂等、历史对账
和有界读取。JSONL 保留为审计镜像与显式迁移入口，不替代 SQLite 查询权威。

## 确定性 UART 构建修复

Cause：`CONFIG_APP_COMPILE_TIME_DATE=y` 时，`idf.py flash` 可在烧录前增量重编
`esp_app_desc.c`，把 `__DATE__` / `__TIME__` 写入应用镜像，导致已经检查过的 app
SHA-256 在实际烧录前发生变化。

Fix：UART-only 示例的 defaults 固定
`CONFIG_APP_COMPILE_TIME_DATE=n`，并用 test 合同锁定该设置与说明。正式烧录仍必须在
每次操作前重新计算最终实际写入的全部分段哈希；任一分段发生漂移就停止，不把旧摘要
当作当次烧录证据。恢复只能在精确授权范围内进行。

本轮最终验收 app 为 176,816 字节，SHA-256：

```text
4017628FA6BDFD2453C6518299F60D0ACF2A15BD3C43D466DE7CA8EF365D8CA2
```

## 实板封口证据

- KEY1：GPIO34 松开为 `1`、按住为 `0`，两次只读查询均为
  `mode_changed=false`。
- UART-only：COM3 启动捕获和 7 秒固定捕获合并后得到 READY 与连续
  HEARTBEAT `0..8`。
- 恢复边界：烧录前备份完整 4 MiB flash，备份 SHA-256 为
  `F28649C0194A67C951E5DFCB8BC690B526ABD1CFDA50D94BE2027F5DCA66CE89`；
  验收后从地址 0 写回完整 4,194,304 字节，并确认 MicroPython Raw REPL 和 mpremote
  文件访问恢复。没有执行恢复后的完整 4 MiB read-back，因此不声称恢复后全片摘要与
  备份摘要相同。
- 临时文件清理：以下四个明确授权路径已逐个删除：
  `/esp_mcp_reg_hardware_readonly_gpio34_key_read.py`、
  `/esp_mcp_reg_negative_intentional_failure.py`、
  `/esp_mcp_reg_safe_runtime_smoke.py` 和 `/mcp_acceptance_payload.txt`。
  本次会话最终实时工具返回只列出 `/boot.py`。

## 本地门禁

源码冻结前的最终本地结果为：

```text
UART-only 专项：5 passed in 0.30s
main 全量：120 passed in 61.67s
test 跨工作树：587 collected / 583 passed / 4 skipped / 0 failed in 332.84s
```

4 项 skip 是当前 Windows 账户无法创建普通文件 symlink 测试夹具：Flash 1 项、
历史 capture 1 项、Monitor 2 项；不是功能失败。test 跨工作树结果不替代尚待执行的
final branch-local 全量，也不替代推送后新的 Windows/Linux、Python 3.10/3.12
GitHub Actions。

## 蜂鸣器与业务固件边界

按用户确认，蜂鸣器瞬时电流/掉电属于业务固件问题，不是 ESP MCP 工具链缺陷；本项目
不再安排蜂鸣器专项，该项不阻塞 `v0.1.0`。UART-only 源码和本轮工具调用没有主动配置
GPIO25、LEDC、PWM 或蜂鸣器，但这不证明蜂鸣器、供电、复位线路或板级电气瞬态状态。

## 发布边界

本次是 GitHub-only 发布。个人 Marketplace 更新、cachebuster、安装缓存写入和插件
重启验收不适用；当前已安装的 `0.1.0+codex.20260730084223` 只是历史插件背景，不是
本源码快照的发布证据。

只有以下剩余门禁全部成功后，才创建 `v0.1.0` tag 和正式 Release：

1. 提交并同步本次版本元数据文档；
2. test 分支 final branch-local 全量通过；
3. main/test 精确推送；
4. 新 GitHub Actions 矩阵全部成功。

完整验证证据和禁止扩大声明的边界见
`docs/15-release-readiness.md`。
