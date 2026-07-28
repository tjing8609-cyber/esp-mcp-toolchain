# Changelog

本文件记录用户可见的项目变化。版本发布后再把 `[Unreleased]` 内容归入对应版本。

## [Unreleased]

### Added

- 新增任务书 6 项基础 + 6 项提高的 12 套公开 prompts；公共名称严格固定为 12 个，保持 `debug_error`、`build_flash_monitor`、`review_hardware_context`，不注册其余旧名称。
- 新增 `esp_program_stop`、`esp_gpio_status`、`esp_hardware_info`、`esp_regression_test`、`esp_performance_profile`，源码工具面目标为 48 tools / 12 resources / 12 prompts。
- 新增独立 Conda 启动器；MCP Server 使用 `esp-mcp-toolchain` 环境及其中已验证的 `mpremote 1.28.0`，找不到专用解释器时不静默退回全局 Python。
- 新增后台串口 Monitor 候选实现：`esp_serial_monitor_start`、`esp_serial_monitor_stop`、`esp_serial_monitor_status` 和 `esp_serial_monitor_read`。
- Monitor 使用正式状态机、不可变项目绑定、单调递增 `seq`、`after_seq` 游标、有界环形缓冲和分块原始字节日志。
- 新增跨进程串口锁、进程所有权与端口身份记录、只针对已结束进程的陈旧锁恢复，以及 MCP Server 退出清理。
- 新增 Windows / Linux、Python 3.10 / 3.12 的 GitHub Actions 全量测试矩阵。

- 新增 SQLite schema v2 与 runs/events 仓储，包含 project-scoped 复合键、外键、JSON 对象约束、规范 UUID、事务 sequence 和结构化查询索引。
- 新增 v1 数据库重建迁移、legacy JSONL 稳定快照与可重复导入，以及 `docs/adr/0003-sqlite-log-authority.md`。
- `esp_logs_query` 新增 `run_id`、phase、level、tool、source、时间和 sequence 范围过滤，并同步 CLI、FastMCP schema 和静态工具注册资源。
- 新增 SQLite schema v3-A 的 `raw_logs` / `errors` 约束与复合索引，以及稳定 UUIDv5、
  occurrence-aware error identity、严格幂等、显式冲突和 project/run 边界校验的底层仓储 API。
- 新增不可变 `EventArtifacts` 和原子 `append_event_with_artifacts()`；completion event、
  raw、error 与 run sequence 在同一 SQLite 事务中提交，旧 `append_event` 二元组接口保持兼容。
- 新增 Monitor 终态 artifact 对账协议：`sqlite-artifacts-v1.json` 与旧
  `sqlite_reconciled` 生命周期标记分离，并记录规范 event/run/raw/error 集及确定性
  bundle SHA-256。
- 新增 v3-B4.1 `reconcile_existing_event_artifacts()`：只向既有终态 run 的最后一个
  `complete` event 原子补入 raw/error，不创建 run/event、不改变 sequence，并支持严格
  幂等重试；同 bundle 并发最终只插入并保留一组记录。
- 新增 v3-B4.2 `resolve_historical_monitor_artifacts()`：从当前项目内的 v1/v2 终态
  Monitor manifest/chunk 生成只读 `EventArtifacts` 候选，显式区分 `resolved` 与
  `no_artifacts`；不获取 lease、不访问 SQLite，也不发布 sidecar。

### Changed

