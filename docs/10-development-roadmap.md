# 开发路线图

路线按依赖关系推进。每项功能必须在独立功能分支同时完成实现、测试和文档，通过全量测试与对应硬件门禁后才能合入 `main`。

1. Python CLI：串口枚举、选择、固定时长捕获和日志读取。已完成基础闭环。
2. MCP Server：使用官方 MCP Python SDK、`FastMCP` 和 stdio transport 暴露 tools、resources 和 prompts。已完成。
3. ESP 开发闭环：构建、备份、烧录、恢复、复位、文件操作和错误解析。已完成当前板卡范围内的基础闭环。
4. hardwork 上下文：附件归档、资料索引、硬件审查门禁和映射增量回写。已完成基础闭环。
5. 项目 memory：写入、读取、检索、更新和删除。已完成基础闭环。
6. 后台串口 Monitor：状态机、不可变项目绑定、游标读取、有界缓冲、分块落盘、跨进程串口锁和退出清理。软件测试、四平台 CI、Codex 插件缓存验证和真实 ESP 串口验收已完成，并已合入 `main`。
7. SQLite schema 与仓储层：SQLite 已成为 runs/events 正式查询源；project-scoped schema、v1/JSONL 迁移、首次并发建库、事务序号、UUID 幂等、run 状态机、同步工具和异步 Monitor 生命周期已完成，本地提交、当前项目迁移、marketplace 源同步和远端四平台 CI 已完成；插件缓存重载待完成。
8. 日志查询增强：`run_id`、phase、level、tool、source、时间和 sequence 过滤已接通，并同步 CLI、FastMCP schema 与静态工具注册资源；本地门禁、marketplace 源同步和远端四平台 CI 已完成，插件缓存重载待完成。后续可继续做导出、聚合和 run_id 前缀等非阻断增强。
9. 项目数据迁移体系：工程路径重绑定、项目合并、导出、导入和完整性校验。当前排在 SQLite 发布验证之后；它与数据库 schema 迁移是两类任务。

当前第 7 项和第 8 项的基础合同、本地 `134 passed` 门禁、本地提交、当前项目日志迁移、插件源同步和远端四平台 CI 已完成。下一步是在新任务重载插件；不自动开始第 9 项。