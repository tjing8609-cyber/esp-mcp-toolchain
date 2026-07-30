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

## 2026-07-28：completion、raw 和 error 分开写会留下“任务已完成但证据缺失”的假完整记录

### 症状

- SQLite 已有 `events`、`raw_logs` 和 `errors` 仓储，但固定 capture 和程序停止完成后只写
  completion event；原始文件与结构化错误没有进入正式仓储。
- 如果先写 event、再分别写 raw/error，中间任一步失败会留下 completion-only 记录。
  查询端会看到“任务已经完成”，却无法区分证据确实不存在还是审计只写了一半。
- 直接信任工具结果里的绝对 `raw_path` 会允许项目外文件、大小不符文件或
  symlink/junction/reparse 目标被登记为当前项目的正式原始证据。
- capture 持久化失败时返回的 `recovery_path` 只表示“可能需要人工保存的部分文件”，
  不能冒充已经完成 fsync 并通过校验的正式 raw。
- 程序停止成功时常见 `KeyboardInterrupt`。若对所有输出自动做结构化异常投影，会把正常
  Ctrl-C 停止证据误报为运行时错误。

### 根因

原日志装饰器只有“写一个 completion event”的接口，raw/error 注册是彼此独立的事务；
调用方还可以自行提供 ID、project/run 和 created_at。这样既不能保证同一 occurrence
共用 event UUID 和时间，也不能保证 event、证据和 sequence 同时提交。

另一个根因是隐式扫描策略。若所有工具都根据 `raw_path`、`error_report` 或输出文本自动
推断证据，普通工具返回的同名字段也会被错误提升为正式审计记录；程序停止中的预期
`KeyboardInterrupt` 就属于这种误判。

### 修复

- 新增 frozen `RawLogArtifact`、`ErrorArtifact` 和 `EventArtifacts`。输入不包含
  project/run、数据库 ID 或 created_at；仓储从本次 event 的项目、run、规范时间和
  occurrence key 统一生成稳定 UUIDv5。
- 新增 `append_event_with_artifacts()`，在一个 `BEGIN IMMEDIATE` 中按
  event → raw → error → sequence 顺序执行；只有新 event 才递增 sequence。任一仓储冲突
  或 SQLite 写错都回滚整个事务，并统一为 `artifact_projection_failed`，同时保留 cause。
- 旧 `append_event(...)->(event, inserted)` 委托新接口的空证据路径，保持调用方式和
  幂等行为不变。已经结束的 run 只允许使用完全相同的 completion UUID/内容补齐缺失证据，
  不允许追加新 event，也不增加序号。
- `logged_task` 在业务返回后只生成一次 completion UUID 和时间戳，证据构建和事务提交
  共用这两个值。构建或提交失败时禁止退化为 completion-only 写入；业务 `ok`、
  `error_kind` 和 `message` 不变，另外返回 `logging_persisted=false` 和 warning，并继续
  尝试按业务结果结束 run。
- artifact 投影必须由每个工具显式声明。固定 capture 启用
  `serial_capture_raw/result_error/structured_error`；程序停止只启用 `result_error`。
- capture 只有在 `ok is True` 时才登记 raw，并要求绝对路径位于当前项目
  `logs/raw`，路径组件和文件不是 reparse，打开句柄是普通文件，实际大小等于
  `bytes_read`，哈希从文件实际内容计算。`recovery_path` 从不参与 raw 投影。
- result error 与 structured error 分别使用
  `event:<uuid>:result_error` 和 `event:<uuid>:structured_error`，所以“持久化失败且同时
  捕获 traceback”会形成两条可重试但不互相覆盖的记录。
- `esp_program_stop` 不解析输出生成 structured error；只有 `ok=false` 且存在顶层
  `error_kind` 时登记失败。成功停止中的 `KeyboardInterrupt` 仍保留为停止证据，不进
  errors 表。

### 验证

- 旧实现上的首轮合同为预期 `11 failed, 1 passed`。
- 最终 15 项专项覆盖：原子提交与严格重试、晚阶段冲突回滚、意外 SQLite 错误包装、
  终态 event 补齐、旧二元组兼容、规范化共享时间、稳定 ID、可信 raw 成功路径、项目外/
  大小/reparse 拒绝、recovery 排除、capture 双错误、程序停止 KeyboardInterrupt 语义，
  以及日志失败不篡改业务成功或失败。
- 专项 `15 passed in 1.61s`；main 全量 `119 passed in 14.54s`；test 工作树显式加载
  main 源码 `342 passed, 1 skipped in 35.69s`。
- 两轮独立只读终审均为 P0=0、P1=0。全部新增测试使用临时 SQLite、临时项目和假串口，
  没有迁移正式项目数据库，没有访问 COM3、执行板端程序、擦除或烧录。

### 经验

- “任务完成”与“证据完整”必须是一个数据库事务的结论，不能依赖三个先后成功的写操作。
- 稳定 ID 解决重复提交；同一 occurrence key 加内容才定义同一错误，不能只按异常文本去重。
- 工具返回字段不是天然可信证据。正式 raw 必须从受控项目路径重新核验文件类型、大小和哈希。
- 错误检测策略必须按工具声明；正常控制流中的异常文本不能靠通用字符串扫描决定业务语义。
- 日志失败不应反向改写已经发生的业务动作，但必须显式暴露审计缺口，不能静默假装完整。

### 剩余风险

- raw 文件和 SQLite 仍跨两个介质；文件完成后、事务提交前的崩溃会留下未登记文件，必须由
  后续独立、可重复的 reconciliation 回收，不能复用旧 JSONL importer marker。
- 路径核验已经检查 reparse、打开句柄的大小、设备号、inode 和实际读取长度，但 Python
  路径 API 不能彻底消除同一主机账户恶意并发替换造成的最后窗口；当前威胁模型是普通本地
  工具链，不声称抵御同账户攻击者。
- 本切片没有接入 Monitor chunk、历史 manifest/JSONL 或 v3 查询；这些分别属于
  v3-B3、v3-B4 和 v3-C。
- 正式项目数据库和当前安装插件仍为 v2；只有 Marketplace 源更新、用户重启并确认新插件后，
  才能另行执行正式 v2→v3 迁移。

## 2026-07-28：v3-B3 双分支 CI 暴露 fd 所有权、分支同步和 POSIX fail-closed 合同问题

### 症状

- main 首轮 [run 30330801910](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30330801910)
  的 Linux/Python 3.10 在测试 teardown 报 `Bad file descriptor`；Windows 和其他矩阵
  没有稳定复现。
- test 首轮 [run 30330806829](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30330806829)
  在收集阶段无法导入 v3-B3 新符号。test 并非缺少 v3-B2，而是产品代码只同步到 B2，
  新增 B3 合同时没有同步 B3 产品实现。
- 同步实现后，Linux 真实 symlink 用例在读取 `sqlite-artifacts-v1.json` 时得到
  `FileNotFoundError`；原测试错误地要求恢复预检拒绝后仍生成 failed sidecar。

### 根因

- `_safe_binary_reader` 把整数 fd 交给 `fdopen(closefd=True)` 后，外层 `finally` 又调用
  `os.close`。file object 已经关闭 fd 后，操作系统可把同一整数编号分配给另一线程；
  第二次关闭会误关新资源。这是所有权错误，不是普通“多关一次无害”。
- 本地 `index-test` 可用 `ESP_MCP_SOURCE_ROOT` 加载另一工作树的 main 源码，但 GitHub
  Actions 只检出被推送的 test 分支。本地跨工作树绿灯没有证明 test 分支自身包含 B3 实现。
- 安全流程在 `_require_safe_regular_file` 预检阶段发现 symlink 后就返回 recovery error，
  尚未冻结可信 terminal marker，也没有进入 SQLite 对账。原测试把“进入对账后失败”的
  sidecar 语义套到了“写入前拒绝”的路径。

### 修复

- `fdopen` 成功后由 file object 单独拥有关闭责任；只有 `fdopen` 构造失败时，原始
  descriptor 才由失败清理路径显式关闭一次。新增成功所有权转移与构造失败各一条合同。
- 把固定 `main@98d9403` 合入 test；test 专属差异继续只维护测试和验证脚本，README
  恢复为 main 权威版本。推送 test 前必须验证 main 是其祖先。
- 保持生产 fail-closed 代码不变。symlink 测试改为直接断言 recovery error 含 reparse、
  manifest 原始 bytes 不变、上层报告 `artifact_marker=None`、sidecar 不存在、SQLite
  raw 为空且外部目标内容不变。

### 验证

- main 本地 Conda 全量：`119 passed in 40.92s`。
- test 本地 Conda 标准全量：`387 passed, 3 skipped in 229.87s`。
- test 显式加载 main 的 Conda 跨工作树全量：`387 passed, 3 skipped in 247.38s`。
- [main run 30333882504](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30333882504)
  和 [test run 30334699560](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30334699560)
  共 8 个 Windows/Linux、Python 3.10/3.12 job 全部成功。Linux 两组实际执行 symlink
  用例，证明了本机 Windows 权限 skip 没有掩盖远端合同。
- 本轮只使用临时工程、临时 SQLite 和模拟对象；没有访问 COM3、升级正式 v2 数据库、
  更新 Marketplace 或修改安装缓存。

### 经验

- fd 的整数编号不是稳定资源身份；资源所有权一旦转交，就不能在旧作用域再次释放。
- 本地跨工作树测试证明“测试可在指定源码上通过”，不证明远端单分支 checkout 已同步实现。
- 安全测试应断言最早拒绝边界和零副作用；不能为了得到错误 sidecar 而让不可信输入进入写阶段。

### 剩余风险

- v3-B3 已完成软件与远端门禁，但正式项目数据库和已安装插件仍为 v2。后续仍需完成
  v3-B4 历史对账、v3-C 查询接入、Marketplace 源更新和用户重启确认，才能另行授权正式升级。

