# 当前开发状态

更新时间：2026-07-30（Asia/Shanghai）

## 当前分支

- 实现工作树：`index` / `main`。
- 测试工作树：`index-test` / `test`。
- 当前目标：完成任务书 6 项基础能力和 6 项提高能力，并形成 12 套 prompts + 48 个小工具的插件架构。
- 当前状态：启动器、串口生命周期、reset、Raw REPL/程序停止/错误检测、SQLite v3
  正式日志链、12 套任务书能力和 48/12/12 MCP 面均已完成。当前 Codex 任务已从
  Marketplace 安装版本 `0.1.0+codex.20260730084223` 加载 ESP skill 和 MCP tools。
- 2026-07-30 已完成非蜂鸣器实板封口：KEY1 松开/按住两态、UART-only
  backup→flash→READY/HEARTBEAT→restore、MicroPython/mpremote 恢复，以及四个临时
  验收文件的精确删除。用户已确认蜂鸣器瞬时电流/掉电属于业务固件问题，不是 ESP MCP
  工具链缺陷；该项不再专项处理，不是工具链未完成项，也不阻塞发布。
- 当前工作树新增确定性构建修复 `CONFIG_APP_COMPILE_TIME_DATE=n`、对应 test 合同和
  发布文档；这些变更尚未形成正式 main/test 提交，也没有新的远端 Actions 或
  Marketplace 包，因此项目状态是“发布候选，等待正式发布”。

## 本轮已完成实现

- 在独立 Conda 环境安装并验证 `mpremote 1.28.0`；解释器为 `C:\Users\16224\anaconda3\envs\esp-mcp-toolchain\python.exe`，未修改全局 Python。`.mcp.json` 经 `scripts/run_mcp_server.py` 定位该环境，找不到专用解释器时不会静默使用全局 Python。
- 新增 `esp_program_stop`：只发送两次 Ctrl-C，不发送 Ctrl-D 或复位命令；只有观察到 `>>>` 才确认停止。结果只证明 `reset_command_sent=false`，并明确 `physical_reset_excluded=false`。
- MicroPython 异常检测形成闭环：
  - raw REPL stdout/stderr 自动解析；
  - 固定串口 capture 支持跨 chunk Traceback；
  - 后台 Monitor 暴露 `detected_error` 并只写一次结构化 SQLite 错误事件；
  - `esp_error_parse_log` 一次固定 LogScope，按正式 errors → 正式 raw_logs → 兼容
    event/Monitor 选择；正式 raw 的路径/目录链/普通文件身份和完整 SHA-256 均验证后，
    才把不超过 `max_bytes` 的内容交给解析器；
  - 兼容 event 只取最新 64 条，message/payload 在 SQL 侧有界，截断 payload 不解码；
    schema v2 只读 runs/events，不迁移、不导入 JSONL。
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
- SQLite schema 升至 v3，`raw_logs` / `errors` 增加数据库约束和按 run/kind/time 的复合
  索引；仓储层新增稳定 UUIDv5、error occurrence identity、规范相对 POSIX 路径、
  SHA-256、时区时间戳、行列号和 recoverable 校验。
- raw/error 注册采用严格幂等：完整内容相同返回 `inserted=false`，同 ID 内容不同报显式
  conflict；跨项目或不存在的 run 在写入前拒绝。
- 显式 v2→v3 迁移只在事务中重建 raw/error 两表，严格复制并核对行数和外键后才写版本；
  约束失败或晚阶段外键失败会完整回滚。v1 raw/error 也改为严格复制，避免
  `INSERT OR IGNORE` 静默丢行。
- 固定 capture 现在保留精确串口 bytes；文件名带 UUID 后缀并以排他模式创建，写入后
  flush + fsync。同 session 同秒重复执行不会覆盖，`bytes_read` 不再统计替换文本长度；
  raw 目录在打开串口前准备，持久化失败返回结构化阶段；只有本次确实创建的文件才可作为
  recovery path，文件 close 失败单独标记持久化清理未完成。
- 新增不可变 `EventArtifacts` 输入和 `append_event_with_artifacts()`：调用方不能提供
  project/run/ID/created_at；仓储在单个 `BEGIN IMMEDIATE` 中按
  event → raw → error → sequence 顺序提交，任一证据冲突或 SQLite 写错均整体回滚。
  旧 `append_event(...)->(event, inserted)` 保持兼容；已结束 run 可用完全相同的
  completion UUID/内容补齐缺失证据而不消耗新序号。
- `logged_task` 只按工具显式策略投影证据。`esp_serial_capture` 只登记位于当前项目
  `logs/raw`、非 reparse 普通文件、大小等于 `bytes_read` 且由实际内容计算 SHA-256 的
  正式 raw；`recovery_path` 永不当作 raw。capture 可同时登记业务失败和既有 traceback
  两条 occurrence 不同的 error。`esp_program_stop` 只登记 `ok=false` 的 result error，
  正常停止中预期的 `KeyboardInterrupt` 不作为异常。