- ESP-IDF key/LED/buzzer 示例新增受版本控制的 4 MiB、DIO、40 MHz `sdkconfig.defaults`，继续使用 single-app 分区；现有本地 `sdkconfig` 的应用边界已在示例 README 明确说明。
- 新增受版本控制的 MicroPython 分层回归 manifest 与 safe/runtime、GPIO34 只读、GPIO32 LED 状态、独立 negative 四个脚本；`esp_regression_test` 现在把不含 stdout 的逐项摘要写入 SQLite，并保守报告 Raw REPL 复位边界。
- `esp_performance_profile` 的最多 50 个工具生成样本、时间/堆变化汇总和 `sampling_profiler` 状态现在写入 SQLite completion 事件；异常文本限制为 256 字符，结构化 marker 限制为 128 KiB，主机在统计前验证并规范化固定字段；stdout、原始 marker 和内联 code 仍不落库。
- `esp_reset` 的有界动作后原始输出现在以长度、SHA-256、Base64、文本、解码状态、捕获完成状态和上限状态写入 SQLite completion 事件；该持久化仍不表示复位或输出因果关系已被独立确认。
- `logged_task` 支持工具局部声明额外结果字段白名单，并校验声明类型；默认工具不会因此持久化通用 `text`。
- `logged_task` 新增显式 completion artifact 策略。固定 capture 只投影受信任的正式 raw
  和明确的 result/structured error；程序停止只投影业务失败，不把正常停止时的
  `KeyboardInterrupt` 当作错误。
- 串口统一采用零参构造，打开前关闭流控并将 DTR/RTS 置为非活动态，打开后再次压低控制线；端口探测同时报告生命周期阶段和清理结果。
- Raw REPL 只有在严格收到 `OK + stdout EOT + stderr EOT + >` 后才确认完成，并分别记录 ACK、两个 EOT、提示符、退出发送和退出确认。
- 程序停止只在观察到 `>>>` 后确认；仅声明 `reset_command_sent=false`，并保留 `physical_reset_excluded=false`。
- GPIO 和 MicroPython runtime 硬件探测要求 `allow_program_interrupt=true`；回归执行要求 `confirm_execution=true`；性能重复执行要求 `confirm_repeated_execution=true`。
- passive 硬件信息只接受当前已枚举串口，并将 host USB descriptor 与 reviewed mapping 一起作为有来源标记的信息返回。
- `main` 维护产品实现和文档；`test` 分支的分支专属提交维护测试文件和测试规则，门禁由测试工作树加载主线源码执行。
- GitHub Actions 的 push 触发分支增加 `test`，使测试分支也执行 Windows/Linux、Python 3.10/3.12 矩阵。
- README、CHANGELOG、开发状态页和 ADR 分工记录不同层级的信息。
- SQLite 成为 runs/events 的正式状态与查询源；JSONL 改为审计镜像和旧数据迁移入口，`latest.json` 不再是查询权威。
- SQLite v2→v3 改为单事务重建 `raw_logs` / `errors`：严格复制并核对行数和外键后才写
  v3 marker；失败时保持原 v2 表、数据、版本和 marker。正式项目数据库不会由本阶段
  自动升级。
- 同步工具统一使用 start/prepare/complete/finish run 生命周期；后台 Monitor 在启动时固定完整 `LogScope`，并由 worker 写入原项目终态。
- 跨工作树门禁由 `index-test` 明确加载 `index` 源码，并校验实际导入来源，避免测试工作树误测自身旧实现。
- GitHub Actions 只检出被推送的单个分支；test 推送前必须合入固定、已验证的 main，不能把本地 `ESP_MCP_SOURCE_ROOT` 跨工作树覆盖当作远端分支同步。
- Monitor 终态恢复改为持有 OS run lease 的单写者流程；lease 覆盖扫描、受限修复、冻结、
  描述符复核、SQLite 原子投影、JSONL/latest 镜像和 sidecar 发布。终态 run 即使已有
  committed sidecar 也会执行可重入深度核验。

### Fixed

- 固定串口 capture 不再用秒级文件名覆盖同一 session 的先前日志；文件以 UUID 后缀和
  排他创建保存，并对原始 bytes 执行 flush + fsync。
- capture 不再把替换解码后的文本重新编码成“原始日志”；非法 UTF-8 现在原样写盘，
  `bytes_read` 统计真实接收字节，返回的 `text` 仅作为可读视图。
- capture 在打开串口前验证 raw 目录；持久化阶段失败不再抛出未捕获异常，而是返回
  `serial_capture_persist_failed`、原始字节事实和只作恢复用途的 `recovery_path`；
  open/碰撞耗尽不会误报别人的路径，close 失败另行报告持久化清理缺口。
- 修复仅提高 `CURRENT_SCHEMA_VERSION` 并执行 `CREATE TABLE IF NOT EXISTS` 时，
  v2 的旧 `raw_logs` / `errors` 会被错误标记为 v3、却没有获得新约束和索引的问题。
