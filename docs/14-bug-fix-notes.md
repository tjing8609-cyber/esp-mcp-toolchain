# Bug 修复学习记录

本文按“症状、根因、修复、验证、剩余风险”记录影响项目流程的典型 Bug。它不替代
`CHANGELOG.md`；前者用于解释为什么会出错，后者用于记录用户可见变化。

## 2026-07-27：GitHub Actions 中 main 全失败、test 仅 Linux 失败

### 症状

- `main` 的 Windows/Linux、Python 3.10/3.12 四组任务均在完整 pytest 步骤失败：
  Raw REPL 一项、reset 两项。
- `test` 的 Windows 两组通过，Linux 两组在测试结束清理阶段报错：
  `NotImplementedError: cannot instantiate 'WindowsPath' on your system`。
- 依赖安装和 Actions runner 本身正常；Node.js 弃用提示不是失败原因。

### 根因

#### 1. main 的旧假串口没有跟随生产串口生命周期升级

生产代码已经从“构造 `Serial(port, baudrate, timeout)` 时直接打开”改为：

1. 零参数构造串口对象。
2. 设置端口、波特率、读写超时和流控。
3. 在打开前把 DTR/RTS 置为安全非活动态。
4. 显式调用 `open()`。
5. 只在完整 Raw REPL 帧或完整 reset 动作证据出现后报告成功。

main 中保留的三个轻量测试仍模拟旧构造方式，也没有模拟显式 `open()`、动作开始前
不返回启动文本等新合同。因此失败的是过时测试桩，不是生产代码需要退回旧行为。

#### 2. Linux 测试修改了进程共享的 `os.name`

Windows 进程树测试使用：

```python
monkeypatch.setattr(subprocess_utils.os, "name", "nt")
```

`subprocess_utils.os` 不是独立副本，而是 Python 进程共享的 `os` 模块。测试把
`os.name` 改为 `nt` 后，自动项目上下文 fixture 先进入清理阶段，pytest 随后才恢复
monkeypatch。Linux 清理代码此时调用 `pathlib.Path(...)`，`Path` 根据伪造的 `nt`
选择 `WindowsPath`，最终在 Linux 上抛出 `NotImplementedError`。

这个问题在本机 Windows 上不会暴露，因为 Windows 本来就支持 `WindowsPath`；它说明
跨平台分支模拟必须限制修改的生命周期，不能污染其他 fixture 的 teardown。

### 修复

- main 的轻量 Raw REPL/reset 假串口改为零参数构造、显式 `open()`，并模拟安全控制线、
  动作开始和读取时序；没有放宽生产代码的完整帧或 reset 证据要求。
- Windows 进程树测试改用局部 `monkeypatch.context()`。平台状态在测试函数继续执行
 断言前已经恢复，不再等待 pytest 的全局 fixture 收尾。
- `.codex-plugin/plugin.json` 的既有本地差异未进入本次提交。

### 验证

- main 本地：`104 passed in 14.70s`
- test 本地：`226 passed in 28.08s`
- test 加载 main 源码：`226 passed in 27.36s`
- main Actions：
  [run 30210462578](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30210462578)，
  Windows/Linux、Python 3.10/3.12 全部成功。
- test Actions：
  [run 30210462530](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30210462530)，
  Windows/Linux、Python 3.10/3.12 全部成功。

### 经验

- 测试桩必须跟随资源生命周期，而不是只模拟最终返回值。
- 修改 `os`、`sys` 等共享模块属性时，应使用局部上下文并在 fixture teardown 前恢复。
- 本地单平台全绿不能替代 Windows/Linux CI。
- CI 失败时先区分“生产缺陷、测试桩过时、测试隔离错误、runner 警告”，不要为了让
 旧测试通过而回退安全生产合同。

### 剩余风险

本次结论只覆盖软件测试和 CI。它没有重新验证 MicroPython 实板执行、蜂鸣器供电稳定性、
擦除、烧录或新版 Marketplace 缓存。

## 2026-07-27：erase_flash 超时路径绕过受管进程

### 症状

- `run_erase_flash()` 直接调用 `subprocess.run()`；其他构建、烧录路径已经使用统一的
  `run_managed_command()`。
- 超时只能返回一个领域错误，不能证明子进程树是否终止，也不保留统一的清理字段。
- 命令依赖 esptool 的默认复位行为，没有在命令中固定擦除前后的复位语义。
- 原测试只检查命令中出现 `erase_flash`，不能阻止上述安全合同回退。

### 根因

擦除功能早于统一子进程管理器实现，后续迁移时漏掉了这个入口。旧测试又只验证“函数可调用”
和最终返回值，没有验证进程生命周期、精确参数、失败映射与确认门，因此代码长期保持可用但
不可充分审计的状态。

### 修复

- test 分支先定义精确命令合同，要求：
  `python -m esptool --chip esp32 -p <port> --before default_reset
  --after hard_reset erase_flash`。
- 后端改用 `run_managed_command()`，统一处理超时、启动失败、returncode、stdout、
  stderr 和进程树终止/清理证据。
- 公共错误 `managed_command_timeout`、`managed_command_spawn_failed` 映射为
  `erase_timeout`、`erase_spawn_failed`，但不重建结果字典，避免丢失诊断字段。
- `esp_erase_flash(confirm=False)` 的测试让后端一旦被调用就立即失败，证明未确认时
  不会启动 esptool；生产确认门没有放宽。

### 验证

- 测试先行证据：旧 main 面对新增后端契约时为 `3 failed, 3 passed`；确认门专项
  `2 passed`。