- completion UUID 和时区时间戳在构建证据与数据库提交之间共用。证据构建或原子事务失败
  时禁止降级写 completion-only event；业务结果保持原 `ok/error_kind/message`，另用
  `logging_persisted=false` 和 warning 暴露审计缺口，run 仍按业务结果结束。
- Monitor 终态恢复精确核对 manifest 与磁盘 chunk 集，只在 stale 活动态收养合法孤立
  `.bin`，终态拒绝额外文件；每个 raw/error/event/run 与确定性 bundle SHA-256 在同一
  SQLite 终态投影中提交。
- 新增独立、版本化 `sqlite-artifacts-v1.json`；旧 `sqlite_reconciled` 仅保留生命周期
  兼容语义。committed 状态会深度核验 canonical marker、SQLite 行、最后事件、JSONL 和
  latest 镜像，不信任 sidecar 的单一布尔值。
- chunk 与 JSON 读取使用描述符绑定检查，读前/读后复核普通文件、reparse、身份和长度；
  run lease 覆盖扫描到 sidecar 的完整恢复流程，Windows 锁禁止删除且释放不 unlink，
  避免并发提交与 ABA。
- 旧 stale UUID/`ended_at` 仅在历史 event/run 内容与最后事件完全一致时复用；持久
  `RUNNING` stop 会先恢复为 FAILED 后对账，运行期首错冻结、可重试 close、镜像冲突和
  有界 startup recovery 报告均有对应合同。
- Monitor 文件读取采用单一 fd 所有权：`fdopen(closefd=True)` 成功后只由 file object
  关闭；只有构造失败时才显式关闭原 descriptor。成功转移和构造失败均有独立回归合同，
  避免并发 fd 复用后被第二次关闭。
- 新增 v3-B4.1 `reconcile_existing_event_artifacts()`：必须提供已存在且归属当前
  project/run 的 event UUID；run 必须已结束，event 必须是最后一个 `complete`。API
  只补入 raw/error，返回 `event_inserted=false`，不创建或修改 run/event，也不消耗
  sequence。精确重试和同 bundle 并发均只保留一组记录。
- 历史补投影的时间规范化、raw/error 写入和 commit 共用一个 `BEGIN IMMEDIATE` 错误
  边界；仓储冲突、非法时间戳或 SQLite 提交失败统一包装为
  `artifact_projection_failed`、保留 cause，并完整回滚。
- 新增 v3-B4.2 `resolve_historical_monitor_artifacts()`：只读取当前项目
  `logs/serial/<run_id>` 的终态 manifest 与 finalized chunks。v1 的 Windows/POSIX
  旧绝对路径只作本地绝对路径和规范后缀的词法校验，实际 I/O 始终由当前 run 目录派生；
  v2 只接受规范 `name` 且禁止 `path`。
- manifest 摘要和 JSON 来自同一安全 fd；resolver 前后复核 project/log/serial/run
  目录身份，并验证终态、时间、精确 chunk 集、连续 ID、长度/SHA-256、
  `persisted_bytes` 与 B3 ownership。陈旧或缺失 `process_owner` 是历史元数据；
  B3 sidecar/旧 ownership 字段会拒绝，释放后保留的 lease 文件不等于 ownership。
- resolver 只返回 `resolved`/`no_artifacts` 文件证据候选及深拷贝错误快照；不获取 lease、
  不访问 SQLite、不调用 B4.1，也不写 sidecar、manifest、JSONL 或 latest。数据库
  native profile、持 lease 二次解析和实际补投影属于 B4.4。
- 新增 v3-B4.3 `resolve_historical_serial_capture_artifacts()`：caller 必须显式提供
  当前 `sessions` 的安全 basename、run 与规范 event UUID；source 路径只作 provenance，
  不进入 event/artifact identity。legacy event UUID 复用 importer 的公开 UUIDv5 纯函数，
  native completion 保留原 UUID，并返回不可变 event/run profile 与独立 reconciliation
  version，供 B4.4 在 lease 内与 SQLite 精确比较。
- adapter 严格解析 UTF-8 JSONL；native 必须恰为唯一 UUID 的两条
  `prepare → complete`，并校验连续 sequence、镜像 payload、project/run/tool/event、
  task/source/selected-port 归属及当前目录链；成功 completion 必须有一致 port，失败
  completion 可省略 port 且 mirror 端口允许一致为 `null`。旧
  Windows/POSIX `raw_path` 只作
  本地绝对路径与 `logs/raw/<basename>` 后缀校验，实际文件由当前项目重派生并在安全 fd
  上核对大小/SHA-256。source alias 不改变 event profile 或 artifact bundle identity。