- v1 raw/error 迁移不再用 `INSERT OR IGNORE` 静默跳过冲突或不合规数据；此类问题现在
  终止迁移并整体回滚。
- `esp_backup_flash` 与 `esp_restore_flash` 现在共用规范路径边界：只接受当前 workspace
  或当前项目经校验的 `artifacts/flash`，拒绝越界路径以及 artifact/staging 中的
  symlink/junction。备份先写项目私有 UUID staging，再以不覆盖方式发布；已有 final、
  未知旧 `.part`、输出父目录替换和发布竞态均不会被覆盖或删除。
- Flash 备份发布冲突或底层文件系统不支持原子 hard link 时，不再删除已经完整且通过
  大小/SHA-256 校验的镜像，而是返回 `recovery_path`；成功发布后的临时文件清理失败
  也保留真实成功结果和清理告警。
- backup/restore staging 改为每次调用独占的 `run_<uuid>` 目录；清理拒绝 reparse 与
  非普通文件。Windows Python 不实现 `os.link(..., follow_symlinks=False)` 时走已测试
  兼容分支，hard link 完全不可用时仍返回结构化 recovery，而不是抛出未捕获异常。
- partial、restore staging 与运行目录的清理前检查若遇到权限/I/O 错误，现在记录为
  cleanup error 并保留文件，不再抛异常覆盖原始备份/恢复结果。
- 备份现在先完成输出父目录、已有 final 和旧 `.part` 的拒绝检查，再创建本次运行目录；
  无效请求不再留下空的 `backup-staging/run_*`。
- Flash 恢复在 `confirm=True` 后把源镜像复制到每次调用独占的项目 UUID staging，核对
  源文件身份、长度和双重 SHA-256，再把受控副本交给 esptool；未确认调用的启动日志不再记录
  未经校验的原始绝对路径或 expected hash。
- `esp_file_upload`、`esp_file_download` 和 `esp_run_file(path_type="local")` 的主机相对路径现在绑定活动项目 `workspace_root`，不再随 MCP 进程当前目录写入或读取插件缓存；父目录逃逸和工作区外绝对路径会在任何后端调用或文件副作用前返回 `unsafe_local_path`。
- Monitor 的 `STARTING` 并发测试改用假串口 `open_started` 事件进行确定性同步，不再假设慢速 CI runner 必须在 1 秒内完成 SQLite 初始化、线程调度和会话注册。
- Monitor 断连测试不再把 `DISCONNECTED` 错当作 worker 已退出。测试现在使用公开
  `esp_serial_monitor_stop(timeout_ms=5000)` 作为 join/cleanup 屏障，并断言日志关闭和
  `cleanup_complete`；Event 门控回归会验证真实慢清理返回 `monitor_cleanup_timeout`，
  fixture 也会明确报告残留 worker。生产状态机和断连终态顺序保持不变。
- `erase_flash` 改用统一受管子进程执行器，显式固定 `--before default_reset --after hard_reset`，超时或启动失败时保留 stdout、stderr、returncode 和进程树清理证据；`confirm=True` 高风险确认门保持不变。
- ACK 后缺少完整 Raw REPL 终止帧不再误报执行成功；stderr 以 `>` 开头时不会被第一个 EOT 提前截断。
- 源码、Ctrl-C 和 Raw REPL 退出的串口短写不再被记录为完整发送；cleanup 异常会保留原始 operation/protocol 错误类型和已收到输出。
- reset 不再清空打开串口时的输出；打开前后控制线状态、动作前输出、reset 动作、输出捕获和最终清理均分别记录。
- Monitor 串口改为非阻塞读取，先查询实际待收字节，单次最多读取 1024 字节；避免 Windows CH9102 稀疏输出场景中固定 `read(4096)` 产生污染记录。
- 无串口数据时使用 5 ms 有界等待，避免非阻塞轮询占满 CPU，同时保持停止清理及时响应。

