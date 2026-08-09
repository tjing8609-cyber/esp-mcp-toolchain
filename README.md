# ESP MCP Toolchain（Codex 插件）

## 分支说明

本仓库当前保留以下远端分支：

| 分支 | 定位 | 说明 |
| --- | --- | --- |
| `main` | 默认分支、稳定主线 | 保存 Codex 插件清单、MCP 工具链实现、skills、文档和示例；发布版本以该分支中已经完成并可核查的内容为准。 |
| `test` | 验证分支 | 保存测试文件、测试辅助脚本和验证规则；用于同步 `main` 后执行软件门禁，不作为插件发布分支。 |
| `feature/serial-monitor` | 历史功能分支 | 曾用于开发后台串口 Monitor；该分支的提交已经进入 `main`，当前仅保留为历史追溯，不应作为新开发基线。 |
| `agent/readme-and-development-log` | 当前文档修订分支 | 用于修复被数字钢琴说明覆盖的 README，并把开发日志整理到独立文档；合入后由 `main` 提供最新文档。 |

后续临时开发分支统一按 `feature/*` 或 `agent/*` 命名，通过 Pull Request 合入；它们不是长期稳定接口。实际分支列表以 [GitHub Branches](https://github.com/tjing8609-cyber/esp-mcp-toolchain/branches) 页面为准。

## 仓库结构

```text
esp-mcp-toolchain/
├─ .codex-plugin/
│  └─ plugin.json                  # Codex 插件清单
├─ .codex/
│  └─ config.toml                  # 仓库级 MCP 配置
├─ .github/
│  └─ workflows/tests.yml          # GitHub Actions 软件检查
├─ skills/
│  └─ esp-mcp/                     # ESP MCP skill 及使用示例
├─ toolchain/
│  ├─ cli.py                       # 命令行入口
│  ├─ mcp_server.py                # stdio MCP 服务入口
│  ├─ esp_mcp_toolchain/           # 工具链核心包
│  │  ├─ backends/                 # 串口、ESP-IDF、esptool、Raw REPL 等后端
│  │  ├─ database/                 # SQLite 数据结构与仓储
│  │  ├─ hardwork/                 # 硬件资料处理
│  │  ├─ memory/                   # 项目记忆
│  │  ├─ prompts/                  # MCP prompts
│  │  ├─ resources/                # MCP resources
│  │  ├─ store/                    # 项目数据存储
│  │  └─ tools/                    # MCP tools
│  └─ tests/                       # 软件测试
├─ scripts/                        # 环境安装、服务启动、日志导出等脚本
├─ docs/
│  ├─ 00-overview.md ... 16-release-notes-v0.1.0.md
│  ├─ 17-development-log.md        # 从旧 README 整理出的开发日志
│  └─ adr/                         # 架构决策记录
├─ examples/                       # ESP-IDF 与 MicroPython 示例工程
├─ hardwork/                       # 硬件资料模板、索引与处理结果
├─ data/                           # 运行产物、日志和记忆目录占位
├─ .mcp.json                       # 插件 MCP Server 启动配置
├─ .app.json                       # 可选 app 声明
├─ hooks.json                      # 可选 hooks 声明
├─ environment.yml                # 独立 Conda 环境
├─ pyproject.toml                  # Python 项目与测试配置
├─ requirements.txt               # Python 依赖
├─ CHANGELOG.md                    # 用户可见版本变化
├─ LICENSE                         # MIT 许可证
├─ config.py                       # 当前 main 遗留的数字钢琴配置，非插件核心
└─ main.py                         # 当前 main 遗留的数字钢琴程序，非插件核心
```

插件主体是 `.codex-plugin/`、`skills/`、`toolchain/`、`scripts/` 及其配套配置和文档。根目录的 `config.py` 与 `main.py` 属于现有数字钢琴业务文件，不是 ESP MCP Toolchain 的功能入口。
