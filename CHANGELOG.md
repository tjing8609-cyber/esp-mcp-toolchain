# Changelog

本文件记录用户可见的项目变化。版本发布后再把 `[Unreleased]` 内容归入对应版本。

## [Unreleased]

### Added

- 新增后台串口 Monitor 候选实现：`esp_serial_monitor_start`、`esp_serial_monitor_stop`、`esp_serial_monitor_status` 和 `esp_serial_monitor_read`。
- Monitor 使用正式状态机、不可变项目绑定、单调递增 `seq`、`after_seq` 游标、有界环形缓冲和分块原始字节日志。
- 新增跨进程串口锁、进程所有权与端口身份记录、只针对已结束进程的陈旧锁恢复，以及 MCP Server 退出清理。
- 新增 Windows / Linux、Python 3.10 / 3.12 的 GitHub Actions 全量测试矩阵。

### Changed

- 新功能改为在 `feature/<topic>` 分支同时维护实现、测试和文档；历史 `test` 分支保留但停止承载新开发。
- README、CHANGELOG、开发状态页和 ADR 分工记录不同层级的信息。

### Validation

- Monitor 假串口、存储和进程级专项测试通过，包括 stdin EOF、强制终止恢复、跨进程冲突、断连、缓冲区淘汰、UTF-8 分片、二进制日志和磁盘故障。
- 功能分支与个人 marketplace 源均通过 plugin validator；重启后的 Codex 已加载 `0.1.0+codex.20260713091610` 缓存并实际调用四个 Monitor 工具。
- `COM3` 真实板卡门禁已通过：捕获 ESP-IDF 启动日志、验证游标续读不重复、停止后完整落盘，并立即重新打开同一端口。提交 `60a3a83` 的 Windows/Linux、Python 3.10/3.12 CI 全部成功；Monitor 仍是功能分支候选，尚未合入 `main`。