- 修复后后端专项：`6 passed`。
- 修复后擦除工具专项：`8 passed`。
- main 全量：`104 passed in 13.89s`。
- test 加载 main 源码的跨工作树全量：`228 passed in 27.76s`。
- main Actions：
  [run 30211040021](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30211040021)，
  Windows/Linux、Python 3.10/3.12 全部成功。
- test Actions：
  [run 30211040067](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30211040067)，
  Windows/Linux、Python 3.10/3.12 全部成功。

### 经验

- “设置了 timeout”不等于“超时后进程树一定清理”；外部工具必须统一走受管执行器。
- 依赖第三方工具默认值会降低可审计性。影响复位或设备状态的参数应显式写入命令并由测试锁定。
- 高风险工具的测试既要验证 `confirm=True` 路径，也要证明 `confirm=False` 不会进入后端。
- 领域错误映射应在保留底层诊断字段的基础上完成，不能为了改错误名而丢掉清理证据。

### 剩余风险

本节只覆盖模拟进程的软件合同及四平台 CI。没有连接 `COM3` 或真实擦除板卡；因此不能据此
声称板端复位、电源稳定性或真实擦除已经通过。

## 2026-07-27：Monitor STARTING 测试在慢速 Windows runner 偶发为空

### 症状

- 最终发布记录的 main Windows/Python 3.10 job 在
  `test_monitor_stop_while_starting_is_bounded` 失败。
- 失败位置不是 Monitor 返回错误，而是测试执行 `monitors[0]` 时得到
  `IndexError: list index out of range`。
- 同一提交的 main 另外三组和 test 四组全部成功；此前多轮相同生产代码也通过。

### 根因

测试启动后台线程后，只给它 1 秒时间完成 SQLite run 初始化、日志 prepare、Manager 会话注册
和 worker 调度，然后轮询全局状态。`FakeSerial.open_gate` 能阻塞 `open()`，却没有通知测试
“worker 已经进入 open”。在负载较高的 Windows runner 上，启动线程可能在会话注册前尚未取得
足够调度时间，轮询超时后 `monitors` 仍为空。

因此问题是测试同步错误：它用墙钟速度假设代替了线程间确定性事件；生产 Monitor 状态机并未
在该失败中返回错误。

### 修复

- `FakeSerial` 增加每个测试重新创建的 `open_started` 事件。
- 假串口进入 `open()` 的第一步就设置事件，再等待原有 `open_gate`。
- 测试等待 `open_started` 后再读取状态。Manager 在启动 worker 前已经把会话写入
  `_sessions`，因此事件成立时唯一会话必须可见并处于 `STARTING`。
- stop 线程启动后使用既有 `_wait_for_state(..., {"STOPPING"})` 确认状态转换，再释放
  `open_gate`；删除另一个依赖调度速度的 `sleep(0.05)`。
- 仍保留 3 秒有界等待和明确失败消息，避免真正的死锁让测试无限等待。

### 验证

- 原始失败：
  [main run 30211564537](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30211564537)，
  Windows/Python 3.10 为 `1 failed, 103 passed`。
- 修复后单项测试用独立 pytest 进程重复：`20/20` 通过。
- main 本地全量：`104 passed in 16.67s`。

### 经验

- 并发测试应等待“阶段已到达”的事件，而不是假设线程会在固定时间内被调度。
- 超时仍然必要，但应作为死锁上限和诊断边界，不能代替同步原语。
- 单个平台的一次偶发失败应先看失败状态和其他矩阵证据，不能直接回退生产状态机。

### 剩余风险

修复后的远端四平台矩阵仍待当前提交验证。本修改只改变假串口和测试同步，没有读取真实串口，
也没有重新验证真实 Monitor 启停或板卡断电行为。真实串口驱动若让 `open()` 阻塞超过 stop
超时，API 仍会按既有合同报告 `monitor_cleanup_timeout`，并在 `open()` 最终返回后继续清理；
这个边界不是本次 CI 失败的原因，也没有在本修复中改变。

## 2026-07-27：相对 local_path 随 MCP cwd 写入 Codex 安装缓存

### 症状

- 实板验收调用 `esp_file_download`，远端文件为 `/mcp_acceptance_payload.txt`，主机目标为
  相对路径 `index/data/artifacts/exports/mcp_acceptance_20260727/downloaded_payload.txt`。
- run `file_download_20260727_132808_0017d671` 返回 `ok=true`、`bytes_written=21`、
  `truncated=false`，但所选项目 workspace 中没有目标文件。
- 文件实际出现在：
  `C:\Users\16224\.codex\plugins\cache\personal-plugins\esp-mcp-toolchain\0.1.0+codex.20260726165544\index\data\artifacts\exports\mcp_acceptance_20260727\downloaded_payload.txt`，
  内容确实是 `MCP_FILE_TRANSFER_OK`。
- 这不是板端传输失败，而是主机落盘位置错误。工具返回“成功”不能证明文件写到了调用者
  期望的项目范围。

### 根因

Marketplace 的 `.mcp.json` 使用 `"cwd": "."` 是合法配置；安装态下的 `.` 就是版本化插件
缓存根。问题在生产代码把这个进程目录误当成了用户工程目录：

- `esp_file_upload` 的 mpremote 与 Raw REPL 分支使用 `Path(local_path)`；
- `esp_file_download` 的 mpremote 与 Raw REPL 分支使用 `Path(local_path)`；
- `esp_run_file(path_type="local")` 使用 `Path(path)`。

相对 `Path` 由操作系统按进程 `cwd` 解析，因此会从插件缓存读取或向插件缓存写入。工作区外
绝对路径和 `..` 逃逸也没有在这些入口统一拒绝。