## 2026-07-28：历史证据补投影缺少终态 event 资格和统一事务错误边界

### 症状

- 初版 `reconcile_existing_event_artifacts()` 只确认 run 已结束。终态 run 中最后一个
  `prepare` event，或后面已有更高 sequence 的旧 `complete` event，仍会接受 raw/error。
  这会把证据挂到不是该 run 最终完成事实的事件上。
- 对 running run 提供随机、其他 run 或其他 project 的 event UUID 时，初版先返回
  `run_not_terminal`，随后才验证 event 归属；错误身份尚未通过边界校验就暴露了目标 run 状态。
- `ErrorArtifact.created_at` 非法时抛出原始 `InvalidEventError`；`connection.commit()`
  失败时抛出原始 `sqlite3.Error`。两者虽由外层回滚，但没有遵守统一
  `artifact_projection_failed` 契约。

### 根因

- “run 已终态”被误当作“给定 event 是终态 event”。run 状态只能证明生命周期已经结束，
  不能证明 event 的 phase 和在该 run 中的位置。
- 校验顺序以目标 run 状态为先，没有先建立 project/run/event 的完整作用域绑定。
- artifact 投影的异常捕获只包含 raw/error 仓储错误和循环内部 SQLite 错误，漏掉
  `normalize_timestamp()` 使用的 `EventRepositoryError`；`commit()` 又位于捕获块之外。

### 修复

- 新增 `EventNotTerminalError(error_kind="event_not_terminal")`。补投影前同时要求
  `event.phase == "complete"` 且
  `event.sequence_no == run.next_sequence_no - 1`；已结束 run 的其他 event 全部拒绝。
- 顺序改为：先 scoped 获取 run，再核对 event UUID 的 project/run 归属；只有绑定正确后
  才检查 run 是否仍为 running，避免用越界 event 探测运行状态。
- event 时间规范化、raw/error 稳定 ID 与写入、以及最终 commit 全部放入同一
  `BEGIN IMMEDIATE` 投影错误边界。`RawLogRepositoryError`、`ErrorRepositoryError`、
  `EventRepositoryError` 和 `sqlite3.Error` 均包装为 `ArtifactProjectionError`，
  保留 `__cause__`；外层对任何失败完整 rollback。
- API 仍然 existing-only：没有 run/event INSERT 或 UPDATE，不改变 sequence；完全相同
  bundle 重试返回 `inserted=false`，同 bundle 两个并发调用最终只插入并保留一组记录。

### 验证

- B4.1 旧基线缺少 API 时，首轮 8 项合同为预期 `8 failed`；实现初版通过 8 项。
- 独立复审补入终态 event、binding 优先级、非法时间戳和 commit 故障后，初版得到预期
  `4 failed, 7 passed in 1.40s`，证明四个缺口各自可复现。
- 修复后 B4.1 专项 `11 passed in 1.78s`；SQLite 相关
  `146 passed, 2 skipped in 50.21s`；main 全量 `119 passed in 49.32s`；
  test 显式加载 main 的跨工作树全量 `398 passed, 3 skipped in 239.99s`。
- 第二轮独立只读复审为 P0=0、P1=0。全部测试使用临时项目和临时 SQLite；未访问 COM3、
  未执行板端程序、未升级正式数据库、未更新 Marketplace 或安装缓存。提交后的双分支远端
  矩阵尚待验证。

### 经验

- 终态 run 与终态 event 是两个不变量；补历史证据时必须同时验证 phase 和最后序号。
- 身份与作用域校验应先于状态信息返回，避免越界标识符成为状态探针。
- 原子性不仅是“失败会 rollback”，还包括所有同类失败使用稳定的上层错误契约；commit
  本身也是事务的一部分，不能放在包装边界之外。
- 幂等测试必须同时验证成功重试和并发重试；回滚测试必须证明前序 raw 确实已插入事务，
  才能排除“根本没执行写入”的假绿。

### 剩余风险

- B4.1 只提供既有终态 event 的仓储补投影原语，不扫描文件，也不解释旧 manifest/JSONL。
  B4.2-B4.4 仍需分别实现历史 Monitor resolver、固定 capture/JSONL adapter 和项目级
  启动/状态报告。
- 正式项目数据库与已安装插件仍为 schema v2；只有 B4/v3-C 软件门禁、Marketplace 源同步、
  用户重启确认均完成后，才能另行申请正式 v2→v3 升级。

## 2026-07-28：Monitor 断连终态被测试误当作线程清理完成

### 症状

- B4.1 首轮 [main run 30338443462](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30338443462)
  的 Windows/Python 3.12 在
  `test_monitor_disconnect_preserves_buffer_and_terminal_reason` 失败，结果为
  `1 failed, 118 passed`。
- 失败断言是状态已为 `DISCONNECTED`，但 `worker_alive` 在固定 1 秒轮询后仍为 true。
  同一 main 的另外 3 个 job 与
  [test run 30338445078](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30338445078)
  的 4 个 job 均成功。

### 根因

- worker 捕获断连后先切换到 `DISCONNECTED`，随后才在 `finally` 中关闭串口、释放 lease、
  关闭日志存储并执行 SQLite/JSONL 终态对账。`worker_alive` 直接读取线程存活状态，因此
  这段收尾期间合法地仍为 true。
- `DISCONNECTED` 的合同是“终止原因已经确定”，不是“所有资源已经清理”。旧测试用固定
  1 秒轮询等待另一个不变量，既没有同步事件，也没有调用 API 已提供的 join 屏障。
- B4.1 没有改变这段生产状态机；Windows/Python 3.12 只暴露了从旧提交开始潜伏的时间竞态。
  现有证据不能进一步确定该次 runner 的具体调度或磁盘延迟来源，因此不作猜测。

### 修复

- 保留生产状态迁移顺序，避免延迟 `DISCONNECTED` 导致读端不能及时返回断连前缓冲。
- 断连原因断言完成后，测试调用
  `esp_serial_monitor_stop(run_id, timeout_ms=5000)`。该 API 使用线程 join，是明确的
  cleanup barrier；随后断言状态仍为 `DISCONNECTED`，且 `worker_alive=false`、
  `log_store_closed=true`、`cleanup_complete=true`。
- 新增 Event 门控回归，故意阻塞 terminal reconciliation。`timeout_ms=0` 必须返回
  `monitor_cleanup_timeout` 和存活 worker；释放 Event 后再次 stop 必须完成。这样真正的
  清理卡死不会被当作偶发慢测试跳过。
- 自动 fixture 在每项测试前后调用 `shutdown_all(5)` 并断言成功，防止残留 worker 静默
  污染后续用例。

### 验证

- 两项针对性测试：`2 passed in 1.31s`。
- 两项测试使用独立 pytest 进程连续重复：`30/30`。
- Monitor 测试文件：`23 passed in 35.85s`。
- main 全量：`120 passed in 50.72s`。
- 同步后的 test 分支自身源码：`399 passed, 3 skipped in 249.96s`。
- 两轮独立只读审查均认为不需要修改生产状态机；最终复审 P0=0、P1=0。
- 修复后的 [main run 30340384047](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30340384047)
  与 [test run 30340395467](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30340395467)
  共 8 个 Windows/Linux、Python 3.10/3.12 job 全部成功。本地与远端测试使用假串口、
  临时项目和临时 SQLite，没有访问 COM3、板卡、正式数据库、Marketplace 或安装缓存。

### 经验

- 业务终态和资源清理完成是两个不同状态；测试必须分别选择对应的同步信号。
- 固定等待只能作为死锁上限，不能替代 Event 或 join。
- 修复并发测试时应增加“故意卡住仍会显式超时”的反例，证明新等待没有掩盖真实泄漏。
- 单个 CI job 暴露旧竞态时，应先沿生产状态机确认不变量，不能为了测试变绿改变正确语义。

### 剩余风险

- 新测试只覆盖模拟断连和线程清理合同，不证明真实 USB 断连、瞬时电流导致的掉线或板卡供电
  稳定性。
- 本次软件门禁不证明真实 USB 驱动在所有断连时序下都能于 5 秒内完成清理；实板断连仍需
  单独的受控验收。

## 2026-07-28：历史 Monitor 的旧绝对路径不能作为当前证据来源

### 症状

- v1 Monitor manifest 把 chunk 保存为生成时工作区的绝对路径。工程移动、复制或从另一
  操作系统恢复后，该路径可能已经失效，或者指向当前项目边界以外的对象。
- B4.1 只负责把证据原子补入指定的既有终态 event；它不会选择、读取或解释历史文件。
  如果把文件发现、数据库资格判断和写入揉在一个入口中，损坏历史也可能先产生锁、SQLite
  或 sidecar 副作用，失败边界过大。

### 根因

- 旧绝对路径是当时的描述性元数据，不是跨迁移后的文件权威。当前文件权威必须来自活动
  `LogScope` 下的 `logs/serial/<run_id>`。
- 项目此前缺少一个独立于 SQLite 的纯文件 resolver，也没有在该层明确区分普通 v1 历史、
  B3 sidecar/旧 ownership 与释放后仍按设计保留的 lease 文件。

### 修复

- 新增 `resolve_historical_monitor_artifacts()`。调用方必须给出规范的既有 event UUID；
  resolver 只从当前项目的固定 run 目录读取 `manifest.json` 和由 chunk ID/name 派生的
  finalized chunk。
- v1 的 Windows 盘符或 POSIX 旧绝对路径只作控制字符、`.`/`..`、本地绝对路径和
  `serial/<run_id>/<chunk>` 后缀的词法校验；旧字符串绝不传给
  `Path/stat/open/resolve/exists`。UNC、Windows 设备路径和根相对路径均拒绝。v2 只接受
  规范 `name` 并禁止 `path`。