- v1 hardwork/memory 表通过重建获得 project-scoped 复合主键，不再只追加可空 `project_id`。
- event UUID 幂等比较纳入规范化时间戳；终态 run 拒绝新事件，同时允许完全相同事件的严格重试。
- legacy JSONL 的事件身份不再依赖绝对文件路径，复制文件不会产生重复事件；导入器不会提前结束已有原生 running run。
- SQLite 事件提交后，JSONL 或 `latest.json` 镜像失败只形成 warning，不再阻止业务动作或反向否定正式审计。
- Monitor 启动失败统一终结已创建 run；stale manifest 使用确定性事件和 `sqlite_reconciled` 标记与原项目 SQLite 可重复对账。
- legacy JSONL 对原生 run 只允许既有 UUID 的严格去重；同 run_id 新 UUID 不再追加事件、回填端口或写 marker。
- optional 默认端口在 run 创建时冻结并传入业务函数，避免审计端口与实际动作端口发生 TOCTOU 偏差；缺失必填端口在建 run 前拒绝。
- 动作或状态变更完成后的日志故障保留真实业务结果，并通过 `logging_persisted=false` 和 `logging_warning` 报告审计缺口。
- completion 证据构建或 SQLite 原子写入失败时不再降级写入孤立的 completion event；
  原业务成功或失败语义保持不变，run 仍按业务结果结束。
- 固定 capture 的正式 raw 登记前会校验项目 `logs/raw` 边界、普通文件、symlink/junction/
  reparse、实际字节数和 SHA-256；`recovery_path` 只用于人工恢复，永不登记为正式 raw。
- Monitor 崩溃在 chunk rename 与 manifest 更新之间时，只对 stale 活动态收养合法孤立
  `.bin`；终态拒绝额外文件，并要求 manifest、磁盘、SQLite 的 chunk 集、大小和摘要完全一致。
- chunk/manifest/sidecar 读取改为打开后基于 fd 校验普通文件、reparse、身份、长度及读后
  稳定性，关闭校验到使用之间的 TOCTOU 窗口；旧绝对路径采用不跟随链接的保守兼容读取。
- 修复恢复锁作用域过窄和锁文件删除造成的 ABA：Windows 锁文件允许共享读写但禁止删除，
  释放时不 unlink；同一 run 的两个进程不能同时进入 SQLite/镜像/sidecar 提交区。
- 修复旧 stale completion 算法兼容问题：历史 UUID、`ended_at`、event/run 内容和最后事件
  完全一致时原样复用；不一致时拒绝追加第二条 completion。
- 修复仅凭 sidecar 布尔值判断 committed、同 UUID JSONL 内容冲突仍成功、latest 取错事件、
  持久 `RUNNING` stop 虚报已终态，以及 malformed startup recovery 返回空失败摘要的问题。
- 修复 Monitor 安全读取的 fd 双重所有权：`fdopen(closefd=True)` 成功后由 file object
  单独关闭，外层不再二次 `os.close` 已可能被并发复用的 fd；只有 `fdopen` 构造失败时才
  显式清理 descriptor 一次。
- 修复 test 分支只同步到 v3-B2 却直接加入 v3-B3 合同的问题；固定
  `main@98d9403` 已合入 test，README 继续由 main 单点维护。
- 修正 POSIX symlink 合同：恢复预检拒绝 reparse 后不应生成失败 sidecar。测试现在验证
  manifest 原字节不变、`artifact_marker=None`、无 sidecar、无 SQLite raw 和外部目标不变；
  生产 fail-closed 实现保持不变。
- 修复历史补投影只检查 run 已终态、却允许 `prepare` 或非最后 `complete` event 接受证据
  的问题；现在 event 必须同时满足 `phase=complete` 与
  `sequence_no=next_sequence_no-1`。
- 修复历史补投影在验证 event 归属前先返回 running 状态的问题；现在先校验
  project/run/event 绑定，错误或跨域 UUID 统一拒绝，再判断 run 是否终态。
- 修复非法 artifact 时间戳和 SQLite commit 失败逃出统一投影错误边界的问题；时间规范化、
  raw/error 写入和 commit 现在共同包装为 `artifact_projection_failed`、保留原始 cause，
  并在任一步失败时回滚整个 bundle。