既有测试没有发现问题，是因为上传源和下载目标都直接使用 pytest 的绝对 `tmp_path`。绝对
路径不依赖当前目录，所以测试即使全绿，也没有覆盖“安装插件从另一目录启动”的真实合同。
同时，夹具目录位于活动项目 workspace 外，无法直接启用严格项目边界。

### 修复

- 复用项目已有的 `safe_project_path()`，没有新造第二套路径算法。
- 上传、下载和本地运行的五个入口在 `exists`、读取、`mkdir`、写入、mpremote 或 Raw REPL
  调用前解析主机路径。
- 相对路径以活动项目规范化后的 `workspace_root` 为基准；工作区外绝对路径和解析后的父目录
  逃逸返回稳定错误 `unsafe_local_path`，并提示改用项目内路径。
- 成功结果中的 `local_path` / `path` 改为规范绝对路径，调用方可以直接核对实际主机位置。
- 板端 `remote_path` 仍属于 MicroPython 文件系统，没有错误套用主机工作区规则。
- 原有二进制字节保持、20,000 字节 Raw REPL 上限、截断不落盘和已有目标不覆盖合同保持不变。

### 验证

- test 分支先模拟“插件缓存是当前目录”，新增 15 个路径域用例；旧 `main@5985230` 得到
  预期的 `15 failed in 2.60s`。
- 红灯覆盖 mpremote / Raw REPL 上传下载、本地 Raw REPL 文件运行、相对路径绑定、`..`
  逃逸、工作区外绝对路径、拒绝时后端不调用和下载不落盘。
- 修复后 main 文件/执行专项：`32 passed in 4.15s`。
- 修复后 test 加载 main 的路径专项：`48 passed in 9.26s`。
- 提交前 main 全量：`119 passed in 17.64s`。
- 提交前 test 显式加载 main 源码的完整门禁：`243 passed in 31.50s`。
- GitHub Actions、Marketplace validator / 发布测试 / 48-12-12 枚举和用户重启后的实板
  相对下载复验仍待后续步骤；本节不提前把它们写成通过。

### 经验

- 项目级工具不能把 MCP 进程 `cwd` 当成用户 workspace；安装缓存、Marketplace 源和业务项目
  是三个不同边界。
- 路径测试要主动把 `cwd` 切换到无关目录，同时在 workspace 和该目录放置同名不同内容文件，
  才能证明代码读取的是正确来源。
- 只断言 `ok=true` 和字节数不够。文件工具还应断言规范路径、最终内容、项目外无副作用以及
  拒绝时后端未调用。
- 安全路径检查应发生在 `exists`、读写和建目录之前，否则即使最后拒绝，也可能已经泄露文件
  存在性或创建项目外目录。

### 剩余风险

- 旧安装缓存中的 21 字节误写文件不会由修复自动删除；当前没有该主机文件的删除授权，因此
  保持原状，不能通过“顺手清理”扩大权限。
- `@logged_task` 在函数体前创建项目内审计 run；越界请求仍可能产生项目审计记录，但不会
  读取或写入越界文件，也不会调用板端后端。这是现有审计架构的预期行为。
- 未选择串口时，各后端对“端口缺失”和“参数缺失”的错误优先级存在历史差异，本次没有扩大
  范围重构；它不影响已选端口下的工作区安全边界。
- 远程文件管理和其余 12 项实板能力必须在新版插件重载后继续验证；本次软件门禁不能替代
  真实下载落盘证据，也不能把 Raw BIN 恢复冒充为 `build_flash_monitor`。

## 2026-07-27：reset 动作后输出返回成功但未写入 SQLite

### 症状

- `esp_reset` 能在返回值 `text` 中给出动作后的有界串口输出，但该次 run 的 SQLite
  completion 事件只有动作、清理和确认状态，没有可复核的原始输出。
- 调用结束后无法证明当时到底读取了哪些字节，也无法区分“成功捕获 0 字节”和“捕获阶段
  抛错后保留默认 0 字节”。

### 根因

`esp_reset` 只把原始字节替换解码为通用 `text`。`logged_task` 为避免把任意工具 stdout
或潜在敏感内容写入数据库，只持久化固定的 `_RESULT_LOG_KEYS`；`text` 不在该白名单中。
因此工具返回和正式审计记录之间出现信息缺口。直接把 `text` 加入全局白名单会影响全部工具，
不是一个足够窄的修复。

### 修复

- `logged_task` 增加静态 `result_payload_keys` 参数，只允许装饰器为当前工具声明额外完成
  字段；参数必须是由非空字符串组成的 tuple。
- `esp_reset` 局部声明并写入：
  `reset_output_bytes`、`reset_output_sha256`、`reset_output_raw_base64`、
  `reset_output_text`、`reset_output_decode_error`、
  `reset_output_capture_completed` 和 `reset_output_capture_limit_reached`。
- 原始输出仍限制在 65,536 字节；Base64 可无损恢复字节，SHA-256 用于一致性核对，文本只用于
  阅读。捕获调用成功返回后才设置 `reset_output_capture_completed=true`。
- 兼容字段 `text` 和原有确认语义不变；默认工具没有获得通用 `text` 落库权限。

### 验证

- 测试先行时，新增合同分别暴露 `KeyError: reset_output_bytes` 和
  `logged_task() got an unexpected keyword argument 'result_payload_keys'`。
- reset、SQLite 和任务书 prompt 定向门禁：`58 passed in 5.71s`。
- main 全量门禁：`119 passed in 15.18s`。
- test 工作树显式加载 main 源码的全量门禁：`247 passed in 28.82s`。
- 覆盖正常 UTF-8、非法 UTF-8、原始 Base64/SHA-256 一致性、65,536 字节上限、捕获失败
  默认状态、默认不保存 `text`、显式白名单保存和非法白名单拒绝。

