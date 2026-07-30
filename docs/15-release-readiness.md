# 正式发布前状态与检查表

更新时间：2026-07-30（Asia/Shanghai）

## 结论

任务书范围内的通用 ESP MCP 工具链已经形成发布候选：

- 6 项基础能力、6 项提高能力；
- 12 套公开 prompts；
- 48 个 MCP tools；
- 12 个 MCP resources；
- 独立 Conda 环境与 `mpremote 1.28.0`；
- project-scoped SQLite schema v3 正式 runs/events/raw/error 查询；
- Windows/Linux、Python 3.10/3.12 自动化测试矩阵；
- MicroPython 文件、执行、停止、reset、日志、错误、GPIO、回归和性能实板证据；
- 无蜂鸣器 UART-only ESP-IDF 构建、烧录、监控、从地址 0 写回完整 4 MiB 备份，
  以及 MicroPython/mpremote 恢复复核闭环。

当前状态是“待正式发布”，不是“已经发布”。本文件区分已经确认的能力、仍需执行的
发布动作和明确排除的业务固件范围。

## 当前候选构成

实现工作树为 `index/main`，测试工作树为 `index-test/test`。本轮候选包含：

1. `examples/esp_idf_uart_smoke/sdkconfig.defaults` 禁用编译日期/时间元数据。
2. UART-only 示例说明补充产物漂移根因、修复和烧录前哈希门。
3. test 分支增加 `CONFIG_APP_COMPILE_TIME_DATE=n` 与说明合同。
4. README、CHANGELOG、开发状态、路线图、能力矩阵、Bug 学习记录和本发布清单。

仓库内 `.codex-plugin/plugin.json` 的现有差异属于用户自有改动，不在本候选范围，
不得暂存、覆盖或随发布提交。

## 软件验证

本轮行为候选已经完成三层本地门禁：

```text
Python 3.12.13
pytest 9.1.1
UART-only 专项：5 passed in 0.30s
main 全量：120 passed in 61.67s
test 跨工作树：587 collected / 583 passed / 4 skipped / 0 failed in 332.84s
```

4 项 skip 均来自当前 Windows 账户无法创建普通文件 symlink 测试夹具：
Flash 1 项、历史 capture 1 项、Monitor 2 项；不是功能失败。test 工作树通过

```powershell
$env:ESP_MCP_SOURCE_ROOT = "C:\Users\16224\Desktop\ShanDong University\summer_holiday_1\index"
python -m pytest
```

显式加载 main 工作树源码，因此该门禁覆盖当前
`CONFIG_APP_COMPILE_TIME_DATE=n`、UART-only README 和对应测试合同。发布文档本身
另以 diff、证据一致性和 `git diff --check` 复核；pytest 结果不被扩写为对全部文字
陈述的自动验证。

远端 `main@1e09789` 与 `test@8ba27b5` 的既有 Actions 已通过，但它们不包含当前未提交
候选。只有候选精确提交并推送后的新 Actions 才能作为正式发布门禁。

## 实板封口证据

### KEY1

- 松开：`gpio_status_20260730_191534_37959ed8`，GPIO34=`1`。
- 按住：`gpio_status_20260730_191640_87385e23`，GPIO34=`0`。
- 两次均为只读查询，`mode_changed=false`；映射已记录为
  `board_test_confirmed / confidence=1.0`。

这证明 GPIO34 与实体 KEY1 的 active-low 两态关系，不证明按键去抖、长期可靠性或
电气波形。

### 确定性 UART-only 镜像

两次普通增量构建：

- `build_20260730_192625_8ef67c38`
- `build_20260730_192812_b9042a6e`

均为 `target_plan=build`、`fullclean_planned=false`、
`set_target_planned=false`、`target_verified=true`。构建日志不持久化三段历史哈希；
第二次构建后的最终烧录前复核确立的验收输入为：

| 地址 | 文件 | 字节数 | SHA-256 |
| --- | --- | ---: | --- |
| `0x1000` | `bootloader.bin` | 26,720 | `1BFB7F309DB6C232FB20AF613B3A2E0E0570C615DD41DEADFF213F6C5015ABE8` |
| `0x8000` | `partition-table.bin` | 3,072 | `7F00B6C042A89B15B0CAC534F82ED988CAF29278FF5700B0C511EB1B5BB7C820` |
| `0x10000` | `esp_idf_uart_smoke.bin` | 176,816 | `4017628FA6BDFD2453C6518299F60D0ACF2A15BD3C43D466DE7CA8EF365D8CA2` |

第一次闭环暴露 `CONFIG_APP_COMPILE_TIME_DATE=y` 会让 `idf.py flash` 增量重编
`esp_app_desc.c`，把 `__DATE__` / `__TIME__` 写入镜像并改变 app SHA-256。安全处理是
停止 UART 验收，并按当次已经包含恢复的精确授权写回新备份。该漂移摘要来自当时实时
工具输出，没有进入 SQLite/JSONL completion payload。修复后 defaults 设置
`CONFIG_APP_COMPILE_TIME_DATE=n`，但每次正式烧录仍必须重新计算最终实际写入的全部
分段哈希；任何变化都必须停止。