- manifest 从同一安全 fd 完成大小、UTF-8/JSON、身份和 SHA-256 复核；返回前后比较
  project/log/serial/run 目录身份，并验证终态、时区时间、精确 chunk 集、连续 ID、
  `persisted_bytes`、实际长度和 SHA-256。
- B3 sidecar 或旧 ownership 字段会 fail-closed；陈旧/缺失 `process_owner` 不作为当前
  所有权。`.sqlite-artifacts.lock` 释放后不会删除，而且 B4.4 必须持该 lease 重跑
  resolver，因此锁文件本身不能作为 B3 ownership。
- 返回值明确区分 `resolved` 与合法的 `no_artifacts`，并通过规范 JSON 快照避免调用者修改
  manifest 的 `last_error`。resolver 不获取 lease、不连接 SQLite，也不写 manifest、
  sidecar、JSONL 或 latest。

### 验证

- 入口缺失的旧基线为预期 `42 failed`。独立复审继续增加陈旧/缺失 owner、caller
  `run_id` 越界、log/serial/run reparse、目录身份变化、持久 lease、合法 POSIX v1
  和 Windows 根相对路径拒绝合同，最终 B4.2 专项 `58 passed in 1.66s`。
- 既有 Monitor 回归 `28 passed in 41.00s`；最终源码 main 全量
  `120 passed in 51.67s`；合入固定 main 后 test 分支自身源码全量
  `457 passed, 3 skipped in 249.69s`。
- 正式项目 22 个 v1 manifest 的只读检查得到 14 个 `resolved`、8 个
  `no_artifacts`、0 个错误；Monitor 文件与正式 SQLite 文件元数据前后不变。
- 本轮没有连接或写正式 SQLite、升级 schema、访问 COM3、执行板端程序、更新 Marketplace
  或安装缓存。

### 经验

- 路径字符串不是文件权威；迁移后的文件位置必须由当前项目根和受限相对身份重新派生。
- “解析文件候选”和“持 lease 校验数据库资格并应用”必须分层。这样损坏输入能在零数据库
  副作用的阶段失败，也避免把 B4.2 候选误称为已对账。
- `no_artifacts` 是合法历史结果，不应伪装成“已写入”或“已 reconciled”。

### 剩余风险

- B4.2 candidate 不是数据库资格或已对账证明。B4.4 必须在 run lease 内重新调用 resolver，
  严格校验 native run/event profile 和最后一个 `complete` event，再调用 B4.1 并发布
  项目级状态/marker；不能缓存 lease 外候选直接写库。
- B4.3 仍需为历史固定 capture/JSONL 建立独立 adapter 和 reconciliation 版本；不得复用
  legacy JSONL import marker。
- 当前前后身份检查面向正常本地工具链故障与普通路径替换，不声称抵御同一主机账户在检查
  间隙完成“替换为链接、读取、再原样恢复”的恶意 ABA。若威胁模型扩大到该级别，需要固定
  祖先目录句柄并使用目录相对打开。
- 正式项目数据库和已安装插件仍为 schema v2；B4.3/B4.4、v3-C、Marketplace 同步和用户
  重启确认完成前，不得执行正式 v2→v3 升级。

## 2026-07-28：Windows 锁文件零长度窗口会把正常竞争误报为永久失败

### 症状

- B4.2 推送后的 test run `30345364620` 在 Windows/Python 3.10 的
  `test_concurrent_reconciliation_commits_one_deterministic_bundle` 失败；同一轮其余
  3 个 test job 和 4 个 main job 成功。
- 失败线程没有返回 `monitor_artifact_reconciliation_busy`，而是
  `monitor_artifact_reconciliation_failed`：
  `SerialLogStoreError: Monitor artifact reconciliation lease could not be acquired:
  [Errno 13] Permission denied`。
- 本机使用独立 pytest 进程在第 4 次复现；受控锁测试进一步把异常固定在
  `SerialRunReconciliationLease.acquire()` 的加锁前 `handle.flush()`。

### 根因

- owner 已锁定 byte 0 后，会在持锁范围内把 `.sqlite-artifacts.lock` 截断为 0，再写入
  新 metadata。旧 contender 路径在真正调用 `_lock_reconciliation_file()` 前先执行
  `fstat()`；若正好看到 owner 的零长度窗口，就会先写一个占位字节。
- Windows byte-range lock 会拒绝第二个 handle 写入 owner 已锁定的 byte 0，因此 buffered
  write 在 `flush()` 抛出 `PermissionError [Errno 13]`。它发生在 busy 分类函数之前，
  外层只能把它包装为普通 `SerialLogStoreError`。
- 这不是 SQLite 事务、B4.2 resolver 或幂等 event UUID 的错误，也不应通过扩大 errno
  busy 映射来掩盖；真实权限和磁盘错误仍必须保持普通失败。

### 修复

- 删除加锁前的零长度占位写。空锁文件打开并完成普通文件/reparse 校验后，立即尝试
  `_lock_reconciliation_file()`；只有成功持有 lease 才执行已有 metadata
  truncate/write/flush/fsync。
- Microsoft `_locking` 和 Windows byte-range lock 均允许锁定超过当前 EOF 的区域，因此
  空文件不需要先物理写入 byte 0。POSIX `flock` 同样不要求文件非空。
- 保留非阻塞语义：owner 仍持锁时 contender 返回 recoverable busy；owner 已释放时 contender
  可取得锁并执行确定性幂等重试。

### 当前验证

- test 分支的 Windows 专项合同先在旧实现上稳定得到预期红灯，并保留真实异常栈。
- 修复后，受控的 zero-length owner、busy contender、release/reacquire 检查通过。
- 双线程独立循环 2000 次共得到 2004 次正常 acquisition、1996 次 busy、0 次普通失败。
- 新增合同和原并发测试 `2 passed`；原并发测试使用独立 pytest 进程重复 `100/100`；
  main compileall 和全量 `120 passed in 51.01s`；合入固定 main 后 test 分支自身源码
  `458 passed, 3 skipped in 256.55s`。
- 两轮只读审查均为 P0=0、P1=0。修复后的
  [main run 30347587842](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30347587842)
  与 [test run 30347592644](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30347592644)
  共 8 个 Windows/Linux、Python 3.10/3.12 job 全部成功，未执行重跑。
- 本阶段只使用临时目录、假串口和临时 SQLite；没有访问 COM3、板卡、正式数据库、
  Marketplace 或安装缓存。

### 经验

- 锁文件的任何共享 metadata 写入都必须发生在取得 OS lease 之后；即使是“确保文件非空”
  的占位写也属于共享写入。
- 并发测试允许两种合法调度：一次成功加一次 busy，或 owner 释放后两次幂等成功。测试必须
  拒绝第三种普通失败，但不能强迫调度一定产生 busy。
- CI 单点并发失败应先保留失败 run、打印实际 report 并构造可控时序，不应靠重跑判断修复。

## 2026-07-28：历史固定 capture 缺少独立证据 adapter，旧文本还可能被误称原始字节

### 症状

- 正式项目有 4 份固定 capture JSONL/raw 配对，但 v3-B2 只处理新调用的 completion
  事务；已存在的文件没有纯只读入口生成 B4.1 所需 `EventArtifacts`。
- 3 份早期 JSONL 是无 `event_uuid/phase/bytes_read` 的单行 legacy 记录，1 份是
  `prepare + complete` 的 native mirror。旧 importer marker 已存在，但 raw/error
  仓储仍没有这些证据。
- 旧 capture writer 先 `decode(errors="replace")` 再 `write_text`。正式 720 B 文件的
  message 是 `707 characters`，说明字符数不能当作原始字节数，文件内容也不能诚实声明为
  exact serial bytes。

### 根因

- `legacy_jsonl_imports` 只以 project、绝对 source path 和 source SHA-256 记录“事件导入
  处理过”；它不校验当前 raw 文件、artifact bundle、最后 completion 资格或 adapter
  版本，因此不能复用为历史证据 reconciliation marker。
- legacy event 按 importer 会成为 `phase=unknown`。B4.1 只接受终态 run 的最后一个
  `complete` event；若 adapter 暗中改 phase、创建 completion 或直接调用 B4.1，会破坏
  已冻结的历史身份合同。
- 首轮实现还只凭现代 UUID 文件名判断 `raw_bytes_exact`。独立复审指出 legacy source
  可以指向或被重命名为 modern-looking basename，从而把 replacement text 误标
  `serial_capture_raw`。
- 首轮 native 校验只寻找唯一末尾 completion，并逐条校验基础字段，没有冻结整个 mirror
  的结构身份。因此重复 event UUID、非 `prepare` 首记录、额外 execute 记录和前后
  task/source/selected-port 冲突仍可能被接受，后续 B4.4 会收到不可信的数据库 profile。
- 第一轮收紧又把 `selected_port` 和 completion payload port 一律要求为非空字符串，
  忽略了 `esp_serial_capture` 的合法失败返回可能没有 payload port，未选择串口时两条
  mirror 的 `selected_port` 也会同为 `null`，从而误拒绝可恢复的 result error。

### 修复

- 新增版本化纯只读 `resolve_historical_serial_capture_artifacts()`。输入显式绑定当前
  session basename、run 和规范 event UUID；source filename 只作 provenance，不进入
  event/artifact identity。legacy UUID 复用 importer 的同一个公开 UUIDv5 纯函数，
  native UUID 原样规范化。
- JSONL 采用安全 fd 严格读取，限制总大小、单行大小和记录数；拒绝尾部坏行、非 UTF-8、
  非对象、混合格式、非连续 sequence、非末尾/重复 completion、project/run/tool/event
  冲突和 payload mirror 冲突。native 进一步固定为唯一 UUID 的两条
  `prepare → complete`，要求 task/source/selected-port 前后一致，并拒绝 completion
  payload port 与 run 端口冲突。两条 mirror 必须显式包含同值 `selected_port`，值可为
  非空字符串或 `null`；payload port 存在时必须匹配，只有 error/critical 可以省略，
  成功 completion 仍必须提供非空端口。