- 旧 capture writer 曾 replacement decode 后 `write_text`，因此 legacy source 或旧式文件名
  只登记 `serial_capture_legacy_text`，不会冒充精确原始字节；`serial_capture_raw` 同时
  要求 native completion 来源和 UUID 排他文件名。legacy `phase=unknown` 候选返回
  `ineligible/legacy_event_phase_unknown`，不创建或改写 completion event。
- B4.3 不 glob 项目、不获取 lease、不连接 SQLite、不调用 B4.1，也不写 marker/JSONL/
  latest。跨 run 同一 raw basename 的歧义检测、项目级唯一 claim、lease 内二次解析、
  native DB profile 资格和独立 marker 发布均保留给 B4.4。
- B4.4 仓储基础在 schema v3 中新增 `historical_raw_claims`：主键为
  `(project_id, path)`，同时绑定 run、最后 completion event、artifact kind/SHA-256、
  adapter/version、event profile 和 bundle 摘要。已存在的 v3 临时库重开时会 additive
  补表，v2→v3 迁移也在同一迁移事务中建表；正式项目库已于 2026-07-29 在备份和
  临时副本演练后显式迁移。
- `reconcile_existing_event_artifacts()` 现在可选接收完整 event/run profile、精确 event
  sequence、run `next_sequence_no` 和逐 raw claim。全部条件在原有
  `BEGIN IMMEDIATE` 内、任何 claim/raw/error 写入前核对；claim 与 artifact 必须一一
  匹配且摘要绑定输入 profile/bundle。跨 run 重用相同项目相对 path 显式冲突，精确重试
  返回 `inserted=false`，晚阶段 error/SQLite 失败会连同 claim 和 raw 一起回滚。
- 新增 `HistoricalProjectReconciliationLease` 与版本化项目 marker store：持久 lock
  释放后不删除，探测缺锁不会创建文件；marker 使用同目录临时文件、fsync、原子 replace
  和发布后重读。Windows 仅对 WinError 5/32 做最多 1 秒有界重试。
- 新增 `reconcile_historical_project_artifacts()`：先用 URI `mode=ro` 检查 schema，
  v2 在 lease/marker/迁移前拒绝；v3 持项目 lease 扫描 B4.2/B4.3，先拒绝不同
  `(run_id, event_uuid)` 共用 raw path，再执行两次解析。Monitor 的第二次 resolver 与
  SQLite 补投影都处于对应 run lease 内。
- B4.4 每个 candidate 的 claim/raw/error 在 B4.1 中原子提交，项目任务允许失败后幂等
  续跑。SQLite 已提交而最终 marker 失败时报告两个持久化状态，精确重试可补 marker；
  释放后仍为 running 的 marker 由只读 status 报告为 `interrupted`。
- v3-C1 新增 `connect_readonly()` 和查询 schema 能力探测。三个日志查询不再调用
  `_prepare_scope()`：缺库零创建，v2 只读 runs/events 且不升级，v3 缺结构或损坏数据
  显式失败。latest/get 的多项读取使用同一 `BEGIN` 快照，JSONL 只保留审计/显式迁移
  角色。WAL 自身可能创建协调 sidecar，主数据库、schema 和应用文件保持不变。
- v3-C2 扩展 `read_run_snapshot()`：v3 在同一 `BEGIN` 中读取 run/events/raw/errors；
  v2 跳过 artifact SQL 并明确能力缺失。raw/error 分别使用 1000/200 的最新窗口和
  `LIMIT + 1` 截断探针，输出恢复稳定时间正序。
- 新增 connection-level bounded raw/error reader，不改 B3/B4 使用的既有无界对账 API。
  error 四类大文本在 SQL 侧裁剪并逐字段标记；raw 身份和 error 类型/时间/行列/recoverable
  在返回前重新规范校验，避免把损坏 SQLite 内容当可信文件证据。

## 本地验证

- 测试工作树通过 `ESP_MCP_SOURCE_ROOT` 显式加载实现工作树源码。
- 当前未提交候选的最终本地门禁为 UART-only 专项 `5 passed in 0.30s`、main
  `120 passed in 61.67s`、test 跨工作树
  `583 passed, 4 skipped, 0 failed in 332.84s`，Python 3.12.13、pytest 9.1.1；
  覆盖 `CONFIG_APP_COMPILE_TIME_DATE=n`、UART-only 示例说明和 test 合同。发布文档另以
  diff、证据一致性和 `git diff --check` 复核。4 项 skip 是 Windows 普通文件 symlink
  测试夹具权限边界，不是功能失败。
- 当前远端 `main@1e09789` / `test@8ba27b5` 仍只证明上一提交；本候选最终本地全量
  已完成，但必须推送新提交后，才能用新的 Actions 作为正式发布证据。
- B4.4 仓储基础红灯 `5 failed, 11 passed in 1.94s`；实现后专项
  `16 passed in 1.93s`，迁移/raw/error/历史对账合并
  `84 passed in 7.84s`，main 全量 `120 passed in 47.30s`。