### 经验

- 返回给调用方的数据不等于正式审计数据；需要分别验证接口结果和 SQLite completion 事件。
- 原始字节证据应使用有界 Base64 与摘要，不能只存替换解码文本。
- 新增日志字段应按工具最小授权，不能为了修一个工具扩大所有工具的持久化范围。
- “0 字节”必须带捕获完成状态，否则不能判断它是有效测量还是失败后的默认值。

### 剩余风险

- 这些测试使用假串口和临时 SQLite，没有访问真实 `COM3`，不能作为新的板端复位证据。
- `reset_confirmed=false` 与 `output_causality_confirmed=false` 继续保留。当前 reset 会自行
  打开串口；活动 Monitor 已占用同一端口时，仍不能在 Monitor 的唯一句柄内完成复位和建立
  动作前后序列边界。该能力需要后续独立设计和测试，不能由本次持久化修复代替。

## 2026-07-27：4 MiB 实物被构建为 2 MB 镜像头

### 症状

- 已验证的完整 Flash 备份大小为 4,194,304 字节，但 ESP-IDF 示例的本地 `sdkconfig`
  声明 `CONFIG_ESPTOOLPY_FLASHSIZE_2MB=y`。
- 旧 `build/flasher_args.json` 使用 `--flash_size 2MB`，esptool 离线解析 bootloader
  也显示 `Flash size: 2MB`。板端启动因此报告检测到 4 MB、镜像头只声明 2 MB。

### 根因

示例只提交了源文件，没有提交 `sdkconfig.defaults`。`sdkconfig` 又按惯例被 Git 忽略，
所以 Flash 容量完全取决于某台机器第一次生成配置时的选择。物理容量已经确认，但仓库无法
在新环境中复现这一关键构建输入。

### 修复

- 新增 `examples/esp_idf_key_led_buzzer/sdkconfig.defaults`，固定：
  ESP32、DIO、40 MHz、4 MB 和 single-app 分区。
- 不手写 Kconfig 派生字符串 `CONFIG_ESPTOOLPY_FLASHSIZE="4MB"`，也不启用
  `CONFIG_ESPTOOLPY_HEADER_FLASHSIZE_UPDATE`；镜像头在构建阶段生成，保持完整性校验。
- 4 MB 只修正物理 Flash 描述和烧录参数，不扩大 1 MiB factory app 分区，避免把容量修复
  混成分区方案变更。
- 示例 README 明确说明 defaults 不会覆盖已有的 ignored `sdkconfig`。本机现有配置只精确
  修改 2 MB/4 MB choice 和派生值后运行普通 build，没有删除配置或触发 `set-target`。

### 验证

- test 分支先增加静态合同；修复前得到预期的 `2 failed`，分别对应 defaults 缺失和说明缺失。
- 补齐配置与说明后静态合同为 `2 passed in 0.43s`。
- `esp_project_build` run `build_20260727_210357_c45f0c14` 返回成功，命令只执行普通
  `idf.py ... build`，没有 `set-target` 或 fullclean。
- 生成的 write-flash 参数为 `--flash_mode dio --flash_size 4MB --flash_freq 40m`。
- esptool 4.7 离线解析 bootloader 显示 `Flash size: 4MB`、`Flash freq: 40m`、
  `Flash mode: DIO`，校验和与 validation hash 均有效；bootloader SHA-256 为
  `620A1ABEDBFF62995143824B5918B91689DFBB9601E46320D1E16D4DD40CE457`。
- main 全量门禁为 `119 passed in 13.99s`；test 工作树显式加载 main 源码的全量门禁为
  `249 passed in 28.88s`。

### 经验

- 被 Git 忽略的 `sdkconfig` 不能承担仓库级硬件事实；稳定构建选择要写入 defaults 并测试。
- “芯片是 4 MiB”与“应用分区使用全部 4 MiB”是两件事，修镜像头不应顺手改变分区策略。
- defaults 对新配置生效，不会自动重写已有配置；验证现有构建时必须明确处理这一边界。
- 删除 `sdkconfig` 可能让后端进入 `set-target`，后者等价于 fullclean，不能作为无提示的
  配置刷新手段。

### 剩余风险

- 本步骤只构建并离线检查产物，没有访问、擦除或烧录 `COM3`。当前板上仍是此前的镜像；
  只有后续经单独确认烧录并捕获启动日志，才能确认 2 MB/4 MB 启动警告消失。
- single-app 分区仍只有 1 MiB，剩余物理容量没有自动分配。若未来需要 OTA 或更大 app，
  必须作为独立分区设计评审，不能把本次修复当作已经完成。

## 2026-07-27：性能分析返回样本但 SQLite 无法复核

### 症状

- `esp_performance_profile` 返回完整 `samples`、`timing_us` 和 `memory_delta_bytes`，
  但同一 run 的 SQLite completion 事件只有 `iterations`、`profile_kind` 等通用字段。
- 历史 run `performance_profile_20260727_175838_8bcce968` 即使执行成功，也不能通过
  `esp_logs_get` 恢复当时各次迭代的时长、堆变化或失败信息。

### 根因

`logged_task` 为控制日志范围，只持久化 `_RESULT_LOG_KEYS`。性能工具的详细字段没有在该
通用白名单中；reset 切片虽然已经提供按工具声明 `result_payload_keys` 的能力，性能工具尚未
使用它。因此返回合同和正式审计合同不一致。

仅增加白名单仍不安全：板端 `repr(exception)` 原先没有长度上限，marker 也会直接进入
`json.loads`；主机只检查样本是对象，没有检查字段类型、数量、序号或数值范围。这样既可能把
任意长异常永久写入 SQLite，也允许 128 KiB 以下的巨整数进入 `statistics.fmean` 并触发
`OverflowError`。

