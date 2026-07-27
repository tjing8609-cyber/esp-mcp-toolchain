# 当前开发状态

更新时间：2026-07-27 22:27（Asia/Shanghai）

## 当前分支

- 实现工作树：`index` / `main`。
- 测试工作树：`index-test` / `test`。
- 当前目标：完成任务书 6 项基础能力和 6 项提高能力，并形成 12 套 prompts + 48 个小工具的插件架构。
- 当前状态：启动器、串口生命周期、reset、Raw REPL/程序停止/错误检测和 12 套任务书能力已完成软件实现；reset 证据持久化、4 MiB 构建配置、性能结果持久化、分层回归套件和 Flash 主机路径安全已依次完成本地软件门禁。当前正在提交 Flash 路径安全切片；远端 CI、Marketplace 与重启后插件验收尚未完成，本轮也没有重新访问板卡。

## 本轮已完成实现

- 在独立 Conda 环境安装并验证 `mpremote 1.28.0`；解释器为 `C:\Users\16224\anaconda3\envs\esp-mcp-toolchain\python.exe`，未修改全局 Python。`.mcp.json` 经 `scripts/run_mcp_server.py` 定位该环境，找不到专用解释器时不会静默使用全局 Python。
- 新增 `esp_program_stop`：只发送两次 Ctrl-C，不发送 Ctrl-D 或复位命令；只有观察到 `>>>` 才确认停止。结果只证明 `reset_command_sent=false`，并明确 `physical_reset_excluded=false`。
- MicroPython 异常检测形成闭环：
  - raw REPL stdout/stderr 自动解析；
  - 固定串口 capture 支持跨 chunk Traceback；
  - 后台 Monitor 暴露 `detected_error` 并只写一次结构化 SQLite 错误事件；
  - `esp_error_parse_log` 读取结构化报告和有界原始日志，路径强制限制在当前项目 logs 根目录。
- 新增四个提高工具：
  - `esp_gpio_status`：raw REPL 查询前要求 `allow_program_interrupt=true`；
  - `esp_hardware_info`：passive 只接受已枚举串口并合并 reviewed mapping，runtime 探测要求 `allow_program_interrupt=true`；
  - `esp_regression_test`：执行精确测试路径前要求 `confirm_execution=true`；
  - `esp_performance_profile`：重复执行前要求 `confirm_repeated_execution=true`。
- 工具注册表从 43 增加到 48；resources 保持 12。
- 公开 prompt 重组为任务书 12 套，每套包含目标、前置检查、工具顺序、成功证据、安全边界、失败处理和最终报告。
- 既有任务书公开名 `debug_error`、`build_flash_monitor`、`review_hardware_context` 保持；`hardware_context_review`、`memory_write_policy`、`write_project_memory` 等其余旧名称不注册。
- 日志完成 payload 增加异常、停止、回归和性能摘要字段，便于 SQLite 审计。
- 串口基础层统一采用打开前/后压低 DTR/RTS 的生命周期；reset 分离记录打开副作用、动作、输出和清理证据。
- Raw REPL 只在确认严格 `OK + stdout EOT + stderr EOT + >` 帧后报告完成，并验证源码、Ctrl-C 与退出写入没有短写。
- `erase_flash` 改为复用统一受管子进程执行器，显式固定 `--before default_reset --after hard_reset`；失败映射保留子进程输出和清理元数据，`confirm=True` 高风险门不变。
- `esp_file_upload`、`esp_file_download` 两个后端及 `esp_run_file(path_type="local")` 统一通过 `safe_project_path()` 解析主机路径；相对路径绑定活动 workspace，越界输入在文件或后端副作用前返回 `unsafe_local_path`，成功元数据返回规范绝对路径。
- `esp_backup_flash` / `esp_restore_flash` 共用 workspace + 当前项目 flash artifact 的规范
  路径边界；artifact/staging reparse、外部绝对路径和 `..` 逃逸在后端前拒绝。
- 备份统一先写当前项目 `backup-staging` 的 UUID partial；输出和 staging 目录身份在长任务后
  复核，final 只做 create-if-absent 发布。发布冲突/不支持 hard link 时保留已验证镜像并
  返回 `recovery_path`。
- 恢复保持确认门优先，随后建立每次调用独占的项目 UUID staging，核对源身份、长度和
  SHA-256；POSIX staging 收紧为只读，Windows 依赖独占运行目录与重复身份/摘要复核，
  正常和错误路径均记录清理结果；清理前的权限/I/O 检查失败也只形成 cleanup error，
  不覆盖原始操作结果。

## 本地验证

