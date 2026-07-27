# 开发路线图

路线按依赖关系推进。每项功能必须在实现工作树完成源码和文档，在测试工作树完成契约，并通过全量测试与对应硬件门禁后才能作为稳定版本发布。

1. Python CLI：串口枚举、选择、固定时长捕获和日志读取。已完成基础闭环。
2. MCP Server：使用官方 MCP Python SDK、`FastMCP` 和 stdio transport 暴露 tools、resources 和 prompts。已完成。
3. ESP 开发闭环：构建、备份、烧录、恢复、复位、文件操作和错误解析。串口、reset、Raw REPL 和错误检测已完成本轮软件加固；`erase_flash` 的受管进程树清理、显式前后复位参数和失败契约已通过本地及远端门禁。2026-07-27 已按明确授权完成 `COM3` 4 MiB 备份、真实整片擦除和 MicroPython v1.28.0 恢复。
4. hardwork 上下文：附件归档、资料索引、硬件审查门禁和映射增量回写。已完成基础闭环。
5. 项目 memory：写入、读取、检索、更新和删除。已完成基础闭环。
6. 后台串口 Monitor：状态机、不可变项目绑定、游标读取、有界缓冲、分块落盘、跨进程串口锁和退出清理。软件测试、四平台 CI、插件缓存验证和历史真实 ESP 串口验收已完成。
7. SQLite schema 与仓储层：SQLite 已成为 runs/events 正式查询源；project-scoped schema、v1/JSONL 迁移、事务序号、UUID 幂等和 run 生命周期已完成并发布。
8. 日志查询增强：`run_id`、phase、level、tool、source、时间和 sequence 过滤已接通；后续导出和聚合属于非阻断增强。
9. 任务书 12 项能力：6 项基础和 6 项提高均已有正式工具或闭环实现；公开提示词重组为 12 套，工具面为 48 tools / 12 resources / 12 prompts。独立 Conda 启动器已绑定 `esp-mcp-toolchain` 环境，其中 `mpremote 1.28.0` 已验证。已安装插件 `0.1.0+codex.20260726165544` 和 48/12/12 工具面已在用户重启后核对。实板验收已确认 runtime、串口 Monitor 和文件上传/读取/列表；相对下载误写插件缓存后暂停。主机路径边界已在本地通过 main `119 passed` 与 test 跨工作树 `243 passed`，但新提交、远端 CI、Marketplace 重载和后续板端能力仍待完成。
10. 项目数据迁移体系：工程路径重绑定、项目合并、导出、导入和完整性校验。与数据库 schema 迁移是两类任务，继续排在本轮任务书能力发布之后。

当前优先顺序：

1. 提交主机相对路径修复与 test 合同，合并双工作树并确认 Windows/Linux、Python 3.10/3.12 远端矩阵。
2. 只同步个人 Marketplace 源，运行 validator、发布测试和 48/12/12 枚举，再更新一次 cachebuster；不直接修改安装缓存。
3. 由用户重启 Codex 后，使用新的项目内输出名复验相对下载确实落在所选 workspace。
4. 继续程序停止、错误解析、GPIO34 只读、板上回归、性能、软复位、临时文件删除和日志闭环；删除、ESP-IDF 烧录或恢复 MicroPython 均按具体动作单独确认。