### 修复

- `esp_performance_profile` 局部声明持久化 `samples`、`timing_us`、
  `memory_delta_bytes` 和 `sampling_profiler`。
- 板端异常文本最多保留 256 字符，并用 `error_truncated` 明确标记；主机再次截断，防止伪造
  或旧版结果绕过板端边界。
- 在 `json.loads` 前按 UTF-8 字节拒绝超过 128 KiB 的 marker；主机只重建固定样本字段，
  丢弃额外键，并核对样本数、连续序号、状态类型、时长上限、32 位堆范围和堆差值。
- completion 事件不保存 stdout、原始 marker 或内联 code，避免把目标源码和人类输出引入
  SQLite；没有扩大所有工具的通用白名单。
- 任务书性能提示词明确成功/失败样本和统计值可按 run_id 复核，同时说明截断不是秘密检测，
  目标代码不得把凭据写入异常信息。

### 验证

- 新增成功与失败样本 SQLite 合同时，旧实现均以 `KeyError: samples` 失败。
- 增加异常长度、marker 上限和样本结构合同时，未补强实现得到预期
  `7 failed, 16 passed in 2.90s`。
- 只读复审进一步用约 4000 位 JSON 整数复现 `OverflowError`；加入时长和堆值上限后，
  巨整数合同返回 `probe_result_invalid`。
- 最终性能专项为 `24 passed in 3.47s`，性能、任务书 prompt 和 SQLite 定向门禁为
  `65 passed in 6.64s`。
- main 全量为 `119 passed in 13.97s`；test 工作树显式加载 main 源码的全量门禁为
  `256 passed in 29.70s`。

### 经验

- 分析结果要能在调用结束后复核；仅把统计打印或返回给当前调用方不等于审计闭环。
- 结构化样本应局部落库，stdout 和待执行源码不应跟随一起持久化。
- 失败样本也属于结果证据，不能只保存成功子集的汇总。
- 输入总大小限制不能替代字段范围验证；一个体积不大的合法 JSON 巨整数仍可能击穿后续统计。

### 剩余风险

- 历史 completion 事件不会被自动补写；旧 run 缺少的样本不能从现有 SQLite 凭空恢复。
- 失败异常的前 256 字符仍可能包含用户数据；这是受限诊断片段，不是脱敏器。目标程序不得把
  口令、令牌或其他凭据写进异常信息。
- 本次测试没有访问板卡或重复执行用户目标。真实性能仍受 MicroPython 版本、GC 状态和目标
  副作用影响；该工具是插桩 wall-time/heap delta，不是采样 profiler，也不能测量电流或功耗。

## 2026-07-27：自动回归只有显式路径执行器，没有可审查套件和持久摘要

### 症状

- `examples/micropython_project` 只有占位 README，没有可提交、可审查、可重复使用的回归文件。
- `esp_regression_test` 把逐项 `results` 和 stdout 返回给当前调用方，但 SQLite completion
  只保存 passed/failed/skipped 等总数，无法复核具体哪一个远程路径失败。
- 响应只有 `program_interrupted=true`，没有区分“未发送复位命令”和“已排除物理复位”。

### 根因

早期实现只解决“按用户给定路径逐个执行”这一层，没有定义默认安全集、硬件分层、negative
合同或日志摘要。直接把完整 results 落库会连带保存任意 stdout；直接信任板端 JSON 又会允许
超长异常、超大 marker、布尔伪装整数或超出捕获窗口的时长进入结果。即使总长度低于 16 KiB，
数千层嵌套 JSON 仍可让标准解析器抛出 `RecursionError`。因此不能只补一个 manifest 或简单
扩大日志白名单。

### 修复

- 新增本地静态 `manifest.json` 与四个受审脚本。默认 safe 不接触 machine、网络或文件写入；
  GPIO34 只读和 GPIO32 LED 状态测试必须显式选择；negative 独立；所有样例排除 GPIO25、
  蜂鸣器和 PWM。
- manifest 不接入工具自动解析：它不证明文件已上传，也不授权执行。工具继续只接受用户确认
  后的精确远程路径。
- `esp_regression_test` 局部持久化固定四字段 `result_summaries`，完整 results/stdout 只留在
  即时响应；板端和主机都把异常限制为 256 字符，marker 在 JSON 解析前限制为 16 KiB。
- 主机只接受布尔 ok、非布尔整数时长、`0 <= duration_us <= capture_ms * 1000`，并要求
  error 与成功/失败状态一致。
- JSON 解析把 `RecursionError` 与类型/语法错误一样收敛为 `probe_result_invalid`，不会让
  异常逃出 MCP 工具边界。
- 返回 `reset_command_sent=false`、`physical_reset_excluded=false`，避免把“代码没有调用
  reset”夸大为“串口打开过程不可能造成物理复位”。

### 验证

- 缺 manifest/脚本和 SQLite 摘要的初始合同在旧实现上得到预期 `2 failed`。
- 增加异常截断、marker 上限和畸形结构合同后，旧实现得到预期
  `9 failed, 3 passed, 20 deselected in 1.43s`。
- 复审用 10,001 字节、5000 层嵌套数组复现未捕获 `RecursionError`；新增单测先失败，修复后
  `1 passed in 0.91s`，completion 不含 results、stdout 或原始 marker。
- 最终回归、prompt 和 SQLite 相关定向门禁为 `74 passed in 7.30s`；main 全量为
  `119 passed in 14.06s`；test 显式加载 main 源码的全量门禁为
  `265 passed in 30.57s`。

### 经验