- B4.4 协调器入口缺失时 `9 failed`；首轮实现后 `9 passed`。复审三项合同先得到
  `3 failed, 9 passed`，修复 Busy retryable、metadata error 和 run-aware raw owner 后
  `12 passed`；B4.1-B4.4 组合 `145 passed, 1 skipped in 6.98s`。
- v3-C1 初始四合同为预期 `4 failed in 0.39s`；实现后补强 query-only、校验先于打开、
  三入口损坏库、坏 JSON、不完整 schema、locked/unavailable 分类和非普通路径拒绝，
  最终专项 `10 passed in 0.43s`。既有日志与
  项目上下文回归 `6 passed in 0.81s`，main 全量 `120 passed in 49.27s`。
- v3-C2 初始四合同在 C1 实现上为预期 `4 failed in 0.89s`；实现后补入并发单快照和
  非规范 error 值，C2 专项 `6 passed`，C1+C2 `16 passed in 0.99s`，main 全量
  `120 passed in 48.06s`。
- 当前完整本地门禁：main `120 passed in 56.80s`；test 显式加载 main
  `557 passed, 4 skipped in 271.45s`。4 项 skip 为 Windows 普通文件 symlink
  权限边界，不涉及本次 SQLite 正式数据验收。
- 正式库迁移前为 schema v2、111 runs、224 events、110 个旧导入标记，主文件
  SHA-256 为 `5D5F75E12C54EF6137CFD2BA991A949FF2574304E96BC67A97B961649AA8711D`；
  `integrity_check=ok` 且外键零违规。临时副本先完成 v3 升级、首次补投影和第二次
  幂等回放，正式源文件与 172 个日志文件前后不变。
- 正式升级保留同 SHA-256 的 v2 备份。首次协调扫描 26 项，5 项补投影、17 项
  ineligible、4 项 `no_artifacts`、0 失败；第二次回放 5 项均为
  `already_reconciled` 且数据库零新增。最终为 schema v3、111 runs、224 events、
  5 raw、1 error、5 claim、110 个旧导入标记，完整性和外键继续通过。
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
- SQLite v3-A 在旧实现上的初始合同为预期 `20 failed in 0.52s`；基础实现绿灯后补入
  假 v3 的 PK/FK/CHECK/索引验证、v2 缺列/额外列拒绝、重复/并发升级、外键晚失败回滚、
  v1 raw/error 直升、UUIDv5 确定性和相同异常不同 occurrence 合同，最终专项
  `33 passed in 1.79s`、SQLite 合并定向 `68 passed in 5.34s`、main 全量
  `119 passed in 14.89s`、test 显式加载 main 全量 `320 passed, 1 skipped in 34.84s`。
- v3-A 测试仅使用临时 SQLite；正式项目 v2 数据库没有迁移，当前安装插件没有更新，
  串口和 COM3 没有访问。
- capture 两个初始合同在旧实现上均失败；复审增加的 fsync 失败合同也先得到未捕获
  `OSError`。最终错误检测文件定向 `25 passed in 3.57s`、main 全量
  `119 passed in 15.21s`、test 显式加载 main 全量
  `327 passed, 1 skipped in 35.56s`。测试使用假串口和临时目录，没有访问 COM3。
- v3-B2 初始合同在旧实现上为预期 `11 failed, 1 passed`；适配不可变 API 并补入
  原子回滚、旧接口兼容、外部/大小/reparse raw 拒绝、recovery 排除、双错误、
  program-stop KeyboardInterrupt 语义及日志失败不篡改业务结果后，专项
  `15 passed in 1.61s`。两轮独立终审均为 P0=0、P1=0。
- 当前 main 全量 `119 passed in 14.54s`；test 工作树显式加载 main 源码为
  `342 passed, 1 skipped in 35.69s`。skip 是既有 Windows 目录 symlink 权限限制；
  同一 reparse 拒绝分支另有确定性 monkeypatch 合同。所有新增测试只使用临时 SQLite、
  临时工程和假串口，没有迁移正式项目数据库或访问 COM3。
- v3-B3 Monitor 终态产物专项 `43 passed, 2 skipped`；最终 main 全量
  `119 passed in 40.92s`，test 标准全量 `387 passed, 3 skipped in 229.87s`，test
  显式加载 main 的跨工作树全量 `387 passed, 3 skipped in 247.38s`。test 产品代码原来
  只同步到 B2，已合入固定 `main@98d9403`；不能用本机跨工作树覆盖代替 GitHub 的单分支
  检出。Windows 本地普通文件 symlink 因权限跳过，Linux 远端已验证恢复预检 fail-closed
  且零副作用。
- v3-B3 最终远端门禁：[main run 30333882504](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30333882504)
  与 [test run 30334699560](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30334699560)
  共 8 个 Windows/Linux、Python 3.10/3.12 job 全部成功。
