# 开发路线图

路线按依赖关系推进。每项功能必须在实现工作树完成源码和文档，在测试工作树完成契约，并通过全量测试与对应硬件门禁后才能作为稳定版本发布。

1. Python CLI：串口枚举、选择、固定时长捕获和日志读取。已完成基础闭环。
2. MCP Server：使用官方 MCP Python SDK、`FastMCP` 和 stdio transport 暴露 tools、resources 和 prompts。已完成。
3. ESP 开发闭环：构建、备份、烧录、恢复、复位、文件操作和错误解析。串口、reset、Raw REPL 和错误检测已完成本轮软件加固；`erase_flash` 的受管进程树清理、显式前后复位参数和失败契约已通过本地及远端门禁，真实擦除仍待验证。
4. hardwork 上下文：附件归档、资料索引、硬件审查门禁和映射增量回写。已完成基础闭环。
5. 项目 memory：写入、读取、检索、更新和删除。已完成基础闭环。
6. 后台串口 Monitor：状态机、不可变项目绑定、游标读取、有界缓冲、分块落盘、跨进程串口锁和退出清理。软件测试、四平台 CI、插件缓存验证和历史真实 ESP 串口验收已完成。
7. SQLite schema 与仓储层：SQLite 已成为 runs/events 正式查询源；project-scoped schema、v1/JSONL 迁移、事务序号、UUID 幂等和 run 生命周期已完成并发布。
8. 日志查询增强：`run_id`、phase、level、tool、source、时间和 sequence 过滤已接通；后续导出和聚合属于非阻断增强。
9. 任务书 12 项能力：6 项基础和 6 项提高均已有正式工具或闭环实现；公开提示词重组为 12 套，工具面目标为 48 tools / 12 resources / 12 prompts。独立 Conda 启动器已绑定 `esp-mcp-toolchain` 环境，其中 `mpremote 1.28.0` 已验证。提示词/提高工具/架构专项为 `25 passed`，串口与执行关联门禁为 `62 passed`；P0 与 `erase_flash` P1 均已分别通过 main/test 共 8 个远端 job，P1 同步 test 全量为 `228 passed in 28.76s`。个人 marketplace 源 `0.1.0+codex.20260726165544` 已通过 validator、`104 passed` 和 48/12/12 直接枚举；本轮没有重新读取板端状态，安装缓存重载与 MicroPython 执行类实板验收仍待完成。
10. 项目数据迁移体系：工程路径重绑定、项目合并、导出、导入和完整性校验。与数据库 schema 迁移是两类任务，继续排在本轮任务书能力发布之后。

当前优先顺序：

1. 推送 Monitor STARTING 测试同步修复并确认 Windows/Linux、Python 3.10/3.12 远端矩阵。
2. 由用户重启 Codex，在新任务核对安装插件版本和 48 tools / 12 resources / 12 prompts。
3. 在不扩大授权的前提下完成可做的实板门禁；执行擦除、烧录或其他高风险动作前必须按具体动作重新确认。