- 旧 Windows/POSIX `raw_path` 只验证本地绝对路径与 `logs/raw/<basename>` 精确后缀，
  不对旧路径执行文件系统调用。实际文件只由当前项目 `logs/raw` 重派生，并在同一安全 fd
  上复核普通文件、reparse、长度、mtime 与 SHA-256；resolver 前后复核目录链身份。
- legacy source 一律登记 `serial_capture_legacy_text`。`serial_capture_raw` 现在必须
  同时满足 `source_format=native_complete_v1` 和 UUID 排他 basename；修复了独立复审
  发现的 P1。legacy 候选返回 `ineligible/legacy_event_phase_unknown`，不放宽 B4.1。
- 候选冻结 source/record 摘要、完整规范 event profile、run profile、artifact bundle
  SHA-256、字节保真结论和独立 reconciliation version。B4.3 不连接 SQLite、不获取
  lease、不调用 B4.1，也不写 marker、JSONL 或 latest。

### 验证

- adapter 缺失的旧 main 基线为预期 `40 failed, 1 skipped`。独立对抗测试补强合同后先
  得到 `6 failed, 40 passed, 1 skipped`；第二轮合法失败端口合同先得到
  `2 failed, 48 passed, 1 skipped`。全部修复后专项
  `50 passed, 1 skipped in 1.31s`。skip 仅是当前 Windows 账户不能创建普通文件 symlink。
- main 全量 `120 passed in 49.93s`。正式项目 4 份样本只读解析得到 1 个 native
  `resolved`、3 个 legacy `ineligible`；四项都正确标为旧 writer 的
  `serial_capture_legacy_text`。
- 正式探针把 `sqlite3.connect` 替换为失败函数，4 项仍全部完成。解析前后核对 189 个项目
  文件的路径、长度、mtime 和 SHA-256，差异为 0；没有访问 COM3、板卡、Marketplace 或
  安装缓存。
- main 合入 test 后，test 分支自身源码全量
  `508 passed, 4 skipped in 254.09s`。B4.3
  [main run 30356471000](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30356471000)
  首次仅 Windows/Python 3.10 的既有
  `test_forced_termination_releases_os_handle_and_next_start_cleans_stale_lock`
  在等待第二个子进程 ready 文件的固定 8 秒窗口超时；该 job 其余 119 项和另外三个
  main job 均成功，日志没有业务异常。
- 上述既有用例在本地独立 pytest 进程重复 `10/10` 通过，耗时范围
  1.85–3.87 秒，因此未修改 B4.3 或 Monitor 代码，只重跑失败 job。main attempt 2
  成功；[test run 30356899571](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30356899571)
  四个 job 首次全部成功。

### 经验与剩余边界

- “文件存在”不等于“原始字节可信”。内容 kind 必须绑定产生它的 source/writer 格式，
  不能只看一个可重命名的 basename。
- event UUID 相等仍不足以授权补投影。B4.4 必须在 lease 内把 candidate 的完整 event/run
  profile 与 SQLite 逐字段或规范摘要比较，再调用 B4.1。
- B4.3 返回的是读取时快照；同一账户仍可能在安全 fd 读取完成后、函数返回前同长度改写
  source/raw。B4.4 必须在项目 lease 内重新解析或重新核对两类摘要，不能直接使用更早的
  candidate 授权写入。
- 旧秒级文件名曾允许不同 run 覆盖同一路径。B4.4 项目扫描必须先建立规范 raw identity
  的全局引用集合；同一路径被不同 event UUID 引用时要报告 ambiguous，不能给两个 run
  各自登记。固定 capture 因共享 `logs/raw`，仅使用 Monitor 的 per-run lease 不够，
  需要项目级 claim/lease 或等价的原子唯一所有权。
- 当前 safe-fd 与前后目录检查面向普通本地故障和路径替换，不声称抵御同一账户恶意完成
  可恢复原样的 ABA。截至该修复阶段，正式项目数据库仍为 v2；B4.4、v3-C、Marketplace
  重启确认完成前不执行正式升级。
- CI 的单次固定 ready 超时不自动等于产品回归。本轮先检查失败日志，再做同一用例独立
  重复，最后只重跑失败 job；没有用整轮盲目重跑掩盖可复现缺陷，也没有把既有测试调度
  波动冒充 B4.3 修复。

## 2026-07-28：租约外 profile 检查和 run-scoped raw ID 不能形成原子唯一归属

### 症状

- B4.3 已返回完整 event/run profile 和 raw 摘要，但如果 B4.4 先用独立连接读取 SQLite
  做比较，再调用 B4.1，两个动作之间的数据库内容仍可能改变；早期 candidate 或一次
  backend 预检查不能授权后续写入。
- `stable_raw_log_id()` 把 `run_id` 放入 UUID 身份。同一个 `raw/<basename>` 因此可以在
  两个 run 下生成两个不同 raw ID；只有项目 lease 或 JSON marker 时，进程退出或租约
  释放后没有数据库内的持久唯一归属。

### 原因

- B4.1 原合同只在 `BEGIN IMMEDIATE` 中确认 run 已结束、event 是最后一个
  `complete`，没有接收 B4.2/B4.3 的完整 profile、精确 sequence 或文件 claim。
- 文件锁解决“谁正在执行”，不能代替崩溃后仍存在的数据库所有权；独立 marker 又在
  SQLite commit 之后发布，不能作为事务内唯一约束。

### 修复

- schema v3 additive 增加 `historical_raw_claims`，以 `(project_id, path)` 为主键，并
  绑定 run、event、kind/SHA-256、adapter/version、event profile 摘要和 artifact bundle
  摘要；外键删除与 run/event 生命周期一致。
- 扩展 `reconcile_existing_event_artifacts()` 的可选严格入口：在同一
  `BEGIN IMMEDIATE` 内比较 event/run profile、event sequence 和 run
  `next_sequence_no`，随后依次提交持久 claim、raw 和 error。
- claim 必须与 raw artifact 一一对应，profile/bundle 摘要必须由实际输入规范计算。
  精确重试复用同一 claim；同 path 的其他 run/event/profile/bundle 显式
  `historical_artifact_raw_claim_conflict`。
- profile、sequence 或 claim 冲突发生在写 artifact 前；晚阶段 error、SQLite 或 commit
  失败时，claim、raw 和 error 与原事务一起回滚。

### 验证和边界

- test 分支初始合同为预期 `5 failed, 11 passed in 1.94s`；实现后专项
  `16 passed in 1.93s`，迁移/raw/error/历史补投影合并
  `84 passed in 7.84s`，main 全量 `120 passed in 47.30s`。
- 本切片只修改源码和临时测试数据库。正式项目数据库仍为 schema v2，没有调用迁移；
  项目级 lease、两次解析、全局歧义预检查和独立 marker 属于下一 B4.4 协调器切片。

## 2026-07-29：历史候选缺少项目级协调、可恢复状态和全局 raw 所有权

### 症状

- B4.2/B4.3 只能解析单个候选，没有项目级 lease、全局扫描或启动/状态 marker。两个
  协调器可能并发工作，跨 run 的共享 `logs/raw` 也可能在较晚的 SQLite claim 时才暴露。
- 只比较 event UUID 不足以定义所有者；不同 run 若复用同 UUID，仍是两个所有者。
- Monitor 历史 manifest 的终态时间可能比 SQLite 规范时间多不足一秒的小数部分，完全
  精确比较会拒绝同一来源证据；放宽整个 profile 又会掩盖 message、port 等真实冲突。
- 项目 Busy 异常使用既有 `recoverable` 语义，而协调器只读取 `retryable`；状态入口也曾
  忽略 probe 返回的损坏锁元数据。
- Windows 并发 reader 可能让 marker 的 `os.replace` 短暂返回 WinError 5/32；不加边界会
  把瞬态共享冲突当成永久失败，无限重试又会让调用失去上界。

### 原因

- 文件 resolver、SQLite 事务和项目运行状态原先分属不同层，没有一个明确的锁顺序和失败
  恢复协议。
- 文件锁只能表示“当前谁在执行”，不能代替数据库持久 claim；marker 又不在 SQLite
  事务内，必须把两个持久化结果分别报告。
- B4.3 与仓储曾使用不同 run profile 字段集合；仓储若用 SQLite 预读字段补齐，会把数据库
  自身内容冒充来源证据。

### 修复

- 新增持久项目 lease，锁顺序固定为项目 lease → 单个 Monitor run lease → SQLite
  `BEGIN IMMEDIATE`。v2 通过 SQLite URI `mode=ro` 在创建 lock/marker 或迁移前拒绝。
- 第一次解析建立整个项目的 raw 引用集合；不同 `(run_id, event_uuid)` 指向同一规范 path
  时在任何补投影前失败。capture 全部二次解析并比对指纹；Monitor 在自己的 run lease 内
  二次解析、比对并完成 B4.1。
- source-proven run profile 统一为六字段，并在事务内用当前 event 构造
  `terminal_event_uuid`。Monitor 只对 event `ts` 提供 `0..1` 秒容差；capture 默认 0，
  其他 event/run 字段始终精确。
- 新增 `sqlite-historical-artifacts-v1.json`：先发布 running，最终发布
  completed/failed。SQLite 已提交但 marker 失败时不伪装回滚，而是返回
  `database_persisted=true/marker_persisted=false`，重试以持久 claim 幂等补齐 marker。