- 修复历史 v1 Monitor chunk 绝对路径与原工作区绑定、工程移动后无法安全复用的问题：
  旧路径现在只作本地盘和规范后缀的词法验证，实际 I/O 只访问当前
  `logs/serial/<run_id>`。manifest 摘要与 JSON 来自同一安全 fd，project/log/serial/run
  目录链和 finalized chunk 精确集合在返回前后复核；B3 sidecar/旧 ownership 字段会
  fail-closed，陈旧 `process_owner` 和释放后保留的 lease 文件不被误当作当前所有权。

### Validation

- SQLite v3-B4.2 初始 42 条合同在入口缺失时全部按预期失败；独立审查补齐陈旧 owner、
  caller 路径逃逸、祖先 reparse、目录身份变化、持久 lease、合法 POSIX v1 历史和
  Windows 根相对路径拒绝后，专项 `58 passed in 1.66s`，既有 Monitor 回归
  `28 passed in 41.00s`，最终源码 main 全量
  `120 passed in 51.67s`，合入固定 main 后 test 分支自身源码全量
  `457 passed, 3 skipped in 249.69s`。正式项目 22 个 v1 manifest 的只读兼容检查得到
  14 个 `resolved`、8 个 `no_artifacts`、0 个错误，Monitor 文件和正式 SQLite 文件
  元数据前后不变；未访问 COM3、写正式 SQLite、升级 schema、更新 Marketplace 或安装缓存。
- v3-B4.1 首轮远端中，[main run 30338443462](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30338443462)
  的 Windows/Python 3.12 因既有 Monitor 固定 1 秒轮询失败，另外 3 个 main job 和
  [test run 30338445078](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30338445078)
  的 4 个 job 成功。确定性清理屏障修复后，两项针对性测试 `2 passed in 1.31s`、
  独立进程重复 `30/30`、Monitor 文件 `23 passed in 35.85s`、main 全量
  `120 passed in 50.72s`、同步后的 test 标准全量
  `399 passed, 3 skipped in 249.96s`。最终
  [main run 30340384047](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30340384047)
  与 [test run 30340395467](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30340395467)
  共 8 个 Windows/Linux、Python 3.10/3.12 job 全部成功。
- SQLite v3-B4.1 独立复审补强后先得到预期 `4 failed, 7 passed`；修复后专项
  `11 passed in 1.78s`、SQLite 相关 `146 passed, 2 skipped in 50.21s`、main 全量
  `119 passed in 49.32s`、test 显式加载 main 的跨工作树全量
  `398 passed, 3 skipped in 239.99s`，复审 P0=0、P1=0。测试只使用临时 SQLite，
  未访问 COM3、升级正式数据库、更新 Marketplace 或安装缓存；双分支远端矩阵待推送验证。
- SQLite v3-B3 Monitor 终态产物专项为 `43 passed, 2 skipped`；fd 所有权和 POSIX
  fail-closed 合同修复后，main 全量 `119 passed in 40.92s`，test 标准全量
  `387 passed, 3 skipped in 229.87s`，显式加载 main 的跨工作树门禁
  `387 passed, 3 skipped in 247.38s`。本机 skip 来自 Windows symlink 权限与既有平台
  边界；Linux 两套远端环境已执行真实 symlink 合同。main
  [run 30333882504](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30333882504)
  与 test
  [run 30334699560](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30334699560)
  共 8 个 Windows/Linux、Python 3.10/3.12 job 全部成功。全部使用临时项目/SQLite 和
  模拟对象，未升级正式数据库、访问 COM3、更新 Marketplace 或安装缓存。
- SQLite v3-B2 合同在旧实现上先得到预期 `11 failed, 1 passed`；最终原子投影专项
  `15 passed in 1.61s`，main 全量 `119 passed in 14.54s`，test 显式加载 main 全量
  `342 passed, 1 skipped in 35.69s`。两轮独立终审均为 P0=0、P1=0；测试只使用
  临时 SQLite、临时项目和假串口，没有迁移正式项目数据库或访问 COM3。