- “有回归执行工具”不等于“有回归套件”；可审查的用例、默认选择和副作用分层必须落到版本库。
- 审计日志应保存足够定位用例的结构化摘要，而不是为了完整而永久保存任意 stdout。
- 没有显式发送复位命令与已排除物理复位是两个不同证据命题。

### 剩余风险

- 本次没有向板卡上传或执行任何脚本。manifest 的路径只是计划目标，不能作为板上存在证明。
- GPIO32 用例虽然在 finally 中恢复 LED-off，仍会实际改变输出；必须独立确认后运行。
- GPIO34 返回 0/1 只能证明读取有效，不能单独证明用户按键动作发生。
- Raw REPL 会中断现有 MicroPython 程序，且 `physical_reset_excluded=false`；不能把软件模拟
  测试写成实板通过。

## 2026-07-27：Flash 备份路径可越界、覆盖已有文件，默认备份又不能直接恢复

### 症状

- `esp_backup_flash(output_path=<绝对路径>)` 只对相对路径调用 `safe_project_path()`，
  因而外部绝对路径绕过 workspace 边界。
- 后端固定使用 `<final>.part`，启动时无条件删除旧 partial，完成后用 `replace()` 发布；
  已有 final 或未知 partial 可能被覆盖/删除。
- 默认备份位于当前项目 `artifacts/flash`，但恢复只允许 workspace，导致工具自己生成的
  默认备份不能直接传给 `esp_restore_flash`。
- 恢复在调用 esptool 前只校验可变源路径；校验后源文件被替换时，后端可能打开另一份内容。
- 第一轮修复虽然加入 canonical resolver 和 no-replace hard link，独立复审仍发现：
  artifact 根本身可被 symlink/junction 重定向、备份长任务期间输出父目录可被替换、
  发布/清理失败会丢掉或掩盖完整镜像。

### 根因

路径校验、临时文件所有权和发布语义分散在工具层与后端层，没有统一回答三个问题：

1. 哪两个根目录有权限保存 Flash 镜像；
2. 长时间运行的 esptool 应写入谁拥有的临时目录；
3. 目标在运行中出现或文件系统不支持原子发布时，完整镜像应保留还是覆盖/删除。

旧实现把“绝对路径”误当成已可信，把固定 `.part` 误当成当前调用拥有，并把 `replace()`
成功等同于安全发布。恢复侧则把重复读取原路径当成稳定快照，没有建立后端专用副本。

### 修复

- 新增共享 Flash resolver：相对路径绑定当前 workspace；绝对路径只允许位于 canonical
  workspace 或当前项目 canonical `artifacts/flash`。
- `artifacts`、`flash`、`backup-staging` 和 `restore-staging` 在创建前后都检查
  symlink/junction/reparse 与 containment；默认目录创建异常转换为稳定工具错误。
- backup/restore 不共享可写临时文件名；每次调用在项目 staging 下建立独立 `run_<uuid>`
  目录。清理前拒绝 reparse 和非普通文件，Windows 不再对可疑链接执行跟随式 `chmod`。
- staging 的创建被移到输出父目录、已有 final 和旧 `.part` 校验之后；被提前拒绝的无效
  备份请求不会遗留空的 `backup-staging/run_*` 目录。
- partial、restore staging 或运行目录在清理前无法完成 reparse/类型检查时，权限/I/O
  异常会转成 cleanup error；工具保留待检查文件，不再让清理异常覆盖原始操作结果。
- 备份不再让 esptool 直接写用户输出目录，而是写当前项目 `backup-staging` 的 UUID
  partial。后端记录输出父目录和 staging 的设备/文件标识，任务结束后复核目录未被替换。
- partial 必须是普通文件，并通过精确长度与两次 SHA-256/身份复核；final 使用
  create-if-absent hard link 发布，不再使用覆盖式 `replace()`。支持该参数的平台禁用
  symlink 跟随；Windows Python 不实现该关键字时使用经测试的兼容分支，若 hard link
  本身不可用则结构化失败并保留 recovery。
- 已有 final、旧固定 `.part`、运行中出现 final、输出父目录变化均不会被覆盖或删除。
  发布冲突或底层文件系统不支持 hard link 时，完整镜像保留在项目 staging，并返回
  `recovery_path`；成功后的 partial 清理失败保留 `ok=true` 和清理告警。
- 恢复仍先检查 `confirm=True`。确认后把源镜像复制到每次调用独占的当前项目 UUID
  staging，比较复制前后的源身份、长度和 SHA-256；POSIX 收紧文件模式，Windows 使用独占
  运行目录，两者都再次校验 staging 后再交给 esptool。源路径随后变化不会改变已经固定的
  后端输入。
- 未确认 backup/restore 的启动 payload 不再记录未经校验的原始路径和 expected hash；
  已确认结果仍保留规范路径、大小、摘要和清理证据。
- 后端在发布后再次核对 final 长度和 SHA-256，并把 `bytes_read`/摘要作为成功事实返回；
  工具层不再重新按可变 final 路径推导成功大小。

### 验证

- 新合同在旧实现上先得到预期 `8 failed, 15 passed`，覆盖外部绝对路径、`..` 逃逸、
  缺失父目录、已有 final/partial、默认备份不可恢复和发布竞态覆盖。
- 第一轮修复为 `26 passed`；独立只读复审指出上述三类高风险缺口后，没有直接提交，
  而是继续加入 project staging、reparse 拒绝、目录身份复核、恢复 staging 和完整镜像
  recovery 合同。
- 最终 Flash 定向门禁：`36 passed, 1 skipped in 2.67s`。实际目录 symlink 用例因本机
  Windows 缺少创建权限而跳过；同一 reparse 拒绝分支另有不依赖系统权限的确定性测试，
  Linux/远端实际 symlink 验证待提交后的 CI。
