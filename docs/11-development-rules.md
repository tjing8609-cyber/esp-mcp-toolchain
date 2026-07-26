# 开发规范

本文档记录项目后续开发的硬性流程。目标是让实现、测试、硬件风险和文档状态始终可追踪。

## 分支规则

- `main` 是受保护的软件稳定分支，只接收已经通过对应软件门禁的实现和文档；未完成实板验收的硬件能力必须保持 `[Unreleased]` 和明确的“未实板验证”状态。
- 产品实现和文档在 `index` 工作树提交到 `main`；提交前必须先通过计划审核和对应门禁。
- `test` 分支的分支专属提交只允许修改测试文件、测试目录和验证规则；先同步最新 `main`，再补充能复现问题或约束新功能的测试。
- 门禁从 `index-test` 运行测试，并显式加载 `index` 中待提交的主线源码；修复前测试应能稳定失败，修复后必须全量通过。
- `main` 提交后再次同步到 `test` 并运行全量测试；依赖真实硬件的能力在声明硬件稳定或发布插件前还必须通过对应硬件门禁。
- 不在功能尚未通过对应软件门禁时把它直接提交或合并到 `main`；mock 门禁只能确认软件合同，不能生成实板结论。

## 测试规则

- 当前测试目录为 `toolchain/tests/`。
- 当前全量测试入口为：

```powershell
python -m pytest
```

- 双工作树门禁必须从 `index-test` 显式加载 `index` 源码，推荐使用 test 分支脚本：

```powershell
.\scripts\run_cross_worktree_tests.ps1 `
  -SourceRoot ..\index `
  -PythonPath C:\Users\16224\anaconda3\envs\esp-mcp-toolchain\python.exe
```

- 门禁必须断言实际加载的 `esp_mcp_toolchain` 位于 `SourceRoot/toolchain`；只修改 `PYTHONPATH` 但仍导入 test 工作树源码不算有效验证。
- 每次新增或修改测试后必须重新记录 pytest 的 collected/failed/passed；不得沿用修改前的历史数字。只有最后一次完整跨工作树运行的 passed 数可以写成当前门禁结果。

- GitHub Actions 必须至少覆盖 Windows 与 Linux，以及项目支持的 Python 版本。
- 新增工具、后端、资源、提示词、存储逻辑或硬件流程时，必须在 `test` 分支新增或更新对应测试，并对 `main` 待提交源码执行门禁。
- 修复 bug 时，优先补充能够复现问题的回归测试。
- 全量测试未通过时，不得合入 `main`，也不得作为稳定能力写入发布说明。
- 依赖串口的测试优先使用可控假串口和独立子进程覆盖并发、退出、断连和故障注入；假串口测试不能替代真实板卡验收。

## SQLite 日志规则

- SQLite 是 runs/events 的正式查询源，JSONL 只能作为审计镜像和迁移输入；查询工具不得静默回退到 JSONL。
- schema 或仓储变化必须覆盖 fresh init、v1 迁移、并发 init、并发 sequence、UUID 严格幂等、run 终态、selected_port 冲突和跨项目隔离。
- 并发 init 必须覆盖两个线程和两个独立进程同时创建同一个尚不存在的数据库，并覆盖 WAL 设置发生在 `BEGIN IMMEDIATE` 之前的锁竞争。
- legacy JSONL 测试必须覆盖重复导入、复制文件去重、并发 marker 以及原生 running run 不被提前结束。
- 时间过滤参数必须与写入时间使用同一 UTC 规范化规则；legacy 未知 level 不得使可恢复历史文件永久无法导入。
- 后台 Monitor 必须验证启动项目 A、切换到 B、终止后 run/events 仍全部落在 A。
- 动作或状态变更前日志不可用时必须阻止执行；完成后的日志故障不得覆盖业务结果，尤其不能诱导调用方自动重试烧录、擦除、删除、恢复、端口选择或配置写入。
- 对 port selection、配置写入等非硬件状态变更同样应用后置日志失败语义：业务变更不得回滚或被误报为未执行，但必须返回 `logging_persisted=false` 和 `logging_warning`。
- SQLite 层改动不需要真实硬件门禁；涉及硬件工具时通过 mock 验证生命周期，只有硬件执行逻辑本身变化才进入真实板卡门禁。

## 硬件操作规则

