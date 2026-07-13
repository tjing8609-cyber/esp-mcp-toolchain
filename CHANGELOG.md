# Changelog

本文件记录用户可见的项目变化。版本发布后再把 `[Unreleased]` 内容归入对应版本。

## [Unreleased]

### Added

- 新增后台串口 Monitor 候选实现：`esp_serial_monitor_start`、`esp_serial_monitor_stop`、`esp_serial_monitor_status` 和 `esp_serial_monitor_read`。
- Monitor 使用正式状态机、不可变项目绑定、单调递增 `seq`、`after_seq` 游标、有界环形缓冲和分块原始字节日志。
- 新增跨进程串口锁、进程所有权与端口身份记录、只针对已结束进程的陈旧锁恢复，以及 MCP Server 退出清理。
- 新增 Windows / Linux、Python 3.10 / 3.12 的 GitHub Actions 全量测试矩阵。

### Changed

- `main` 维护产品实现和文档；`test` 分支的分支专属提交维护测试文件和测试规则，门禁由测试工作树加载主线源码执行。
- README、CHANGELOG、开发状态页和 ADR 分工记录不同层级的信息。

### Fixed

- Monitor 串口改为非阻塞读取，先查询实际待收字节，单次最多读取 1024 字节；避免 Windows CH9102 稀疏输出场景中固定 `read(4096)` 产生污染记录。
- 无串口数据时使用 5 ms 有界等待，避免非阻塞轮询占满 CPU，同时保持停止清理及时响应。

### Validation

- Monitor 假串口、存储和进程级专项测试通过，包括 stdin EOF、强制终止恢复、跨进程冲突、断连、缓冲区淘汰、UTF-8 分片、二进制日志和磁盘故障。
- 两条污染读取回归在修复前失败、修复后通过；跨分支全量门禁为 `101 passed`，Monitor 专项为 `29 passed`。
- `COM3` 修复后门禁通过：启动日志 3,653 字节无解码错误；最终按键日志包含两次完整五脉冲序列，共 1,466 字节、41 条记录，无替换字符、丢弃或未持久化字节。
- 修复后的仓库源码和个人 marketplace 源均通过 plugin validator，版本为 `0.1.0+codex.20260713135819`，从 marketplace 源直接枚举为 43 tools / 12 resources / 4 prompts；当前任务仍加载旧缓存，重新安装和重启后的工具面验收待完成。
- `COM3` 真实板卡门禁已通过：捕获 ESP-IDF 启动日志、验证游标续读不重复、停止后完整落盘，并立即重新打开同一端口。功能分支头 `962a382` 和 `main` 合入提交 `e67dd7f` 的 Windows/Linux、Python 3.10/3.12 CI 均全部成功；Monitor 已合入 `main`。