- v3-B4.1 独立复审补强后先得到预期 `4 failed, 7 passed`；修复后专项
  `11 passed in 1.78s`，SQLite 相关 `146 passed, 2 skipped in 50.21s`，main 全量
  `119 passed in 49.32s`，test 显式加载 main 的跨工作树全量
  `398 passed, 3 skipped in 239.99s`。独立复审 P0=0、P1=0。
- B4.1 首轮远端中，[main run 30338443462](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30338443462)
  的 Windows/Python 3.12 在旧 Monitor 固定 1 秒轮询处失败，另外 3 个 main job 和
  [test run 30338445078](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30338445078)
  的 4 个 job 成功。改用公开 stop join 屏障并补慢清理合同后，针对性测试 `2 passed in
  1.31s`、独立进程重复 `30/30`、Monitor 文件 `23 passed in 35.85s`、main 全量
  `120 passed in 50.72s`、同步后的 test 标准全量
  `399 passed, 3 skipped in 249.96s`。最终
  [main run 30340384047](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30340384047)
  与 [test run 30340395467](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30340395467)
  共 8 个 job 全部成功。
- v3-B4.2 初始入口缺失时为预期 `42 failed`；独立复审补齐陈旧 owner、caller 越界、
  祖先 reparse、目录身份变化、持久 lease、合法 POSIX v1 历史和 Windows 根相对路径
  拒绝后，专项 `58 passed in 1.66s`。既有 Monitor 回归 `28 passed in 41.00s`，最终源码
  main 全量 `120 passed in 51.67s`；合入固定 main 后 test 分支自身源码全量
  `457 passed, 3 skipped in 249.69s`。
- 正式项目 22 个 v1 manifest 的只读解析得到 14 个 `resolved`、8 个
  `no_artifacts`、0 个错误；Monitor 文件与正式 SQLite 文件元数据前后不变。该检查
  没有连接正式 SQLite、写任何项目文件或访问 COM3。
- B4.2 推送后的 [main run 30345368464](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30345368464)
  4 个 job 全部成功；[test run 30345364620](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30345364620)
  仅 Windows/Python 3.10 失败。没有直接重跑；本机复现并确认是既有 Monitor lease 在
  owner truncate 窗口内执行加锁前 sentinel flush，正常竞争被误报为
  `PermissionError [Errno 13]`。
- 删除 pre-lock sentinel 后，受控 zero-length busy/release/reacquire、双线程 lease
  2000 轮、原并发测试独立进程 `100/100` 均通过；main 全量
  `120 passed in 51.01s`，合入固定 main 后 test 自身源码
  `458 passed, 3 skipped in 256.55s`，两轮审查 P0=0、P1=0。修复后的
  [main run 30347587842](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30347587842)
  与 [test run 30347592644](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30347592644)
  共 8 个 job 全部成功；没有重跑失败 job。
- B4.3 adapter 缺失时合同为预期 `40 failed, 1 skipped`；实现后独立复审发现 legacy
  source 可用 modern-looking 文件名误标精确 raw，以及 native 全记录身份校验不足的
  P1。补入 source-format 双条件与严格两记录身份合同后专项
  第一轮修复后，第二轮复审又发现合法失败可没有 payload port；对应合同先得到
  `2 failed, 48 passed, 1 skipped`。全部修复后专项
  `50 passed, 1 skipped in 1.31s`，main 全量 `120 passed in 49.93s`。
- 固定 main 合入 test 后，test 分支自身源码全量
  `508 passed, 4 skipped in 254.09s`。B4.3
  [main run 30356471000](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30356471000)
  首次仅 Windows/Python 3.10 的既有 Monitor 跨进程用例在固定 8 秒 ready 窗口超时；
  该用例本地独立进程 `10/10` 通过后只重跑失败 job，attempt 2 成功。
  [test run 30356899571](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30356899571)
  四个 job 首次全部成功；最终两分支共 8 个矩阵 job 成功。
- 正式项目 4 个 capture 的只读解析为 1 个 native `resolved`、3 个 legacy
  `ineligible`；四项均为旧 writer 的 `serial_capture_legacy_text`。探针将
  `sqlite3.connect` 替换为失败函数仍全部完成，前后 189 个正式项目文件的路径、长度、
  mtime 与 SHA-256 差异为 0。
- C3 首轮五项合同在旧实现上为预期 `5 failed in 0.87s`；查询前 payload/最新事件窗口
  两项复审另为预期 `2 failed`。完成后专项 `7 passed in 1.09s`、相关回归
  `49 passed in 5.63s`、main 全量 `120 passed in 48.77s`、test 显式加载 main 全量
  `557 passed, 4 skipped in 250.00s`。仅使用临时 SQLite/raw/Monitor 文件。