- capture 新合同在旧实现上得到预期两个失败：15 个原始字节被报告为 19，且同 session
  同秒两次 capture 返回同一路径。复审新增的 fsync 失败合同也先暴露未捕获 `OSError`；
  最终错误检测文件定向 `25 passed in 3.57s`，main 全量 `119 passed in 15.21s`，
  test 显式加载 main 全量 `327 passed, 1 skipped in 35.56s`；全部使用假串口和临时目录，
  未访问 COM3。
- SQLite v3-A 合同在旧实现上先得到预期 `20 failed in 0.52s`。实现后的基础合同为
  `20 passed`；加入假 v3 的 PK/FK/CHECK/索引验证、v2 缺列/额外列拒绝、重复/并发迁移、
  外键晚失败回滚、v1 直升、UUIDv5 确定性和重复异常 occurrence 后为
  `33 passed in 1.79s`。SQLite 合并定向为 `68 passed in 5.34s`，
  main 全量为 `119 passed in 14.89s`，test 显式加载 main 全量为
  `320 passed, 1 skipped in 34.84s`。测试只使用临时数据库，未迁移正式项目 v2
  数据库，也未访问串口或板卡；远端矩阵待提交后验证。
- Flash 路径安全合同在旧实现上先得到预期 `8 failed, 15 passed`；第一轮修复为
  `26 passed`，独立复审发现 reparse、恢复源 TOCTOU 和备份父目录替换缺口后继续补强。
  最终 Flash 定向门禁为 `36 passed, 1 skipped in 2.67s`；跳过项仅因本机无 Windows 目录
  symlink 创建权限，同一拒绝分支另有确定性测试。main 全量为 `119 passed in 15.53s`，
  test 显式加载 main 的全量门禁为 `287 passed, 1 skipped in 33.01s`。以上均未访问
  串口、未备份板卡、未擦除或恢复 Flash；远端矩阵仍待提交后验证。
- 4 MiB 配置静态合同先得到预期 `2 failed`，补齐 defaults 和说明后为 `2 passed in 0.43s`；普通 ESP-IDF build run `build_20260727_210357_c45f0c14` 成功，生成烧录参数和 bootloader 头均为 4 MB / 40 MHz / DIO。main 全量为 `119 passed in 13.99s`，test 跨工作树全量为 `249 passed in 28.88s`；本步骤没有烧录或访问板卡。
- MicroPython 回归套件初始合同先得到预期 `2 failed`，扩展输入安全合同后为预期 `9 failed, 3 passed`；复审发现的深层 JSON `RecursionError` 也先红后绿。最终相关定向门禁为 `74 passed in 7.30s`，main 全量为 `119 passed in 14.06s`，test 跨工作树全量为 `265 passed in 30.57s`。测试没有访问板卡或执行脚本。
- 性能样本持久化新增合同先得到预期 `2 failed`；安全补强合同再得到预期 `7 failed, 16 passed`。最终性能专项为 `24 passed in 3.47s`，性能/提示词/SQLite 定向门禁为 `65 passed in 6.64s`，main 全量为 `119 passed in 13.97s`，test 跨工作树全量为 `256 passed in 29.70s`。测试使用模拟 Raw REPL 和临时 SQLite，没有执行真实程序或板端副作用。
- reset / SQLite / 任务书 prompt 定向门禁为 `58 passed in 5.71s`；main 全量为 `119 passed in 15.18s`，test 加载 main 源码的本地全量门禁为 `247 passed in 28.82s`。本切片没有访问真实串口或板卡，远端矩阵与 Marketplace 更新尚待后续步骤。
- 相对路径修复先在旧 `main@5985230` 上得到确定性 `15 failed in 2.60s`；修复后 main 专项为 `32 passed in 4.15s`、test 跨工作树专项为 `48 passed in 9.26s`、提交前 main 全量为 `119 passed in 17.64s`、test 跨工作树全量为 `243 passed in 31.50s`。这些是本地软件结果，远端矩阵与新版 Marketplace 尚待完成。
- 2026-07-27 P0 跨平台修复已通过 main/test 共 8 个 Windows/Linux、Python 3.10/3.12 远端 job；`erase_flash` P1 修复后后端专项 `6 passed`、擦除工具专项 `8 passed`、main 全量 `104 passed in 13.89s`、同步 test 全量 `228 passed in 28.76s`。P1 的 [main run 30211040021](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30211040021) 与 [test run 30211040067](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30211040067) 共 8 个远端 job 全部成功。此后已按用户授权完成 `COM3` 真实整片擦除和 MicroPython v1.28.0 恢复。
- 个人 marketplace 源和已安装缓存均已核对为 `0.1.0+codex.20260726165544`，已安装插件枚举为 `48 tools / 12 resources / 12 prompts`。本次相对路径修复仍待生成后续 Marketplace 版本并由用户重启验证。
- Monitor STARTING 竞态测试修复后单项独立进程重复 `20/20` 通过，main 全量 `104 passed in 16.67s`；远端复跑待当前提交验证。
- 2026-07-26 提示词/提高工具/架构专项为 `25 passed`，串口/reset/Raw REPL/停止/错误检测关联门禁为 `62 passed`；显式跨工作树候选门禁为 `226 passed in 29.35s`。main→test 同步后，test 分支自身源码的标准全量门禁为 `226 passed in 27.66s`。
- 本次 2026-07-26 软件门禁使用模拟串口和临时项目目录，没有读取或操作真实板卡；MicroPython 执行类能力仍需独立实板验收。