- `recoverable` 与 `retryable` 均映射为可重试；status 单独传播 `metadata_error`。
  Windows replace 只对 WinError 5/32 每 5 ms 重试、总计最多 1 秒，并在发布后安全重读。

### 验证和边界

- 协调器缺失时 9 项合同全部红灯；实现后为 `9 passed`。复审补入 Busy、metadata 和
  同 UUID 跨 run owner 三项，先得到预期 `3 failed, 9 passed`，修复后 `12 passed`。
- B4.1-B4.4 组合 `145 passed, 1 skipped in 6.98s`；main 全量
  `120 passed in 49.70s`；test 显式加载 main 全量
  `531 passed, 4 skipped in 243.41s`。两轮独立审查未发现 P0/P1。
- 临时目录另行验证同/跨进程非阻塞 lease、未知 marker 版本拒绝、150 次发布与 1415 次
  并发读取零撕裂。它们是本轮补充复核，尚未全部固化为独立自动化合同，记为 P2 测试缺口。
- 项目级协调器按 candidate 原子、允许失败后续跑，不承诺所有 candidate 共用一个 SQLite
  事务。正式项目数据库仍为 schema v2；本轮未迁移或写正式库，未访问 COM3、板卡，
  未更新 Marketplace/安装缓存，也未提交用户的 plugin manifest 差异。v3-C 尚未开始。

## 2026-07-29：只读日志查询会创建、迁移并导入数据库

### 症状

- 在没有 `esp_mcp.sqlite` 的项目执行 latest/get/query，查询本身会创建数据库和日志目录。
- 读取正式 schema v2 会调用当前 `init_database()`，把它升级到 v3；因此“查询”实际改变
  了被观察对象。
- 若项目只有 JSONL 审计镜像，查询会触发 importer，导致镜像内容在没有显式迁移动作时
  成为在线查询结果。
- 损坏 SQLite 或非法持久化 `payload_json` 会把 `sqlite3.DatabaseError` /
  `JSONDecodeError` 直接抛给 MCP 调用者。
- 早期修复把所有 `sqlite3.DatabaseError` 都视为损坏；这样正常的 busy/locked 竞争也会
  被误报为不可恢复的数据库损坏。
- 只读连接最初把“路径存在但被目录占位”也抛成 `FileNotFoundError`，三个工具因此把
  异常存储状态误报成空数据或 run 不存在。

### 根因

- `_prepare_scope()` 同时承担建日志目录、初始化/迁移 SQLite 和导入旧 JSONL 三个写入
  职责，三个查询入口误用了这个写入准备函数。
- 即使只删掉 `_prepare_scope()`，原仓储 getter 仍调用普通 `connect()`；它会建父目录、
  设置 WAL，并且 get/latest 的 run 与 events 分两次连接读取，不能证明来自同一快照。
- 查询路径缺少独立的 schema 能力探测和数据损坏错误边界。

### 修复

- 新增 `connect_readonly()`：只接受已经存在的数据库，以绝对 URI `mode=ro` 打开，设置
  `query_only=ON` 和 busy timeout，不创建目录、不设置 journal mode、不运行迁移。
- 新增 latest/run/query 三个只读仓储入口。每次先在同一连接内 `BEGIN`，探测版本和必需
  表/列，再读取关联数据；v2 只认可 runs/events，v3 必须满足当前结构。
- 工具先完成 tail/limit/sequence/时间参数校验，再打开数据库。缺库保持原兼容返回；
  不再扫描或导入 JSONL。损坏结构和坏持久化数据统一映射为不可恢复的
  `log_database_invalid`，未知/缺失 schema 映射为
  `log_database_schema_unsupported`。
- SQLite 主错误码和保守消息分类把 busy/locked/cannot-open/permission/read-only/I/O
  映射为可恢复的 `log_database_unavailable`；格式或持久化内容损坏才标为
  `log_database_invalid`。
- `Path.resolve(strict=True)` 只负责真实缺失判断；路径存在后必须通过普通文件检查，
  目录或其他非普通目标返回 `log_database_invalid`，不进入缺库兼容分支。
- 没有使用 `immutable=1`。SQLite 官方 WAL 文档说明，WAL 只读连接在协调文件不存在而
  目录可写时可创建 `-wal/-shm`；immutable 只适合确定不会变化的数据库，不能冒充仍可能
  并发写入的日志库。

### 验证和边界

- test 分支先固化四项合同，在旧实现上为 `4 failed in 0.39s`。
- 修复后加入 query-only 强制、参数校验先于打开、三个入口的损坏库、坏 JSON、不完整
  schema、locked/unavailable 分类和目录占位拒绝，专项 `10 passed in 0.43s`；既有日志/项目上下文回归 `6 passed in 0.81s`；
  main 全量 `120 passed in 49.27s`。
- v2 测试逐项核对 user_version、主数据库大小/mtime/SHA-256 和应用文件不变。仅允许
  SQLite 自身根据 WAL 协议产生协调 sidecar；这不等价于 schema/data 迁移。
- 本切片不读取或修改正式项目 SQLite，不访问 COM3、板卡、Marketplace 或安装缓存。
  raw/error 详情与错误解析 DB-first 顺序属于后续 v3-C2/C3。

## 2026-07-29：正式 raw/error 已入库但日志详情不可见

### 症状

- schema v3 已把串口 raw 和结构化 error 原子登记到 SQLite，但 `esp_logs_get` 仍只返回
  run/events；调用者无法从公开详情判断某个 run 有哪些正式 artifact。
- 既有 `get_run_raw_logs()` / `get_run_errors()` 各自调用普通写型 `connect()` 并
  `fetchall()`。直接拼接它们会破坏 C1 的同一只读快照，并允许单个 run 返回无界行数。
- raw/error row decoder 原先只复制列值。如果 SQLite 被手工损坏，`../escape` 路径或
  非规范 recoverable 等值可能被当作可信详情，进而污染 C3 的文件来源选择。

### 根因

- B3/B4 的无界 getter 面向受 lease/事务控制的内部对账，不是 MCP 详情输出接口；C2
  缺少独立的 connection-level、有界、只读 decoder。
- raw/error schema 的 CHECK 约束能保护正常仓储写入，但查询边界仍需 fail-closed，
  不能假定磁盘内容永远没有损坏或离线篡改。
- error 的 message/raw_text 等列没有长度约束，先 `fetchall()` 再在 Python 截断仍会把
  任意大 TEXT 拉入进程。

### 修复

- 在 raw/error repository 新增专用 bounded reader；保留 B3/B4 原 getter 不变。SQL
  先限定 `project_id + run_id`，按 `created_at + id` 倒序取最新 `limit + 1`，Python
  去掉探针行并反转为时间正序。
- `read_run_snapshot()` 在 C1 已建立的同一只读 `BEGIN` 中依次读取 run、events、
  raw 和 error。schema v2 完全跳过 artifact SQL，明确返回 `reason="schema_v2"`。
- raw UUID/kind/path/time/SHA-256 和 error UUID/kind/time/行列/recoverable 重新通过
  仓储规范。身份字段过长或不规范时返回 `log_database_invalid`，不静默生成另一身份。
- error 的 file/exception type/message/raw text 在 SQL 中分别用
  `substr(..., 4096/256/2048/8192)` 限制；每条 error 返回 `field_truncation`，汇总元数据
  返回行数和字段截断状态。

### 验证和边界

- test 分支先加入四项红灯，在 C1 实现上得到 `4 failed in 0.89s`：正式详情缺失、
  最新窗口/文本截断缺失、v2 能力元数据缺失、非法 raw path 未被读取校验。
- 修复后补入并发写隔离和非法 recoverable，C2 专项 `6 passed`，C1+C2
  `16 passed in 0.99s`。并发写在 run/events 建立快照后提交，但本次响应的 raw/error
  都不可见，证明没有混合两个时间点。
- main 全量 `120 passed in 48.06s`。
- C2 只限制新增 artifact 行数和大文本字段；既有 events 的 message/payload 仍沿用
  `tail` 合同，不能宣称整个响应有总字节上界。C3 文件读取仍必须独立执行 `max_bytes`。
- 本轮只使用临时 SQLite，不打开任何 raw artifact，不访问正式项目数据库、COM3、
  板卡、Marketplace 或安装缓存，也不提交用户 plugin manifest 差异。

## 2026-07-29：错误解析忽略正式 artifact 并重复捕获项目

### 症状

- schema v3 已经登记正式 errors/raw_logs，但 `esp_error_parse_log` 仍只解析 event
  message、event payload 的绝对 raw_path 和旧 Monitor 目录。旧 event 可能压过更新、
  约束更强的正式 error/raw。
- 入口先调用 `esp_logs_get()`；该函数内部读取一次活动项目。返回后解析器又调用
  `LogScope.active()`，如果这两次之间切换项目，SQLite 记录和文件根可能来自两个项目。
- `max_bytes` 只在 `esp_logs_get` 已把最多 10000 条完整 event message/payload 拉入
  Python 后才生效，不能约束查询前内存；登记 raw 的 SHA-256 也没有参与读取验证。

### 根因

- 早期错误解析实现形成于 schema v3 正式 artifact 之前，event/Monitor 是当时唯一来源；
  C1/C2 改造查询层后没有同步更新解析来源权威顺序。
- 公开 `esp_logs_get` 面向详情展示，不是 C3 的有界输入投影。复用它既拿到不需要的完整
  event，也没有办法把第一次捕获的 LogScope 传给后续文件读取。
- 旧测试直接 monkeypatch `error_tools.esp_logs_get` 注入事件；这种桩把实现细节当合同，
  反而阻止移除第二次作用域捕获。

### 修复

- 新增 `read_error_parse_snapshot()`：在一次 `mode=ro + query_only + BEGIN` 中读取 run、
  C2 有界 raw/error 和 C3 专用兼容 event 投影。工具先捕获一个 LogScope，并把同一
  project/database/log_root 使用到调用结束。