- C2/C3 文档同步后的
  [test run 30438663603](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30438663603)
  仅 Windows/Python 3.10 在既有 Monitor 用例的固定 3 秒前置轮询超时，另外三个 job
  成功。失败发生在 stop 及 SQLite artifact 断言之前，相关测试与产品源码和此前全绿
  提交相同。测试现改为逐段输入并通过带游标的条件读取确认 `abc`、`def`，再保留全部
  stop/幂等/摘要断言；单项独立进程 `20/20`、相邻回归 `5 passed`、Monitor 文件
  `46 passed, 2 skipped`、test 全量 `557 passed, 4 skipped in 255.17s`。修复提交的
  [test run 30446579852](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30446579852)
  四个矩阵 job 均成功。
- 本次路径软件测试使用 mpremote / Raw REPL mock、临时项目目录和临时 SQLite，不访问真实开发板；下节单独记录此前已执行的实板动作。

## 安全与实板状态

- 最终非蜂鸣器封口以 2026-07-30 晚间的第二次 UART-only 闭环为当前证据；此前各批次
  记录保留为历史阶段，不得覆盖本节最终结论。
- KEY1 两态已完成：`gpio_status_20260730_191534_37959ed8` 在用户确认松开时读取
  GPIO34=`1`，`gpio_status_20260730_191640_87385e23` 在用户确认按住时读取
  GPIO34=`0`。两次均为只读查询且未改变 GPIO 模式；硬件映射已更新为
  `board_test_confirmed / confidence=1.0`。
- 第一次 UART-only 闭环中，`idf.py flash` 因
  `CONFIG_APP_COMPILE_TIME_DATE=y` 重编 `esp_app_desc.c`，应用哈希发生漂移。系统没有
  继续串口验收，而是用该轮新鲜 4 MiB 备份恢复。修复后
  `CONFIG_APP_COMPILE_TIME_DATE=n`。该漂移来自本次会话实时工具输出，未进入
  SQLite/JSONL completion payload。后续普通增量构建
  `build_20260730_192625_8ef67c38` /
  `build_20260730_192812_b9042a6e` 均成功；第二次构建后的最终烧录前复核确立了
  实板输入。
- 该最终实板输入为 bootloader 26,720 字节
  `1BFB7F309DB6C232FB20AF613B3A2E0E0570C615DD41DEADFF213F6C5015ABE8`，
  partition 3,072 字节
  `7F00B6C042A89B15B0CAC534F82ED988CAF29278FF5700B0C511EB1B5BB7C820`，
  app 176,816 字节
  `4017628FA6BDFD2453C6518299F60D0ACF2A15BD3C43D466DE7CA8EF365D8CA2`。
- 第二次闭环新备份 run `backup_flash_20260730_193625_9ba0d34b` 读取
  4,194,304 字节，文件 SHA-256 为
  `F28649C0194A67C951E5DFCB8BC690B526ABD1CFDA50D94BE2027F5DCA66CE89`。
  `flash_20260730_193819_dfa2a884` 只写目标区段，没有调用整片擦除工具。
- `reset_20260730_193902_3172efe8` 捕获 READY 和 HEARTBEAT `0,1`；
  `serial_capture_20260730_193904_5fdcba4c` 在 115200 连续捕获 7 秒和
  HEARTBEAT `2..8`，原始日志 476 字节、SHA-256
  `C0411F143FDF459800DCB06C335ADA57FABBF285EC4C6B09E248DE672F9ED50C`，
  且没有结构化错误。
- `restore_flash_20260730_193928_f67fad17` 从地址 0 写回完整 4,194,304 字节；
  `hardware_info_20260730_194102_9624e720` 与后续 mpremote 目录查询确认
  MicroPython Raw REPL 和文件访问恢复。本轮没有做恢复后的第二次完整 4 MiB
  read-back，不能声称当前 flash 重新读回摘要等于备份摘要。
- 经删除前目录复核，四个明确授权的临时验收路径已逐个删除。最终实时目录查询
  `file_list_20260730_194316_f3f30c16` 的本次会话实时工具返回只包含 `/boot.py`；
  SQLite/JSONL 摘要只证明四次删除终态与最终列目录命令成功，没有持久化该目录 stdout。
  没有格式化、没有删除其他路径，也没有操作 GPIO25/PWM/蜂鸣器。
- reset 工具仍报告 `reset_confirmed=false` 与
  `output_causality_confirmed=false`；当前证据是“脉冲已发送且捕获到随后启动输出”，
  不是 reset 工具独立证明因果。UART-only 也只证明有序串口输出，不证明物理 GPIO 电平。