- main 全量：`119 passed in 15.53s`。
- test 工作树显式加载 main 源码：`287 passed, 1 skipped in 33.01s`。
- 全部测试使用临时目录和模拟 esptool，没有访问 COM3、读取板卡 Flash、擦除、烧录或恢复。

### 经验

- “绝对路径”不是授权；授权必须来自明确根目录及 canonical containment。
- 固定 `.part` 不能证明文件属于当前调用。随机唯一名称、调用方私有 staging 和只删除自己
  创建的文件缺一不可。
- `replace()` 的原子性只表示切换动作完整，不表示“不覆盖”；安全发布必须单独实现
  create-if-absent。
- 高风险恢复不能把“刚校验过的可变路径”直接交给外部进程；应先建立受控 staging 快照。
- 绿灯后仍需独立审查。第一轮 26 项测试全绿，但没有覆盖根目录 reparse、长任务目录替换和
  hard-link 不支持，审查把这些盲区变成了第二轮合同。

### 剩余风险

- Python 路径 API 无法在 Windows/Linux 上用同一套代码把目录句柄直接传给 esptool。
  当前方案通过项目私有随机 staging、POSIX 只读权限、目录身份和重复摘要把竞态窗口压到很小，
  但不能声称能抵御拥有同一主机账户权限的恶意进程在最后一次校验与 esptool 打开之间的
  定向替换；若威胁模型要求对抗同账户攻击者，需要后续 OS 专用句柄/沙箱设计。
- 不支持 hard link 的文件系统会安全失败并给出 `recovery_path`，不会把备份记为成功。
  recovery 文件不会被自动删除，需在确认内容和目标路径后由显式清理流程处理。
- 本轮没有做真实 4 MiB 备份或恢复；软件门禁不能代替实板数据完整性和供电稳定性验收。

## 2026-07-27：SQLite 只改版本号会把旧表误报为 v3，失败迁移还可能静默丢行

### 症状

- schema v2 已经存在 `raw_logs` / `errors` 空壳表，但运行时没有正式仓储 API。
- 如果只把 `CURRENT_SCHEMA_VERSION` 从 2 改为 3，原 `init_database()` 会把 v2 判定为
  “当前形状”，执行 `CREATE TABLE IF NOT EXISTS` 后直接写 `user_version=3`。
- SQLite 不会用 `CREATE TABLE IF NOT EXISTS` 改造已经存在的表，因此旧表仍缺少路径、
  SHA-256、行列号、recoverable 约束和查询索引，却会被标记成 v3。
- v1 raw/error 迁移原来使用 `INSERT OR IGNORE`；出现主键冲突或新约束不接受的行时，
  迁移可能继续并删除 legacy 表，使问题表现为静默数据丢失。

### 根因

旧迁移流程把“runs/events 列看起来是当前格式”与“整个数据库满足最新 schema”混为一谈，
也把幂等建表语句当成了表结构升级机制。SQLite 的 `IF NOT EXISTS` 只保证对象存在，
不会比较或替换既有定义；而 `INSERT OR IGNORE` 又会把迁移数据问题隐藏成成功。

另一个风险是过早写版本号。若复制之后才发现外键不一致，只有把 rename、建表、复制、
行数检查、外键检查、migration marker 和 `user_version` 全部放在同一个事务里，
才能保证数据库回到可识别的 v2 状态。

### 修复

- schema v3 为 raw/error 增加必要 CHECK 和四个复合索引；数据库层不强制旧 ID 必须为 UUID，
  以便合法历史 ID 原样迁移，新写入则由仓储层强制规范 RFC 4122 UUID。
- 新增 `raw_log_repository` / `error_repository`。raw ID 由项目、run、kind 和规范路径
  生成；error ID 还要求调用方提供稳定 occurrence key（后续使用来源 event UUID），
  使同一次异常可严格重试、相同异常的第二次发生可生成另一条记录。
- 仓储层规范项目/run/kind、相对 POSIX 路径、SHA-256、时区时间戳、正整数行列号和
  recoverable，并在 INSERT 前检查复合 run 外键。
- 新增显式 `_migrate_v2()`：在 `BEGIN IMMEDIATE` 中把两张旧表改名，应用 v3 schema，
  使用严格 INSERT 复制全部列，核对两表行数，删除 legacy 表后再次应用 schema 以避免旧
  index 名暂时占用，最后运行 `foreign_key_check`。
- 只有上述步骤全部通过后才新增 v3 migration marker 并写 `user_version=3`。
  Constraint 或最终外键检查失败统一抛 `database_migration_error` 并回滚。
- v1 raw/error 复制也取消 `OR IGNORE` 并核对行数；不合规数据现在阻止迁移，不会被静默删除。
- 对已经盖章 v3 的数据库不再只看列名：初始化会核对两表复合主键、同一组复合外键、
  ON DELETE CASCADE、四个索引的精确列序，并在可回滚 savepoint 中证明非法路径、SHA、
  行号类型和 recoverable 确实被 CHECK 拒绝。
- v2 重建前先用 `PRAGMA table_info` 核对精确复制列集合；缺列时在 rename 前失败，避免
  SQLite 把双引号中的未知列名兼容解释成字符串常量并写入伪造数据；存在额外扩展列时也
  失败并保留原库，避免白名单复制后 DROP legacy 静默删除扩展证据。

### 验证

- 新增合同在旧实现上先得到预期 `20 failed in 0.52s`。
- 第一轮实现后基础合同 `20 passed`；独立复审指出假 v3、v2 缺列字符串伪造、额外列静默
  丢失和重复异常 identity 阻断项后，继续加入 PK/FK/CHECK/同名错索引验证、精确列集合
  预检、重复与双线程升级、复制完成后的外键晚失败回滚、v1 raw/error 直升和
  occurrence-aware UUIDv5 测试。