- 来源顺序固定为正式 errors → 正式 raw_logs → 兼容 event/Monitor。存在正式 error 时
  直接返回最新结构化记录，不打开 raw；存在支持的正式 raw 时不再混合旧 event 文本。
  schema v2 跳过物理 raw/error 表，只走 runs/events 兼容。
- 兼容 event 只取最新 64 条。SQL 只返回至多 8192 字符 message 和 16384 字符 payload；
  payload 一旦截断就不执行 JSON 解码，message 还要经过总 `max_bytes` 字节上限。
- 正式 capture/Monitor path 必须匹配 kind/run 形状。日志根、祖先目录和文件拒绝
  symlink/reparse，安全 fd 读前后核对身份；单文件最多完整读取 64 MiB，计算全文件
  SHA-256 并与 SQLite 登记值比较，解析器只接收 `max_bytes` 范围内字节。
- 旧 Monitor 兼容先核对 manifest、chunk 精确集合、长度和摘要。旧 event raw_path
  没有登记摘要，只能在项目 logs 内安全读取并报告计算摘要，不能冒充正式 SHA 验证。
- 三项依赖 monkeypatch 的旧测试改为写入真实临时 SQLite event；Monitor 测试改断言正式
  error 优先。测试继续验证结构化兼容、项目外路径拒绝和扫描上界，但不要求生产代码回退。

### 验证

- 首轮五项合同在旧实现上为预期 `5 failed in 0.87s`：正式 error/raw 未被选择、重复
  捕获项目、尾部篡改未检出、v2 来源元数据缺失。
- 查询前 payload 与最新 64 条窗口两项补强在旧实现上为预期 `2 failed`。
- 修复后 C3 专项 `7 passed in 1.09s`；C1/C2/错误解析相关回归
  `49 passed in 5.63s`。
- main 全量 `120 passed in 48.77s`；test 工作树显式加载 main
  `557 passed, 4 skipped in 250.00s`。
- 以上只使用临时 SQLite/raw/Monitor 文件。没有读取或升级正式数据库、访问 COM3、
  更新 Marketplace/安装缓存，也没有纳入用户 plugin manifest 差异。

### 剩余风险

- errors/raw_logs 的详情窗口仍分别为最新 200/1000 条；结果通过
  `source_truncation` 明示窗口或字段截断。`max_bytes` 限制解析内容，64 MiB 另行限制
  单文件完整摘要 I/O，两者不是同一个概念。
- 旧 event raw_path 没有权威登记摘要，所以只保留兼容级证据；正式结论应依赖 schema v3
  errors/raw_logs。
- C2/C3 已由
  [main run 30437244226](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30437244226)
  与 [test run 30437262633](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30437262633)
  完成 8 个 Windows/Linux、Python 3.10/3.12 job，全部成功；尚未进入 Marketplace 或
  安装缓存。正式项目 SQLite 仍为 v2；远端软件矩阵也不能替代正式升级或实板验收。

## 2026-07-29：Monitor SQLite artifact 测试在慢速 Windows runner 固定轮询超时

### 症状

- C2/C3 代码和测试此前已由 main/test 共 8 个矩阵 job 验证成功。随后只同步文档的
  [test run 30438663603](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30438663603)
  中，Ubuntu/Python 3.10、Ubuntu/Python 3.12、Windows/Python 3.12 成功，只有
  Windows/Python 3.10 失败。
- 失败节点为 `test_monitor_stop_registers_each_finalized_chunk_once`。测试向假串口队列
  连续放入 `abc` 与 `def` 后，在调用 `esp_serial_monitor_stop()` 以前等待
  `persisted_bytes == 6`；固定 3 秒窗口到期后抛出
  `Monitor ... did not reach the expected state`。
- 因为 stop 尚未调用，失败本身不能证明 chunk 封存、SQLite raw_log 登记、SHA-256、
  重复 stop 幂等或 C2/C3 错误解析存在回归。

### 根因

- 测试用 `_wait_for_monitor()` 每 10 ms 读取一次完整 status，并把 3 秒墙钟时间当成
  后台 worker 已消费两条队列记录的完成条件。该方法依赖 runner 调度速度，不是 Monitor
  已提供的记录到达条件。
- `esp_serial_monitor_read(wait_ms=...)` 本身使用 Monitor condition：记录写入 store、
  更新 `persisted_bytes` 并加入带 sequence 的内存记录后会通知等待者。旧测试没有使用
  这条同步路径。
- 失败提交相对此前全绿提交只改变文档；失败测试、Monitor backend 和串口工具源码未变。
  因此现有证据指向测试同步竞态，而不是文档或 C2/C3 产品改动。

### 修复

- 不修改公共 `_wait_for_monitor()`，也不只把所有轮询统一延长。目标测试改为逐段执行：
  先放入 `abc`，使用 `after_seq=0` 和最长 `wait_ms=30000` 的条件读取确认第一条记录；
  再放入 `def`，使用第一条返回的 `next_after_seq` 确认第二条记录。
- `wait_ms=30000` 是失败上界，不是固定休眠；记录到达后立即返回。逐段确认还能区分首段
  未消费与第二段触发 chunk 轮换失败。
- 两段读取后继续检查 `persisted_bytes == 6` 和非终态持久化状态。原测试后半段保持：
  stop 后两个规范 chunk、各自 SHA-256、`serial_monitor_chunk` 类型、第二次 stop 不新增
  raw_log、唯一 complete event、run 状态和 sidecar 提交标记。

### 验证和边界