以下条目是 2026-07-13 至 2026-07-20 既有 Unreleased 切片的历史验证，不代表 2026-07-26 候选已重新执行硬件、Marketplace 或远端 CI 操作：

- Monitor 假串口、存储和进程级专项测试通过，包括 stdin EOF、强制终止恢复、跨进程冲突、断连、缓冲区淘汰、UTF-8 分片、二进制日志和磁盘故障。
- 两条污染读取回归在修复前失败、修复后通过；跨分支全量门禁为 `101 passed`，Monitor 专项为 `29 passed`。
- `COM3` 修复后门禁通过：启动日志 3,653 字节无解码错误；最终按键日志包含两次完整五脉冲序列，共 1,466 字节、41 条记录，无替换字符、丢弃或未持久化字节。
- 修复后的仓库源码和个人 marketplace 源均通过 plugin validator，版本为 `0.1.0+codex.20260713135819`，从 marketplace 源直接枚举为 43 tools / 12 resources / 4 prompts；重新安装并重启后，缓存后端哈希核对和当前模型实板工具调用均已通过。
- SQLite 版本已同步到个人 marketplace 源 `0.1.0+codex.20260720110129`；validator、源目录 `99 passed` 和 `43 tools / 12 resources / 4 prompts` 枚举通过。当前 Codex 缓存尚未重载该版本。
- SQLite 定向契约 `33 passed`；`index-test` 显式加载 `index` 源码的跨工作树完整门禁 `134 passed in 21.44s`。
- 当前项目已正式创建 schema v2 SQLite：首轮导入 19 files / 32 events，第二轮 0 / 0 / 0；12 cancelled、2 failed、5 succeeded，19 markers，外键检查为空。
- SQLite 本轮只使用临时目录、mock 和独立进程验证，不涉及烧录、擦除、删除、full clean 或其他真实硬件动作。
- `main` push workflow #12 和 `test` push workflow #11 均通过；Windows/Linux、Python 3.10/3.12 共 8 个 job 的完整测试步骤全部成功。
- 本地全量门禁、正式迁移、marketplace 源同步和 GitHub Actions 已完成；在 Codex 缓存重载完成前，本节仍属于 `[Unreleased]`，不代表当前安装缓存已经包含该实现。
- 当前模型最终门禁 `monitor_20260713_223126_87fc393e` 捕获一次人工确认的完整五脉冲序列，共 733 字节、24 条分片，无解码错误、丢弃或未持久化字节；停止清理和同端口重新打开均成功。
- `COM3` 真实板卡门禁已通过：捕获 ESP-IDF 启动日志、验证游标续读不重复、停止后完整落盘，并立即重新打开同一端口。功能分支头 `962a382` 和 `main` 合入提交 `e67dd7f` 的 Windows/Linux、Python 3.10/3.12 CI 均全部成功；Monitor 已合入 `main`。
