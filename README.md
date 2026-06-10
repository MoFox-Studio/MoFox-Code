# mofox-code

Neo-MoFox Coding Agent 的终端前端（TUI），Claude Code 风格的终端编程助手。通过 WebSocket 连接到 Coding Agent 后端，在终端内提供交互式 AI 编程对话体验。

## 功能特性

- **终端内交互式 AI 编程对话** — 直接在终端与 AI 编程助手进行自然语言交流，完成代码生成、修改、分析等任务
- **流式 Markdown 实时渲染** — 增量渲染 Agent 回复，支持代码块语法高亮、行号显示，所见即所得
- **Bash 命令审批系统** — Agent 执行 Shell 命令前需用户审批，支持自动审查模式，防止危险操作
- **Checkpoint 回滚机制** — 每次代码变更自动生成检查点，支持 `/undo` 和 `/rollback` 快速恢复
- **三套配色主题** — 内置 dark / light / monokai 三套完整配色方案，适应不同终端环境
- **配置文件 + CLI 参数分层配置** — 通过 `~/.mofox-code/config.toml` 持久化偏好，CLI 参数一键覆盖

## 环境要求

- Python >= 3.11
- 一个运行中的 [Coding Agent 后端服务](https://github.com/your-org/coding-agent)

## 安装

```bash
git clone https://github.com/your-org/mofox-code.git
cd mofox-code
pip install -e .
```

或使用 uv：

```bash
uv pip install -e .
```

## 配置

配置文件路径：`~/.mofox-code/config.toml`（首次运行自动生成）

```toml
[server]
url = "ws://localhost:8765/coding-agent/ws"

[preferences]
theme = "dark"
auto_review = false
```

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `server.url` | 后端 WebSocket 地址 | `ws://localhost:8765/coding-agent/ws` |
| `preferences.theme` | 配色主题 | `dark` |
| `preferences.auto_review` | 是否启用自动审查 | `false` |

## CLI 参数

```bash
mofox-code [选项]
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--server` | 后端 WebSocket 地址 | `ws://localhost:8765/coding-agent/ws` |
| `--auto-review` | 启用自动审查模式 | `false` |
| `--theme` | 配色主题（dark / light / monokai） | `dark` |
| `--project-dir` | 项目根目录 | 当前目录 |
| `--verbose` | 输出详细调试信息 | `false` |

> 配置优先级：CLI 参数 > 配置文件 > 默认值

## 内置命令

在对话界面中，以 `/` 开头的输入会被识别为内置命令：

| 命令 | 功能 |
|------|------|
| `/exit` / `/quit` | 退出程序 |
| `/undo` | 回滚最近一步操作 |
| `/rollback` | 列出所有检查点，选择回滚到指定步骤 |
| `/history` | 查看操作历史 |
| `/status` | 查看当前会话状态（phase、项目名、检查点数量等） |
| `/auto-review on` | 开启自动审查模式 |
| `/auto-review off` | 关闭自动审查模式 |
| `/clear` | 清屏 |
| `/help` | 显示帮助信息 |

## 命令审批

当 Agent 需要执行 Bash 命令时，会弹出审批面板，展示命令内容、工作目录和自动审查结果。此时按以下键做出决策：

| 按键 | 操作 | 说明 |
|------|------|------|
| `a` | 允许一次 | 仅本次放行 |
| `s` | 会话内允许 | 当前会话中对相同命令前缀自动放行 |
| `f` | 永久允许 | 写入配置文件，永久对该命令前缀放行 |
| `d` | 拒绝 | 拒绝执行本次命令 |
| `r` | 开启自动审查 | 切换到自动审查模式，后续命令自动评估 |

> 命令前缀指命令的第一个 token（如 `git`、`uv`、`python`），用于定义审批规则的生效范围。

## 许可

MIT License