- 原失败节点单次 `1 passed`；20 个独立 pytest 进程为 `20/20`。
- 条件读取、游标、停止唤醒和分块相邻回归为 `5 passed in 2.63s`。
- `test_sqlite_monitor_artifacts.py` 为 `46 passed, 2 skipped in 34.08s`。
- test 分支最终工作树全量为 `557 passed, 4 skipped in 255.17s`。
- [test run 30446579852](https://github.com/tjing8609-cyber/esp-mcp-toolchain/actions/runs/30446579852)
  的 Windows/Linux、Python 3.10/3.12 四个 job 均成功。
- 本次测试使用假串口、临时项目和临时 SQLite，不访问 COM3、正式数据库或实板；没有
  更新 Marketplace/安装缓存，也没有纳入用户 plugin manifest 差异。

## 2026-07-29：正式 SQLite 升级封口脚本误把持久锁文件当作活动租约

### 症状

- Marketplace 新版插件重启后，正式 schema-v2 数据库先通过只读检查和临时副本演练，
  再成功升级到 v3、完成首次历史补投影和第二次幂等回放。
- 最后的聚合校验仍报告失败；数据库版本、行数、完整性、外键、marker 和文件摘要均未
  报错，失败条件只来自 `.sqlite-historical-artifacts.lock` 仍然存在。

### 根因

- `HistoricalProjectReconciliationLease.release()` 的合同是释放 OS 文件锁并关闭句柄，
  不删除持久锁文件。锁文件保留项目、进程和 lock identity 元数据，供后续竞争者和
  status/probe 判断。
- 验收脚本把 `Path.exists()` 当成租约状态，混淆了“控制文件存在”和“文件锁仍被持有”。
  因此它把符合设计的持久控制文件误报为协调器未清理。

### 修复

- 封口条件改为同时检查：`read_historical_project_reconciliation_status()` 返回
  `active=false`、`metadata_error=null`、`effective_state=completed`，且锁文件能以
  `FileShare.None` 重新取得独占只读句柄。
- 正式写入前先要求 DB/WAL/SHM 连续 5 秒内容与时间稳定、WAL 为 0 字节、三个文件均可
  独占只读打开；随后复制并核对 v2 备份，再调用事务化 `init_database()`。

### 验证和边界

- v2 备份为
  `artifacts/database-backups/esp_mcp-v2-preupgrade-20260729T123747Z-5D5F75E12C54.sqlite`，
  462,848 字节，SHA-256 为
  `5D5F75E12C54EF6137CFD2BA991A949FF2574304E96BC67A97B961649AA8711D`。
- 升级前后 runs/events 保持 111/224；最终 schema v3 为 5 raw、1 error、5 claim，
  `integrity_check=ok` 且外键零违规。5 个 raw 文件重新读取后的 SHA-256 全部与 SQLite
  登记值一致。
- 首次协调扫描 26 项：5 reconciled、17 ineligible、4 `no_artifacts`、0 failed；
  第二次回放为 5 `already_reconciled`、0 reconciled、`database_persisted=false`。第二次
  marker 的 false 表示该次无需数据库写入，不表示首次提交失败。
- 运行中的安装插件通过 `esp_logs_get` 返回 schema-v3 authoritative raw/error；
  `esp_error_parse_log` 对历史断连 run 只使用正式 `sqlite_errors`，未回退到兼容来源。
- 本步骤没有访问 COM3、运行板端程序、删除文件、擦除或烧录；正式数据库写入只包含
  已演练的 schema 升级、历史证据补投影及 marker。

## 2026-07-30：未先识别当前固件导致把 ESP-IDF 的 Raw REPL 超时当作 MicroPython 可达性问题

### 症状

- COM3 正常枚举、可打开且无 Monitor 占用，但 `esp_file_list` 的 Raw REPL 后端超时；
  mpremote 同样报告 `could not enter raw repl`。
- 3 秒被动串口捕获为 0 字节；`esp_program_stop` 发送两次 Ctrl-C 后也没有
  `KeyboardInterrupt` 或 `>>>`。所有调用均完成串口清理。
- 人工重启后现象不变，因此不能继续把原因归为某一个 Raw REPL 后端。

### 根因

- 授权的 hard reset 捕获到 ESP-IDF 5.2.1 启动信息、项目名
  `esp_idf_key_led_buzzer` 和 `ready`，而不是 MicroPython banner。
- SQLite 历史 run `flash_20260727_200755_94df480d` 进一步证明：2026-07-27 恢复
  MicroPython 后，又在 20:07 向 COM3 烧入了 ESP-IDF 示例。Raw REPL 与 mpremote 失败
  符合当前固件类型，不是相对路径修复失效。
- 仅凭串口静默和 REPL 超时也不能判定蜂鸣器电流导致掉电；本轮失败探测没有驱动蜂鸣器，
  COM3 始终保持枚举。hard reset 后应用完整启动，只证明该次启动期间供电可用。

### 修复

- 实板文件验收增加固件身份门槛：先从 hard reset/启动 banner 识别实际运行时，再选择
  MicroPython Raw REPL 或 ESP-IDF 工具，不能从历史恢复记录推断当前固件。
- 经用户明确授权，先备份当前完整 4 MiB flash，再整片擦除并恢复已核验的
  `ESP32_GENERIC-20260406-v1.28.0.bin`。擦除前备份和恢复输入均独立复算 SHA-256。
- MicroPython banner 与 `>>>` 出现后才重新进入文件验收；使用全新载荷，避免把擦除前
  板端文件当作当前结果。

### 验证和边界

- 当前 ESP-IDF 备份 run `backup_flash_20260730_120347_28c4eab3` 读满 4,194,304 字节；
  工具与本地独立 SHA-256 均为
  `5ACF1DB30021D3B1C1A83264E586007A7F36AB2C5B604522612E2E6C164E2365`。
- `erase_flash_20260730_120550_7455b13f` 成功；恢复 run
  `restore_flash_20260730_120609_a5476af6` 从 `0x1000` 写入 1,760,192 字节，输入及登记
  SHA-256 为 `CD7820D02C35D34DD403B44263129C6A511B350AEA8446C229890753FE240784`，
  esptool 完成写入哈希校验。
- reset run `reset_20260730_120644_74c13588` 捕获 MicroPython v1.28.0 banner 与 `>>>`；
  reset 工具仍按合同保留 `reset_confirmed=false` 和 `output_causality_confirmed=false`，
  本节只把原始输出作为强关联启动证据，不放宽公共字段含义。
- 21 字节载荷 `MCP_FILE_TRANSFER_OK\n` 上传和板端读回成功。下载 run
  `file_download_20260730_120738_d3d58548` 返回 workspace 内规范绝对路径；实际目标与
  源逐字节一致，SHA-256 均为
  `2DDF47ADFD6E81358CE6B00AA1EF332AF66AE718BD3AE2CAAC452218958CD163`，版本化插件缓存
  中同名文件数为 0。
- 备份、擦除、恢复、reset 和下载终态均从 authoritative schema-v3 SQLite 读回。该时点
  板端临时文件未删除；后续程序停止、错误解析、GPIO34 查询、回归、性能和软复位已分别
  完成验收。

## 2026-07-30：exec/run-file 已解析 Traceback，但正式 SQLite errors 为空

### 症状

- 实板 run `exec_code_20260730_125742_1024f69a` 执行受控
  `ValueError("ESP_MCP_ACCEPTANCE_ERROR")`。
- 即时响应收到完整 Raw REPL `OK + stdout EOT + stderr EOT + >`，并正确生成
  `micropython_traceback / ValueError / <stdin>:2`。
- `esp_error_parse_log` 也能恢复同一异常，但 `scan_sources` 使用
  `structured_error_report` 兼容来源；同一 run 的 `esp_logs_get.errors` 和正式
  `errors` 表查询均为 0 行。

### 根因

- B2 的 completion artifact 合同采用逐工具显式 opt-in。当时固定 capture 声明
  `serial_capture_raw/result_error/structured_error`，程序停止只声明
  `result_error`；默认 `logged_task` 不投影任何 artifact。
- `esp_exec_code` 和 `esp_run_file` 都调用 `_attach_error_report()`，因此 complete
  event 已含结构化报告，但两项装饰器没有声明 `structured_error`。查询层没有丢数据；
  error parser 是在正式 errors 为空时按设计回退到有界兼容 event。
- 仓库与已安装插件的 `exec_tools.py`、`log_tools.py`、`log_repository.py` 和
  `schema.sql` 摘要一致，排除了 Marketplace/安装缓存漂移。

### 修复

- `esp_exec_code` 与 `esp_run_file` 的 `logged_task` 均增加
  `completion_artifacts=("structured_error",)`。
- 不同时增加 `result_error`。同一 Traceback 若写入宽泛 wrapper 和具体 structured
  两条 error，现有 DB-first 最新项选择可能返回较宽泛的
  `raw_repl_runtime_error`，反而丢失精确文件、行号与异常类型。
- Raw REPL 的 `esp_run_file` 会嵌套调用 `esp_exec_code`；活动 `logged_task` 上下文会
  跳过内层 run，由外层只登记一条 structured error。mpremote 分支直接由外层登记。
- 历史 run 不自动回填。若以后需要把旧 event 的结构化报告补入 errors，应设计独立、
  可预演且幂等的正式数据库协调流程，不能由查询静默写入。

### 测试和边界

- 新合同在旧实现上得到预期 `2 failed, 1 passed`：exec 与 mpremote run-file 缺少正式
  error，成功 exec 不误写。
- 修复后 exec、mpremote run-file、成功不误写均通过；独立审查补充 Raw REPL run-file
  嵌套单条投影合同并通过。相关执行/停止/错误/SQLite 定向门禁为 `68 passed`，新增
  Raw REPL 单项与整文件门禁分别为 `1 passed`、`27 passed`。
- 最终完整门禁为 main `120 passed in 60.53s`、test 显式加载 main 源码
  `559 passed, 4 skipped in 296.00s`；4 项 skip 仍是 Windows 普通文件 symlink
  权限，不涉及本次修复。
- 独立审查为 P0=0、P1=0；兼容 event fallback 保留，旧日志仍可读。
- 本切片只正式投影可解析的 MicroPython Traceback。`mpremote_timeout`、
  `raw_repl_enter_failed` 等没有 `error_report` 的顶层操作错误仍不进入 errors；如要扩展
  必须单独设计 `result_error` 的优先级和去重合同。
- 源码软件门禁不能替代新版 Marketplace 重载后的实板验证；最终复验结果见下一节。

### 新版插件实板复验

- 个人 Marketplace 源通过 `plugin-creator` validator 更新为
  `0.1.0+codex.20260730053724`；用户重启后，当前任务实际加载的技能和安装缓存均为
  该版本。
- COM3 受控 run `exec_code_20260730_150437_0d9c65aa` 执行
  `ValueError("ESP_MCP_SQLITE_PROJECTION_FINAL")`。即时结果收到完整 Raw REPL ACK、
  stdout EOT、stderr EOT 和提示符，并生成
  `micropython_traceback / ValueError / <stdin>:1`。
- 该 run 的 `esp_logs_get.errors` 从 authoritative schema-v3 SQLite 返回恰好一条正式
  error，ID 为
  `5d63306a-a820-5282-9728-f95bee726015`；`esp_error_parse_log.scan_sources`
  只有 `{kind: sqlite_errors, count: 1}`，没有回退到兼容 event。
- 工具因主动抛出的异常返回 `ok=false` 是预期业务结果。调用后 COM3
  `available=true`、`busy=false`；本次没有刷写、擦除、删除、GPIO 或板端文件修改，
  也未调用 reset 工具或显式发送复位命令；`physical_reset_excluded=false`，串口控制线
  效应未独立排除。
- 该实板调用直接覆盖 exec producer；mpremote run-file 与 Raw REPL 嵌套 run-file
  的单条正式投影由前述软件合同覆盖。蜂鸣器瞬时电流专项按用户决定延期，不能由本结果
  推断。

## 2026-07-30：无蜂鸣器验收若复用 key/LED/buzzer 示例会在启动阶段访问 GPIO25

### 症状

- 用户决定暂不处理蜂鸣器瞬时电流专项，但原计划中的 ESP-IDF 实板闭环仍指向
  `examples/esp_idf_key_led_buzzer`。
- 只承诺“不按 KEY1”仍不足以建立无蜂鸣器边界。

### 根因

- 该示例在 `app_main()` 启动时无条件初始化 LEDC、绑定 GPIO25，再把占空比设为 0；
  因此即使没有蜂鸣声，应用也已经访问 GPIO25/PWM。
- GPIO34 若在启动或运行时读到低电平，示例会进入五次蜂鸣器脉冲。软件无法保证人工
  不按键、抖动或输入状态不会触发该分支。

### 修复

- 新增 `examples/esp_idf_uart_smoke`，只包含 `ESP_LOGI`、FreeRTOS delay、启动 READY
  和每秒递增 HEARTBEAT。
- 合同要求应用 component 只编译唯一 `main.c`，拒绝额外 components/source；扫描全部
  应用源码并拒绝 GPIO、LEDC、PWM、MCPWM、RMT、DAC 和直接寄存器接口。
- `sdkconfig.defaults` 精确固定 ESP32、DIO、40 MHz、4 MiB、single-app、UART0 115200
  和 INFO 日志；生成的 sdkconfig/dependencies/managed components 由 Git 忽略。

### 验证和边界

- 初始四项合同在旧 main 上为预期 `4 failed in 2.95s`，首轮实现为
  `4 passed in 0.62s`；独立复审收紧后五项为 `5 passed in 0.28s`。
- host build run `build_20260730_152205_0508e89d` 成功；BIN 176,896 字节，SHA-256
  `AA9E9AFA7036D2F78B183A4834835EBEDDD859AB3914D3509F9C00FD0AD409A9`，flash args
  为 DIO / 40 MHz / 4 MiB，地址为 `0x1000`、`0x10000`、`0x8000`。
- 源码静态边界不能证明 ROM bootloader、复位瞬间或板级上拉下拉的 GPIO25 电气波形；
  这需要示波器。当前结果也没有烧录或监控 UART-only 固件。
- 本步骤未访问 COM3，未驱动 GPIO25/PWM，未擦除、烧录、删除或恢复；蜂鸣器专项继续
  标记为延期。

## 2026-07-30：普通 build 隐式执行 set-target，绕过 full clean 确认门

### 症状

- `run_idf_build()` 在生成的 sdkconfig 不存在或目标不匹配时，直接拼接
  `idf.py set-target <target> build`。
- `esp_project_build` 没有对应确认参数，看起来只是普通主机构建，却可能删除既有构建
  产物并替换配置。

### 根因

- ESP-IDF 5.2.1 官方动作表声明 `set-target` 依赖 `fullclean`；`fullclean` 会递归删除
  build 目录内容。
- 同一官方 CMake 入口会在 set-target 时把 sdkconfig 重命名为 `sdkconfig.old`；若
  `sdkconfig.old` 已存在，默认 rename 存在覆盖风险。
- 旧后端只读取 sdkconfig，没有检查 `build/CMakeCache.txt` 的 IDF_TARGET，也没有区分
  “计划执行”“子进程已启动”“可能部分完成”和“目标事后验证通过”。
- 首轮修复又只给三种 destructive plan 加路径检查，错误假设普通 `build` 和
  `define_target_build` 不会产生破坏性路径影响；实际上它们同样会向 build 目录写入，
  junction/symlink 指向项目外时仍可能越界。

### 修复

- 将预检固定为五种计划：
  - `build`：sdkconfig 与 cache 均匹配；
  - `define_target_build`：首次使用 `-D IDF_TARGET=... build`，不 fullclean；
  - `fullclean_build`：sdkconfig 匹配但 cache 冲突；
  - `fullclean_define_target_build`：sdkconfig 缺失但 cache 冲突；
  - `set_target_build`：既有 sdkconfig 目标冲突或缺字段。
- 后三种 destructive plan 在 `confirm_target_change=false` 时于启动 idf.py 前返回
  `target_change_confirmation_required`。确认后，纯 cache 冲突只 fullclean，不调用
  set-target；只有 sdkconfig 确需替换时才调用 set-target。
- 子进程环境显式绑定请求的 IDF_TARGET，不修改全局环境。预检记录 sdkconfig.old 是否
  已存在及覆盖风险。
- 结果分别记录 plan、confirmation、planned、command started/completed、
  `side_effects_partial_possible` 和 postflight sdkconfig/cache target；确认后的命令即使
  返回 0，只要两处目标未核对一致也 fail closed。
- 路径检查前移到读取 target cache 之前并覆盖全部五种 plan，且在启动 `idf.py` 前再次
  检查 resolved build 路径。build 目录的 symlink/junction/reparse/越界，以及链接或
  非普通文件形式的 `CMakeCache.txt` 均拒绝并保持零 spawn。
- timeout 返回 `command_completed=false`；结果不再提供会把计划或子进程启动误解为
  具体 `set-target`/`fullclean` 已执行的 invoked 字段。
- `build_flash_monitor` 明确分成首次安全 build、目标变更单独授权并重新 build、核对
  产物、烧录单独授权、flash 与独立 monitor，两个确认不能互相代替。
- 关键安全字段加入 `logged_task.result_payload_keys`，写入当前 build run 的 SQLite
  completion；工具数保持 48，MCP schema 只新增默认 false 的参数。

### 测试和边界

- 第一轮合同在旧实现上为预期 `6 failed, 6 passed`；缓存冲突、sdkconfig.old、失败和
  postflight 合同追加后为预期 `8 failed, 3 passed`。
- 修复后 UART、后端、工具日志和 MCP 架构定向门禁为 `39 passed in 2.30s`，覆盖五种
  plan、未确认零 spawn、普通 build/首次 define-target 的 reparse 零 spawn、启动前
  二次路径检查、非普通 CMake cache、timeout、精确命令、CRLF/missing cache target、
  spawn/nonzero/postflight 失败、SQLite 安全字段、MCP 默认值和 48 tools。
- main 全量为 `120 passed in 60.59s`；test 显式加载当前 main 为
  `582 passed, 4 skipped in 297.08s`。最终独立复审为 P0=0、P1=0。
- 新后端对已配置 UART-only 工程的实际增量构建返回 `target_plan=build`、
  `confirmation_required=false`、`fullclean_planned=false`、
  `set_target_planned=false`、`target_verified=true`，且 BIN 哈希保持不变。
- 首次旧实现构建虽触发 set-target 的 fullclean dependency，但当时没有 build 目录，
  ESP-IDF 明确输出 `Nothing to clean`；这只证明该次没有既有产物可删，不代表旧通用路径
  安全。
- destructive 三种确认路径只通过临时目录和模拟子进程验证，本轮没有实际执行
  fullclean、set-target、烧录、擦除、恢复、文件删除或 COM3 操作。

## 2026-07-30：Monitor 启停竞态把合法空错误对象当作字典

### 症状

- GitHub main run `30525807125` 的 Ubuntu/Python 3.10 job 在
  `test_monitor_stop_while_starting_is_bounded` 失败；启动线程没有返回结果，pytest
  同时报出未处理线程异常。
- 直接异常为 `AttributeError: 'NoneType' object has no attribute 'get'`，位置是
  `esp_serial_monitor_start()` 处理启动后已进入 `STOPPED` 的分支。

### 根因

- `MonitorSession.status()` 的正式形状允许 `last_error` 为 object 或 null。干净的并发
  stop 可以进入 `STARTING → STOPPING → STOPPED`，此时没有业务错误，所以值为 null。
- 旧代码使用 `status.get("last_error", {}).get(...)`。字典默认值只在 key 不存在时
  生效；key 存在且值为 null 时，第一段仍返回 `None`，第二个 `.get()` 必然失败。
- `03c84bc..28c8054` 没有修改 Monitor 工具、后端或该测试；四矩阵中另三个 job 成功。
  因此它是既有调度竞态被 Linux/Python 3.10 暴露，不是 UART/ESP-IDF target 修复引入。

### 修复

- 读取 `last_error` 后先用 `isinstance(..., dict)` 校验；非 dict 统一作为空错误对象处理。
- error kind 和 message 分别使用稳定 fallback
  `serial_monitor_start_failed` / `Serial monitor failed during startup.`，公开工具返回
  结构化失败并保留原始 monitor 状态，不让异常逃出启动线程。
- 全仓同类访问点复审没有发现第二个未进行类型或真值防护的链式调用。

### 测试和边界

- 新增确定性 fake-session 合同，固定 `status()` 与 `request_stop()` 均返回
  `STOPPED + last_error=None`。旧实现为预期 `1 failed`；修复后该合同与既有并发合同
  合并为 `2 passed`。
- 原并发合同以独立 pytest 进程连续复跑 `30/30`；main 全量
  `120 passed in 59.15s`。独立只读复审认可最小修复和确定性合同。
- 修复合入 test 后标准全量为 `583 passed, 4 skipped in 314.90s`；main run
  `30526826689` 与 test run `30526826402` 的 8 个 Windows/Linux、Python 3.10/3.12
  job 全部成功。个人 Marketplace 源版
  `0.1.0+codex.20260730084223` 通过 validator、`120 passed in 57.90s` 和
  `48 tools / 12 resources / 12 prompts` 直接枚举；安装态等待用户重启确认。
- 本步骤只使用 fake serial、临时日志/SQLite 和 GitHub 日志；未访问 COM3、复位、烧录、
  擦除、删除、GPIO25、PWM 或蜂鸣器。

## 2026-07-30：main 未回同步 Monitor 高频持久化完成合同

### 症状

- 文档提交后的 main run `30528050703` 只有 Ubuntu/Python 3.10 失败：
  `test_monitor_high_frequency_output_is_bounded_and_accounted` 在
  `bytes_received=262144` 时读取到 `persisted_bytes=258048`，差一条 4096 字节记录；
  该 job 为 `1 failed, 119 passed`。
- 同一 main run 的另外三个 job 成功；test run `30528050497` 四个 job 全部成功。

### 根因

- `_consume()` 在串口读取后先增加 `bytes_received`，再逐条调用
  `SerialLogStore.append()`。status 可以在两步之间读取，因此 received 短暂领先
  persisted 是公开计数的合法实时状态，不等于最终漏写。
- 旧 main 测试只等待 received 达标便跳出，随后立即要求 persisted 相等，把前一个计数
  错当成后一个操作的完成屏障。
- test 分支提交 `366f288` 已在 2026-07-27 修正同一问题，但分支专属测试改动没有回同步
  main；因此 test 的四平台矩阵稳定，而 main 仍携带旧完成条件。

### 修复

- 把 test 既有合同精确回同步 main：只有 received 与 persisted 同时达到 262144 才结束
  等待；同时要求 `unpersisted_bytes=0`。
- 显式 stop 后再次核对最终 persisted 和 unpersisted，使用 worker join、store close 和
  force flush 形成真正终态边界。
- 不修改生产计数顺序，也不把磁盘 append 放进 status condition 锁；否则会让状态查询被
  磁盘 I/O 阻塞。没有用固定延时或简单重跑掩盖问题。

### 验证和边界

- 修复后的单项以独立 pytest 进程执行 `30/30`；main 全量
  `120 passed in 57.79s`。
- 远端失败正好少一条最大记录，结合 test 分支四平台成功和 stop 后终态合同，证据支持
  “测试同步错误”，不支持“产品已确认丢数据”。
- 本步骤只使用 fake serial、临时日志和 GitHub 日志；未访问 COM3、复位、烧录、擦除、
  删除、GPIO25、PWM 或蜂鸣器。