- MCP 会话必须先通过 `project_context_select` 绑定 Codex 工作区，不得把插件安装目录当作用户工程目录。
- 当前活动项目是 MCP 服务进程级状态，每个新工作流必须重新选择并核验。
- 普通项目级工具不得并发使用同一服务进程操作多个工作区。
- 后台 Monitor 是明确例外：启动时必须捕获不可变的 `project_id`、`workspace_root` 和日志目录，之后不得重新读取全局“当前项目”。切换活动项目不能改变已运行 Monitor 的写入目标。
- hardwork、memory、日志、产物、数据库和串口配置必须按 `project_id` 隔离；新增存储功能必须包含跨工程不可见测试。
- 项目运行时数据必须放在稳定的用户数据根目录，不能依赖会随 cachebuster 替换的插件缓存路径；旧缓存迁移不得覆盖稳定目录中的已有文件。
- 用户在 Codex 对话框上传硬件资料后，由 `hardwork_upload_attachment` 归档；首次上传必须完成资料阅读和映射提交后才能调用硬件相关工具。
- 首次映射只做安全开发所需的基础初始化；后续问答、查图或实板操作发现新事实时，必须增量回写，不能只在回复中展示。
- 增量更新必须保留无关映射、记录来源和证据等级；关键字段冲突不得静默覆盖。
- 不猜测串口、GPIO、芯片型号、烧录方式和硬件限制。
- 串口必须通过枚举或已记录的稳定事实确认；端口名称变化时还要核对 VID、PID、序列号、location 和描述等可用身份字段。
- GPIO 和板载外设必须先查阅 `hardwork/` 中的板卡资料；资料不足时，先补资料或做低风险探测。
- 烧录、擦除、删除板端文件、full clean 等高风险动作必须保留显式确认门。
- 整片 BIN 恢复必须校验项目内路径、文件大小和 SHA-256，并通过 `esp_restore_flash(confirm=True)` 执行。
- 高风险测试前要说明可以做什么、不能做什么、需要查资料或测试后才能做什么，以及需要用户执行什么。

## Monitor 退出规则

- 正常 server shutdown、stdin EOF、可捕获的 `SIGINT` / `SIGTERM`、支持的平台退出事件、`atexit`、Monitor 线程异常和主线程异常都应触发有界清理。
- 有界清理必须停止 Monitor、刷新可写日志、关闭串口并释放进程内和跨进程锁；清理不得无限等待。
- 对 `TerminateProcess`、进程崩溃、断电等强制终止，不承诺清理逻辑能够执行。此时依赖操作系统关闭串口句柄，并在下次启动时识别和清理陈旧会话锁。
- 必须保留进程级测试：子进程启动 MCP Server 和假串口 Monitor，关闭 stdin，验证服务器限时退出、串口可重新打开且没有陈旧锁；另测强制终止后的恢复。

## 文档规则

- README 只维护稳定的用户入口、能力概览、当前里程碑和最近验证结果，不为每个细小提交堆叠重复说明。
- `CHANGELOG.md` 记录发布和未发布的用户可见变化。
- `docs/10-development-roadmap.md` 记录路线和顺序；`docs/12-development-status.md` 记录当前分支、门禁、阻塞和下一动作。
- 影响架构、接口语义或协作流程的决定写入 `docs/adr/`。
- 同一天内 README 开发日志中的不同里程碑按时间分开记录。
- 开发规范、权限策略、工具规格和路线图有变化时，同步更新 `docs/` 中对应文档。
- 测试结果要记录全量命令和通过数量；提交消息记录该提交的具体实现细节。

## 合入门槛

功能提交并发布前必须满足：

- `main` 工作树包含实现和必要文档，`test` 工作树包含对应回归测试；两个工作树都没有无关改动。
- `python -m pytest` 本地全量通过，GitHub Actions 通过。
- 软件候选可以在完整 mock/跨工作树门禁通过且明确标注“未实板验证”后进入 `main`；依赖真实硬件的能力在发布插件或声明硬件稳定前必须完成目标板卡验收。
- README、CHANGELOG、开发状态页和必要 ADR 已同步。
- 高风险工具仍保留确认机制。
- 运行时产物、日志、备份、构建目录和板端临时文件没有被提交。

## 任务书能力与插件发布规则

- 任务书公开 prompt 固定为 6 项基础 + 6 项提高，共 12 套；既有公开名
  `debug_error`、`build_flash_monitor`、`review_hardware_context` 保持，其余旧名称不注册，
  也不通过隐藏兼容表解析。
- 每套 prompt 必须绑定明确工具、成功证据和安全边界，不能只写通用说明。
- `esp_program_stop` 不能发送 Ctrl-D 或复位命令；只有观察到 `>>>` 时才能确认停止。
  返回值只能证明 `reset_command_sent=false`，必须同时保留
  `physical_reset_excluded=false`，不得把串口打开过程描述为物理复位不可能发生。
- GPIO 状态查询不得切换 pin 模式，并且进入 raw REPL 前必须取得
  `allow_program_interrupt=true`。硬件信息默认 passive，只接受当前已枚举串口并合并
  reviewed mapping；MicroPython runtime 探测同样需要 `allow_program_interrupt=true`。
- 板上回归只运行显式路径，执行前必须取得 `confirm_execution=true`；性能分析必须取得
  `confirm_repeated_execution=true` 并提示重复副作用，同时标明不是 sampling profiler。
- 原始错误日志读取必须限定在活动项目的 logs 根目录，并设置最大扫描字节数。
- `.mcp.json` 只能通过 `scripts/run_mcp_server.py` 定位独立
  `esp-mcp-toolchain` Conda 解释器；找不到时应失败，不得静默退回全局 Python。
- 插件更新只修改个人 marketplace 源，使用 `plugin-creator/scripts/update_plugin_cachebuster.py` 和 `validate_plugin.py`；不得直接覆盖安装缓存、同时改仓库 manifest，或重复追加 cachebuster。
- 本地 marketplace 重装后必须在新 Codex 任务核对工具、资源和 prompt 数量，旧任务中的 MCP 工具面不作为新版缓存证据。