- 当前项目上下文为 `summer-holiday-1-2268049d8188`。
- 已审查映射为 ESP32-D0WD-V3、GPIO34 KEY1、GPIO32 低有效 LED、GPIO25 PWM 蜂鸣器、UART0 115200；2026-07-27 的 MicroPython runtime 探测已作为新的 board-test 事实增量写回。
- 授权前备份 4,194,304 字节，SHA-256 为 `23F1A7424286FED0BA59A1E6883DB4195CDF344F696B628C314892B24585B6B9`；`erase_flash_20260727_131837_f672becc` 成功，`restore_flash_20260727_131918_88af58ec` 成功恢复并校验官方 MicroPython v1.28.0 BIN。
- 动作后捕获到 MicroPython v1.28.0 banner；运行时信息、20 条串口 Monitor 标记和三个临时文件的上传/读取/列表通过。hard reset 工具仍保留 `reset_confirmed=false` 与 `output_causality_confirmed=false` 的严格边界。
- 2026-07-30 hard reset run 捕获的启动输出显示板上实际运行后续烧入的 ESP-IDF 示例；
  这解释了 Raw REPL 与 mpremote 超时。经明确授权，当前 ESP-IDF 4 MiB 备份 SHA-256 为
  `5ACF1DB30021D3B1C1A83264E586007A7F36AB2C5B604522612E2E6C164E2365`，随后擦除并恢复
  已核验的 MicroPython v1.28.0；启动 banner 与 `>>>` 已捕获。
- 相对上传、板端读回和相对下载使用全新 21 字节载荷完成。下载实际落在 workspace，
  源/目标 SHA-256 同为
  `2DDF47ADFD6E81358CE6B00AA1EF332AF66AE718BD3AE2CAAC452218958CD163`，插件缓存同名文件
  数为 0，因此 `remote_file_management` 的相对路径门禁已通过。
- 活动循环由 `program_stop_20260730_125724_a9e36938` 确认中断：
  `interrupt_write_count=2`、`KeyboardInterrupt`、`>>>`、`stop_confirmed=true`，
  cleanup 完成；它没有显式发送 reset，但 `physical_reset_excluded=false`。
- 受控 `ValueError` run `exec_code_20260730_125742_1024f69a` 的 Raw REPL 完整终止帧、
  即时 `error_report` 和 `esp_error_parse_log` 结构均正确。正式 errors 为空暴露
  producer 漏项；`esp_exec_code` / `esp_run_file` 现只声明 `structured_error`
  completion artifact。旧实现红灯为 `2 failed, 1 passed`，修复后专项通过，独立审查
  P0=0、P1=0。
- Marketplace/安装缓存更新到 `0.1.0+codex.20260730053724` 后，受控 run
  `exec_code_20260730_150437_0d9c65aa` 直接复验 producer。authoritative schema-v3
  `errors` 恰好返回一条 `micropython_traceback / ValueError / <stdin>:1`，error ID 为
  `5d63306a-a820-5282-9728-f95bee726015`；`esp_error_parse_log.scan_sources` 只有
  `sqlite_errors`，计数为 1。COM3 调用后仍可用且未占用。
- GPIO34 严格查询 run `gpio_status_20260730_125804_b64cdc22` 返回有效电平 `0`、
  `gpio_read_only=true`、`mode_changed=false`、`failed_count=0`；没有同步人工按键观察，
  不能断言实体 KEY1 当时被按下。正向回归 run
  `regression_test_20260730_125904_536d5573` 为 `2/0/0`，其中 GPIO34 脚本显式设置
  `Pin.IN` 并读到 `1`；negative run `regression_test_20260730_125910_53f2c8a1`
  按预期为一项 `test_failed`，未被误报为通过。
- 性能 run `performance_profile_20260730_125937_ade171bb` 得到 7/7 成功样本，
  `timing_us` 中位数 `4648`、均值 `4766.14`，每次堆差值 `-208` 字节；仅表示
  instrumented wall time 和 `gc.mem_free` 差值，不表示功耗、供电或长期泄漏。
- soft reset run `reset_20260730_125952_687488e1` 捕获 135 字节
  `MPY: soft reboot`、MicroPython v1.28.0 banner 与 `>>>`，输出 SHA-256 为
  `3A78210005A02F30F974E42CB00B0511A0234AFF9A4402D6B87805E7C47EDAF2`；仍保留
  `reset_confirmed=false`、`output_causality_confirmed=false`。复位后无 `main.py`，
  `/boot.py` 为官方注释模板。
- 该阶段实板操作未访问 GPIO25、蜂鸣器、PWM 或 GPIO32 stateful 用例，未发生 COM3
  断连；因此不能据此确认或排除蜂鸣器瞬时电流掉电。当时板端回归脚本与载荷仍保留，
  后续已按精确授权完成删除，见本节顶部的最终封口记录。
- 该阶段完整软件门禁为 main `120 passed in 60.59s`、test 显式加载 main
  `582 passed, 4 skipped in 297.08s`；4 项 skip 是 Windows 普通文件 symlink 权限边界。
