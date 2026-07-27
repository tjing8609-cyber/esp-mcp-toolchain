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
