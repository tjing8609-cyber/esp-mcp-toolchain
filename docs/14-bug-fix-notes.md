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