- 测试工作树通过 `ESP_MCP_SOURCE_ROOT` 显式加载实现工作树源码。
- 提示词/提高工具/架构专项：`25 passed`。
- 串口生命周期、reset、Raw REPL、程序停止和错误检测关联门禁：`62 passed`。
- 显式加载 `main` 源码的完整候选门禁：`226 passed in 29.35s`。
- main→test 同步后，test 分支自身源码的标准全量门禁：`226 passed in 27.66s`。
- P0 main 本地全量：`104 passed in 14.70s`；main/test 共 8 个远端矩阵 job 全部成功。
- `erase_flash` P1 修复后：后端专项 `6 passed`、擦除工具专项 `8 passed`、main 全量 `104 passed in 13.89s`、跨工作树全量 `228 passed in 27.76s`。
- `erase_flash` P1 远端：[main run 30211040021](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30211040021) 与 [test run 30211040067](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30211040067) 共 8 个 job 全部成功。
- Monitor STARTING 测试修复：失败用例独立进程重复 `20/20` 通过；main 全量 `104 passed in 16.67s`。失败证据为 [main run 30211564537](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30211564537) 的 Windows/Python 3.10 job；修复后远端矩阵待当前提交验证。
- 相对路径合同在旧 `main@5985230` 上为预期的 `15 failed in 2.60s`；修复后 main 专项 `32 passed in 4.15s`、test 跨工作树专项 `48 passed in 9.26s`、提交前 main 全量 `119 passed in 17.64s`、test 跨工作树全量 `243 passed in 31.50s`。
- 已安装插件直接枚举为 `48 tools / 12 resources / 12 prompts`；本次路径修复的新 Marketplace 版本尚未生成。
- Flash 路径安全初始红灯为 `8 failed, 15 passed`；第一轮绿灯 `26 passed` 后经过独立
  复审继续关闭 reparse、恢复源 TOCTOU、备份父目录替换、清理异常和发布失败数据丢失。
  最终定向门禁 `36 passed, 1 skipped in 2.67s`；main 全量 `119 passed in 15.53s`；test 显式
  加载 main 源码 `287 passed, 1 skipped in 33.01s`。skip 仅因本机没有目录 symlink
  创建权限，同一拒绝分支另有确定性测试。
- 本次路径软件测试使用 mpremote / Raw REPL mock、临时项目目录和临时 SQLite，不访问真实开发板；下节单独记录此前已执行的实板动作。

## 安全与实板状态

- 当前项目上下文为 `summer-holiday-1-2268049d8188`。
- 已审查映射为 ESP32-D0WD-V3、GPIO34 KEY1、GPIO32 低有效 LED、GPIO25 PWM 蜂鸣器、UART0 115200；2026-07-27 的 MicroPython runtime 探测已作为新的 board-test 事实增量写回。
- 授权前备份 4,194,304 字节，SHA-256 为 `23F1A7424286FED0BA59A1E6883DB4195CDF344F696B628C314892B24585B6B9`；`erase_flash_20260727_131837_f672becc` 成功，`restore_flash_20260727_131918_88af58ec` 成功恢复并校验官方 MicroPython v1.28.0 BIN。
- 动作后捕获到 MicroPython v1.28.0 banner；运行时信息、20 条串口 Monitor 标记和三个临时文件的上传/读取/列表通过。hard reset 工具仍保留 `reset_confirmed=false` 与 `output_causality_confirmed=false` 的严格边界。
- 相对下载返回成功但实际写入版本化安装缓存，因此 `remote_file_management` 尚未通过；程序停止、异常解析、GPIO34 只读、板上回归、性能、软复位、临时文件删除和日志闭环也尚未执行。
- `build_flash_monitor` 只支持 ESP-IDF build→flash→monitor 链，本次 Raw BIN 恢复不能作为其通过证据；若执行需另行授权，恢复 MicroPython 还需再次授权。

## 插件发布状态

- 当前仓库的既有本地插件 manifest 差异不属于本次提交；个人 marketplace 源已更新为 `0.1.0+codex.20260726165544`。
- Marketplace 源通过 plugin validator、main 发布测试 `104 passed in 14.19s` 和 `48 tools / 12 resources / 12 prompts` 直接枚举。
- 用户重启后已确认安装缓存为 `0.1.0+codex.20260726165544` 并直接核对 48/12/12。按用户约定，本次修复只会更新 Marketplace 源，不直接改安装缓存；当前运行插件仍未包含路径修复。

## 待完成

1. 提交 Flash 路径安全的 main 实现/文档和 test 合同，合并后复跑 test 自身源码全量并推送双分支。
2. 确认 main/test 各四个 Windows/Linux、Python 3.10/3.12 远端 job。
3. 只同步 Marketplace 源，运行发布测试、validator 与 48/12/12 枚举，再执行一次 cachebuster 更新。
4. 用户重启后核对新版插件，并用新的项目内输出名重复相对下载。
5. 下载通过后继续剩余 MicroPython 实板能力；删除和新的烧录/恢复操作仍按精确动作单独确认。