- 新增 `examples/esp_idf_uart_smoke`，因为原 `esp_idf_key_led_buzzer` 启动即初始化
  LEDC/GPIO25，不能满足无蜂鸣器边界。首次 host build run
  `build_20260730_152205_0508e89d` 成功；BIN 为 176,896 字节，SHA-256 为
  `AA9E9AFA7036D2F78B183A4834835EBEDDD859AB3914D3509F9C00FD0AD409A9`，flash args
  为 DIO / 40 MHz / 4 MiB，地址 `0x1000`、`0x10000`、`0x8000`。构建没有访问 COM3。
- 首次 build 的 `set-target` 触发了 fullclean 依赖，但当时没有 build 目录，ESP-IDF
  明确报告 `Nothing to clean`。后端现使用五种 target plan；三种需要 fullclean 或
  sdkconfig 替换的计划在 `confirm_target_change=false` 时零子进程，且记录
  `sdkconfig.old` 覆盖风险、partial possible 和 postflight target verified。实际增量构建
  返回 `target_plan=build`、两项 planned 均为 false、`target_verified=true`。
- 复审发现首轮路径门禁只覆盖 destructive plan，普通 build 仍可能经 build
  junction/symlink 写到项目外。现已在读取 target cache 前和启动 `idf.py` 前对全部五种
  plan 做两次检查，同时拒绝链接或非普通文件形式的 `CMakeCache.txt`。新增普通 build
  与首次 define-target 零 spawn 合同；定向 `39 passed in 2.30s`，最终独立复审
  P0=0、P1=0。
- 后续已完成独立的 4 MiB backup→flash→READY/HEARTBEAT→restore MicroPython
  闭环；本次接受的是 176,816 字节、SHA-256 `4017628F...8CA2` 的确定性 app，
  不是首次 host build 的 176,896 字节 `AA9E9AFA...09A9`。
- 首次推送后的 main run `30525807125` 有 3/4 job 成功；Ubuntu/Python 3.10 在既有
  Monitor 启停竞态中命中 `STOPPED + last_error=null`，旧 start 入口链式 `.get()` 后
  抛 `AttributeError`。现已按 `dict | None` 合同规范化并提供结构化 fallback；确定性
  红灯 `1 failed`，修复后相关 `2 passed`、竞争用例独立 `30/30`、main 全量
  `120 passed in 59.15s`。修复合入 test 后标准全量为
  `583 passed, 4 skipped in 314.90s`；main run `30526826689` 与 test run
  `30526826402` 共 8 个矩阵 job 全部成功。
- 文档 main run `30528050703` 的 Ubuntu/Python 3.10 又命中高频 status 完成条件竞态：
  received 已为 262144，persisted 仍为 258048；另外三个 main job 与 test run
  `30528050497` 四个 job 成功。test 分支 `366f288` 早已等待双计数并在 stop 后复核，
  但未回同步 main。现只回同步该测试合同；独立进程 `30/30`、main 全量
  `120 passed in 57.79s`，没有修改产品持久化时序。

## 插件发布状态

- 当前仓库的既有本地 plugin manifest 差异不属于本次提交，文件摘要保持不变。
  个人 Marketplace 源已用一次 cachebuster 更新为
  `0.1.0+codex.20260730084223`；用户重启后，当前 Codex 任务已从该版本路径
  加载 ESP skill 和 MCP tools。安装缓存没有被直接修改。
- 新 Marketplace 源通过 plugin validator、发布测试 `120 passed in 57.90s` 和
  `48 tools / 12 resources / 12 prompts` 直接枚举；活动版本切换已确认，但当前
  未提交确定性构建修复只有在本候选同时发布为 Codex 插件时，才需要新包、一次
  cachebuster 和再次重启验收。
- B4.4/C1-C3 的正式数据库验收此前已完成；本次新版只追加 exec structured-error
  正式投影复验。本轮仍不会修改仓库内用户自有的 plugin manifest 差异。
- 既有 UART-only 示例、构建门禁和 Monitor 竞态修复已提交、合入 test、完成双分支
  推送和 Marketplace 源同步；当前工作树的编译时间戳修复和最终封口文档仍属于新的
  `[Unreleased]` 候选。

## 项目封口后的可选或需授权事项

1. KEY1 两态、临时文件删除和 UART-only 严格实板闭环已经完成，不再列为待办。
2. 当前候选最终本地 main/test 全量已完成；正式发布仍需精确提交、双分支新 Actions，
   以及版本号、Release Notes 和 Git tag。
3. 仅发布 GitHub 仓库时无需更新 Marketplace；若同时发布 Codex 插件，还需同步个人
   Marketplace 源、使用一次 cachebuster，并由用户重启验收。
4. 用户已确认蜂鸣器瞬时电流/掉电属于业务固件问题，不是 ESP MCP 工具链缺陷；无需
   后续工具链动作，也不是可选待办或发布阻塞。现有 UART 结果仍不写成蜂鸣器电气验证。
5. 详细发布清单和禁止扩大声明的边界见 `docs/15-release-readiness.md`。