- v3-A 最终专项：`33 passed in 1.79s`。
- 既有 SQLite 合同与 v3-A 合并定向：`68 passed in 5.34s`。
- main 全量：`119 passed in 14.89s`。
- test 工作树显式加载 main 源码：`320 passed, 1 skipped in 34.84s`。
- 全部新增测试只创建临时数据库，没有打开或迁移正式项目数据库，没有访问 COM3 或板卡。

### 经验

- schema 版本号是结论，不是迁移动作；只有结构和数据验证完成后才能写入。
- `CREATE TABLE IF NOT EXISTS` 适合新建和补缺，不能代替既有表重建。
- 数据迁移不应使用会隐藏失败的 `OR IGNORE`，除非每一条被忽略的业务语义都有单独审计。
- 回滚测试既要覆盖复制时的早失败，也要覆盖复制完成后外键检查的晚失败。
- 新库合同与升级后合同必须共用同一组结构断言，否则容易得到“新库正确、旧库假升级”的绿灯。

### 剩余风险

- v3-A 只提供表结构、迁移和底层仓储；capture、Monitor、错误解析和 MCP 查询还没有写入或
  读取新仓储，必须在 v3-B/v3-C 分步接入。
- 正式项目数据库仍是 v2，当前安装插件也只支持 v2。必须等 Marketplace 更新、用户重启并
  确认新插件后才能迁移正式库，否则旧插件会拒绝 v3。
- 历史 JSONL marker 已存在，不能靠再次运行旧 importer 回填 raw/error；后续需要独立、
  可重复、带自身版本标记的 reconciliation。

## 2026-07-28：固定串口 capture 会同秒覆盖，并把非法 UTF-8 改写后冒充原始日志

### 症状

- 原始路径只由 `session_name + 秒级时间` 组成；同一 session 同一秒执行两次会得到同一路径，
  第二次 `write_text` 会覆盖第一次文件。
- 串口数据先以 `errors="replace"` 解码，再通过 `write_text` 写盘。非法 UTF-8 字节会被
  U+FFFD 替换，文件不再是板端真实输出。
- `bytes_read` 计算替换文本重新编码后的长度。测试中的 15 个实际字节因此被报告为 19。

### 根因

实现把“给人阅读的容错文本”和“用于审计、哈希、重放的原始字节”当成同一种数据；同时把
秒级时间戳误当成唯一文件身份。普通覆盖写入没有 create-if-absent 约束，因此无法证明旧证据
未被替换。

### 修复

- 内存中保留每个串口 bytes 分片；只把临时解码文本交给 Traceback detector 和即时响应。
- 结束后直接把拼接后的 bytes 写入 `.log`，`bytes_read` 使用原始长度。
- 文件名加入 UUID 后缀，并使用排他 `xb` 创建；出现极小概率冲突时重新生成，最多尝试十次。
- 写入完成后执行 flush + fsync，再返回路径；文本视图仍保留替换解码语义。
- `logs/raw` 在串口打开前创建；目录准备失败返回结构化错误并证明串口未打开。
- open/write/flush/fsync/close 或碰撞耗尽统一收敛为 `serial_capture_persist_failed`。
  已创建但持久化状态不确定的文件只返回为 `recovery_path`；结果保留真实 bytes、文本和串口
  清理状态，不要求调用方重复已经发生的串口动作。
- recovery path 只在本次调用成功取得排他文件句柄后出现。open 失败和十次碰撞耗尽不返回
  路径，避免把不存在的目标或别人的 sentinel 归到本次 capture。
- 文件句柄 close 失败设置 `persistence_cleanup_completed=false` 并保留
  `persistence_close_error`；`cleanup_completed` 继续只描述串口资源清理。

### 验证

- 两个测试先在旧实现稳定红灯：15 字节被报告为 19；固定秒级时间后两次 capture 路径相同。
- 独立复审发现 fsync/close 异常会逃出工具、目录准备被延后，并指出原同秒测试未强制碰撞。
  新合同先复现 fsync 的未捕获 `OSError`，随后强制首个 UUID 命中 sentinel，验证内容不变
  且第二个 UUID 排他创建成功；另覆盖目录准备失败不打开串口和读取失败后的字节计数。
- 第二轮终审继续补入十次 UUID 碰撞耗尽与 fake handle close 失败合同，验证所有 sentinel
  不变、结果没有错误 recovery path，并区分串口清理与文件持久化清理。
- 完整错误检测文件 `25 passed in 3.57s`。
- main 全量 `119 passed in 15.21s`；test 工作树显式加载 main 源码
  `327 passed, 1 skipped in 35.56s`。
- 全部使用假串口与临时项目目录，没有连接 COM3、运行板端代码或改正式 SQLite。

### 经验

- 原始证据和展示文本必须分层；替换解码适合 UI，不适合哈希、审计或重放。
- 时间戳适合排序，不是唯一键；不可覆盖证据必须同时使用高熵身份和排他创建。
- 只有 write 返回不代表持久化完成；对证据文件应在返回前 flush + fsync。

### 剩余风险

- 文件落盘与 SQLite 是两个介质，无法共享一个原子事务。后续 v3-B 应先封存文件，再以同一
  SQLite 事务提交 completion event、raw row 和 error row；数据库失败时保留文件供独立对账。
- 本次未把 capture 接入 `raw_logs` / `errors`，也未处理 Monitor chunk 或历史 manifest。
- 软件假串口结果不能证明 COM3 的供电稳定性、驱动行为或真实丢字节情况。