### 第二次 UART-only 闭环

- 新备份：`backup_flash_20260730_193625_9ba0d34b`，4,194,304 字节；
  备份 SHA-256
  `F28649C0194A67C951E5DFCB8BC690B526ABD1CFDA50D94BE2027F5DCA66CE89`。
- 烧录：`flash_20260730_193819_dfa2a884`；只写目标区段，没有调用整片擦除工具。
- 启动捕获：`reset_20260730_193902_3172efe8`，包含 READY 和 HEARTBEAT `0,1`。
- 固定捕获：`serial_capture_20260730_193904_5fdcba4c`，115200 baud、7 秒、
  476 字节、无结构化错误；原始日志 SHA-256
  `C0411F143FDF459800DCB06C335ADA57FABBF285EC4C6B09E248DE672F9ED50C`，
  包含连续 HEARTBEAT `2..8`。
- 恢复：`restore_flash_20260730_193928_f67fad17` 从地址 0 写回完整
  4,194,304 字节；随后 MicroPython Raw REPL 和 mpremote 文件访问恢复，但没有执行
  恢复后的完整 4 MiB read-back。

合并启动与固定捕获后，实板证据为 READY 和连续 HEARTBEAT `0..8`。这证明 UART-only
应用有序输出，不证明 reset 工具独立建立了复位因果，也不证明物理 GPIO 电平。

### 临时文件清理

删除前重新列目录后，仅逐个删除以下授权路径：

- `/esp_mcp_reg_hardware_readonly_gpio34_key_read.py`
- `/esp_mcp_reg_negative_intentional_failure.py`
- `/esp_mcp_reg_safe_runtime_smoke.py`
- `/mcp_acceptance_payload.txt`

四次删除均成功且清理无错误；本次会话最终实时工具返回只包含 `/boot.py`。SQLite/JSONL
摘要只持久化了四次删除终态和最终列目录命令成功，没有保存该目录 stdout。没有格式化
文件系统、没有删除其他路径、没有操作 GPIO25/PWM/蜂鸣器。

## 业务固件范围说明

按用户确认，蜂鸣器瞬时电流/掉电属于业务固件问题，不是 ESP MCP 工具链缺陷；本项目
不再安排蜂鸣器专项，该项不阻塞提交、Actions、Marketplace、标签或 Release。UART-only
源码和本轮工具调用没有主动配置 GPIO25、LEDC、PWM 或蜂鸣器，但这只能说明工具链验收
范围，不能扩展为 Boot ROM、复位线路、板级电气瞬态或蜂鸣器供电行为的验证。

## 正式发布前必须完成

- [x] 精确复核 main/test diff，继续排除 `.codex-plugin/plugin.json` 用户差异。
- [x] 运行最终 UART-only 专项、main 全量和 test 跨工作树全量测试。
- [ ] 分步提交 main 文档/实现和 test 合同；提交正文写明 Cause/Fix/Validation/Scope。
- [ ] 推送前把已固定的 main 合入 test，清除跨工作树源码覆盖后，在 test 分支自身运行
  全量门禁；当前 test 工作树尚未同步 main 的新 defaults/README，不能直接作为远端
  test 候选推送。
- [ ] 推送 main/test，并等待新的 Windows/Linux、Python 3.10/3.12 Actions 全部成功。
- [ ] 若仅发布 GitHub 仓库，记录 Marketplace 不适用；若发布 Codex 插件，则更新个人
  Marketplace 源并使用一次 cachebuster，不得直接改安装缓存。
- [ ] 若发布 Codex 插件，用户重启 Codex 后核对活动插件版本和
  `48 tools / 12 resources / 12 prompts`；仓库单独发布时不适用。
- [ ] 冻结版本号、发布说明和 Git tag；在此之前保持 `[Unreleased]`。
- [x] 已记录蜂鸣器问题的业务固件范围、非工具链缺陷和非发布门禁结论，同时保留 UART
  证据不得扩展为 GPIO/蜂鸣器电气验证的边界。

## 发布时不得声称

- 不得把 pytest 或 UART 文本当作真实 LED、蜂鸣器、功耗或供电证据。
- 不得声称恢复后重新读回的完整 flash SHA-256 等于备份摘要；本轮执行的是受控恢复和
  MicroPython/mpremote 复核，没有第二次完整 4 MiB read-back。
- 不得把首次 host build 的 176,896 字节 `AA9E9AFA...09A9` 写成本次实板烧录镜像；
  本次验收的是 176,816 字节 `4017628F...8CA2`。
- 不得把当前远端绿灯写成覆盖尚未提交的候选。
