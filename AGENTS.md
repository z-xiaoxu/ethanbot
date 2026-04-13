# nanobot-ai 项目文档

## 1. 项目基本信息

| 项 | 值 |
|---|---|
| 包名 | `nanobot-ai` |
| 版本 | `0.1.4.post6` |
| 语言 | Python ≥3.11（bridge 部分为 Node.js/TypeScript） |
| 框架 | Typer (CLI) / Pydantic (配置) / asyncio (运行时) |
| 入口 | `nanobot.cli.commands:app` → `nanobot` 命令 |
| 许可 | MIT |
| 默认端口 | `18790`（Gateway） |
| 数据存储 | 本地文件系统（`~/.nanobot/`），无数据库 |
| 构建系统 | hatchling / uv / Docker |

**一句话定位**：受 OpenClaw 启发的超轻量个人 AI 助手框架，支持多 LLM Provider、多 IM 渠道接入、工具调用、技能扩展、定时任务与长期记忆。

## 2. 目录结构

```
ethanbot/
├── nanobot/                    # Python 主包
│   ├── __main__.py             # python -m nanobot 入口
│   ├── agent/                  # Agent 核心：主循环、上下文、记忆、技能
│   │   └── tools/              # LLM 可调用的工具（shell/web/fs/mcp 等）
│   ├── bus/                    # 异步消息总线（双向 Queue 解耦 channel 与 agent）
│   ├── channels/               # IM 渠道适配层（12 个内置 + 插件机制）
│   ├── cli/                    # Typer CLI 子命令、引导安装、流式渲染
│   ├── command/                # 斜杠命令路由与内置命令（/stop /new /status 等）
│   ├── config/                 # Pydantic 配置 schema、加载器、路径解析
│   ├── cron/                   # 定时任务调度与持久化
│   ├── heartbeat/              # 心跳服务：周期性唤醒 Agent 执行后台任务
│   ├── providers/              # LLM Provider 抽象与多厂商实现
│   ├── security/               # 安全工具（SSRF/内网 URL 拦截）
│   ├── session/                # 会话管理（JSONL 持久化）
│   ├── skills/                 # 内置技能目录（每技能含 SKILL.md）
│   ├── templates/              # 工作区默认 Markdown 模板
│   └── utils/                  # 通用工具函数
├── bridge/                     # WhatsApp Bridge（Node.js/TypeScript，Baileys）
│   └── src/                    # index.ts / server.ts / whatsapp.ts
├── tests/                      # pytest 单元/集成测试（与主包对称）
├── docs/                       # 补充文档（Channel 插件指南等）
├── pyproject.toml              # 包定义、依赖、构建、lint 配置
├── Dockerfile                  # 多阶段 Docker 构建
├── docker-compose.yml          # gateway + cli 服务编排
└── CONTRIBUTING.md             # 贡献指南
```

## 3. 架构分层

```
┌─────────────────────────────────────────────────┐
│                   CLI / Gateway                  │
│  (Typer commands: agent / gateway / onboard)     │
└────────────┬──────────────────┬──────────────────┘
             │                  │
     ┌───────▼───────┐  ┌──────▼──────┐
     │  ChannelMgr   │  │  CronService│
     │  12+ channels │  │  Heartbeat  │
     └───────┬───────┘  └──────┬──────┘
             │                  │
      ┌──────▼──────────────────▼──────┐
      │          MessageBus            │
      │   inbound Queue ⇄ outbound    │
      └──────────────┬────────────────┘
                     │
          ┌──────────▼──────────┐
          │     AgentLoop       │
          │  (session 串行调度)  │
          └──┬──────┬──────┬───┘
             │      │      │
    ┌────────▼┐ ┌───▼────┐ ┌▼──────────┐
    │Context  │ │Runner  │ │Memory     │
    │Builder  │ │(LLM+   │ │Consolidator│
    │(prompt) │ │ tools) │ │(长期记忆)  │
    └─────────┘ └───┬────┘ └───────────┘
                    │
          ┌─────────▼─────────┐
          │   LLM Provider    │
          │ Anthropic/OpenAI/ │
          │ Azure/Codex/...   │
          └───────────────────┘
```

调用链：Channel → MessageBus → AgentLoop → ContextBuilder + AgentRunner → LLMProvider + ToolRegistry → OutboundMessage → MessageBus → Channel

旁路：CronService / HeartbeatService → `AgentLoop.process_direct` → MessageBus outbound

## 4. 核心业务模块

### 4.1 Agent 主循环 (`agent/loop.py`)
- `AgentLoop.run()`：消费 inbound 队列，按 session_key 串行、全局限流并发
- `_dispatch()`：优先级命令（/stop）无锁处理，普通消息走 session 锁
- `_process_message()`：slash 命令分发 → 上下文构建 → AgentRunner 迭代 → session 落盘 → 触发记忆整理
- `process_direct()`：CLI / Cron / Heartbeat 的直连入口

### 4.2 AgentRunner (`agent/runner.py`)
- 业务无关的 LLM + 工具迭代循环
- `run(spec)` → 多轮 `provider.chat` → 解析 tool_calls → `ToolRegistry.execute` → 回注结果
- 通过 `AgentHook` 暴露生命周期钩子（流式、progress 等）

### 4.3 上下文构建 (`agent/context.py`)
- `build_system_prompt()`：身份 + bootstrap 文件 + 记忆 + always skills + skills 摘要
- `build_messages()`：合并 runtime 元数据与用户消息（支持图片多模态）

### 4.4 记忆系统 (`agent/memory.py`)
- `MemoryStore`：读写 `MEMORY.md`（结构化长期记忆）和 `HISTORY.md`（仅追加历史）
- `MemoryConsolidator`：按 token 预算把旧对话切块交 LLM 归档，维护 `last_consolidated` 偏移

### 4.5 技能加载 (`agent/skills.py`)
- `SkillsLoader`：合并工作区与内置 skills，解析 frontmatter
- 支持 `always: true` 技能常驻 prompt、按需加载详细指令

### 4.6 子代理 (`agent/subagent.py`)
- `SubagentManager.spawn()`：独立 ToolRegistry（无 message/spawn），跑 AgentRunner
- 结果通过 `InboundMessage(channel="system")` 回灌主循环

### 4.7 消息总线 (`bus/`)
- `MessageBus`：双 `asyncio.Queue`，`publish/consume` inbound/outbound

### 4.8 会话管理 (`session/`)
- `SessionManager`：内存缓存 + JSONL 持久化（`workspace/sessions/`）
- `Session`：消息列表、`last_consolidated`、history 边界对齐

### 4.9 斜杠命令 (`command/`)
- `CommandRouter`：4 级路由（priority / exact / prefix / intercept）
- 内置：`/stop` `/restart` `/new` `/status` `/help`

### 4.10 定时任务 (`cron/`)
- `CronService`：JSON 持久化、mtime 热加载、单计时器链式唤醒
- 支持 `every`（间隔）/ `cron`（表达式+时区）/ `at`（一次性）

### 4.11 心跳服务 (`heartbeat/`)
- `HeartbeatService`：周期读 `HEARTBEAT.md`，LLM 决策 skip/run
- `on_execute` → Agent 执行，`evaluate_response` 决定是否通知用户

## 5. 代码模式与规范

### 5.1 AgentLoop 初始化模式

```python
loop = AgentLoop(
    bus=bus, provider=provider, workspace=workspace,
    model=model, cron_service=cron_service,
    session_manager=session_manager,
    mcp_servers=config.tools.mcp_servers, ...
)
await loop.run()
```

### 5.2 Channel 实现模板

```python
class XxxChannel(BaseChannel):
    name = "xxx"
    display_name = "Xxx"

    def __init__(self, config, bus):
        if isinstance(config, dict):
            config = XxxConfig.model_validate(config)
        super().__init__(config, bus)

    async def start(self):
        self._running = True
        # 连接平台、注册回调
        # 收到消息 → await self._handle_message(sender_id, chat_id, content)

    async def stop(self):
        self._running = False

    async def send(self, msg):
        # 发送 OutboundMessage，失败抛异常供 manager 重试
```

### 5.3 错误处理
- LLM 调用：`chat_with_retry` 短暂错误指数退避，非短暂错误可尝试去掉图片重试
- 工具执行：`ToolRegistry.execute` 捕获异常返回字符串错误 + 固定提示 `_HINT`
- Channel 发送：`ChannelManager._send_with_retry` 最多 `send_max_retries` 次退避重试

### 5.4 Agent 消息处理标准流程
1. 从 MessageBus 消费 InboundMessage
2. 检查 priority 命令（/stop 等），无锁直接执行
3. 获取 session 锁，加载/创建 Session
4. 尝试 slash 命令分发
5. ContextBuilder 构建 system prompt + messages
6. AgentRunner 迭代（LLM → tool_calls → execute → 回注）
7. 落盘 Session（JSONL）
8. 后台触发 MemoryConsolidator
9. OutboundMessage → MessageBus → Channel 发送

## 6. 基础设施层

### 6.1 LLM Provider

| 实现类 | backend | 覆盖的逻辑 Provider |
|--------|---------|---------------------|
| `AnthropicProvider` | `anthropic` | anthropic |
| `OpenAICompatProvider` | `openai_compat` | openai, openrouter, deepseek, gemini, groq, ollama, vllm, siliconflow, volcengine, 以及更多 |
| `OpenAICodexProvider` | `openai_codex` | openai_codex, github_copilot |
| `AzureOpenAIProvider` | `azure_openai` | azure_openai |

Provider 选择由 `registry.py` 中 `ProviderSpec` 驱动（环境变量、配置匹配）。

### 6.2 Agent 工具

| 工具名 | 功能 | 条件 |
|--------|------|------|
| `read_file` | 分页读文件/图片多模态 | 始终 |
| `write_file` | 写文件、自动建目录 | 始终 |
| `edit_file` | 精确/宽松行匹配替换 | 始终 |
| `list_dir` | 递归列目录 | 始终 |
| `exec` | 异步 shell（超时、截断、危险拦截） | `exec.enable=true` |
| `web_search` | 可配多搜索引擎 | 始终 |
| `web_fetch` | URL 抓取（SSRF 防护、Jina Reader） | 始终 |
| `message` | 向渠道发消息 | 始终 |
| `spawn` | 后台子代理 | 始终 |
| `cron` | 定时任务 CRUD | 注入 CronService |
| `mcp_*` | MCP 协议工具代理 | 配置 mcp_servers |

### 6.3 WhatsApp Bridge
- Node.js/TypeScript，基于 Baileys
- WebSocket 本地通信（`127.0.0.1:3001`）
- 数据流：WhatsApp ↔ Baileys ↔ BridgeServer(WS) ↔ Python WhatsAppChannel

### 6.4 配置系统
- 配置文件：`~/.nanobot/config.json`（支持 `--config` 指定）
- Schema：Pydantic BaseSettings，`env_prefix="NANOBOT_"`，`env_nested_delimiter="__"`
- 路径：以 config.json 所在目录为实例根，下辖 `sessions/`、`media/`、`cron/`、`logs/` 等

## 7. 数据模型

项目无传统数据库，数据以文件形式存储：

| 数据 | 存储方式 | 路径 |
|------|----------|------|
| 配置 | JSON | `~/.nanobot/config.json` |
| 会话 | JSONL（首行 metadata） | `~/.nanobot/workspace/sessions/<key>.jsonl` |
| 长期记忆 | Markdown | `workspace/memory/MEMORY.md` |
| 历史记忆 | Markdown（追加） | `workspace/memory/HISTORY.md` |
| 心跳任务 | Markdown | `workspace/HEARTBEAT.md` |
| 定时任务 | JSON | `~/.nanobot/cron/jobs.json` |
| 技能 | SKILL.md + scripts | `workspace/skills/` 与包内 `nanobot/skills/` |
| WhatsApp 认证 | 文件 | `~/.nanobot/whatsapp-auth/` |

## 8. 构建与启动

### 8.1 构建命令

| 方式 | 命令 |
|------|------|
| 本地安装 | `uv pip install .` 或 `pip install .` |
| 开发安装 | `uv pip install -e ".[dev]"` |
| Docker 构建 | `docker compose build` |
| Docker 启动 Gateway | `docker compose up nanobot-gateway` |
| Docker CLI | `docker compose run --rm nanobot-cli agent -m "hello"` |

### 8.2 CLI 命令

| 命令 | 说明 |
|------|------|
| `nanobot onboard` | 初始化配置与工作区（`--wizard` 交互式） |
| `nanobot gateway` | 启动网关（含 Channel / Cron / Heartbeat） |
| `nanobot agent` | 直连 Agent 交互（`-m` 单次 / 交互式） |
| `nanobot status` | 查看配置、Provider 状态 |
| `nanobot channels status` | 渠道启用状态 |
| `nanobot channels login <ch>` | 渠道交互登录 |
| `nanobot provider login <pv>` | OAuth Provider 登录 |

### 8.3 初始化链路

```
nanobot gateway
  ├── load_config → Config (Pydantic)
  ├── sync_workspace_templates
  ├── create Provider
  ├── create MessageBus
  ├── create SessionManager
  ├── create CronService (on_job → agent.process_direct)
  ├── create HeartbeatService (on_execute → agent.process_direct)
  ├── create AgentLoop (bus, provider, tools, mcp, ...)
  ├── create ChannelManager (bus, config)
  └── asyncio.gather(loop.run, channels.start_all, cron.start, heartbeat.start)
```

## 9. 开发注意事项

### 9.1 测试
- 框架：`pytest` + `pytest-asyncio`（`asyncio_mode = "auto"`）
- 运行：`pytest tests/` 或指定子目录 `pytest tests/agent/`
- 覆盖率：`pytest --cov=nanobot`
- 测试目录结构与 `nanobot/` 对称

### 9.2 代码风格
- Linter：`ruff`（`line-length=100`, `target-version="py311"`）
- 规则：`E, F, I, N, W`（忽略 `E501`）
- 命名：camelCase 配置别名（JSON），snake_case 代码

### 9.3 日志
- 库：`loguru`
- 用法：`from loguru import logger`，各模块直接 `logger.info/error/warning`

### 9.4 安全
- `nanobot/security/network.py`：SSRF 防护（`validate_url_target`、`validate_resolved_url`）
- `ExecTool`：危险命令拦截 + 内网 URL 检测
- Channel `allow_from`：空列表全拒、`"*"` 全开、白名单

### 9.5 关键模式
- **异步队列生产者-消费者**：Channel → MessageBus → AgentLoop
- **会话级互斥 + 全局限流**：`asyncio.Lock` per session + `Semaphore`
- **注册表模式**：ToolRegistry、Channel Registry、Provider Registry
- **Hook / 策略对象**：AgentHook 暴露 Runner 生命周期
- **懒初始化**：MCP 连接 `AsyncExitStack`，失败下次消息重试
- **单计时器链式唤醒**：CronService 只维护一个 timer task

### 9.6 渠道插件扩展
- 内置 12 个渠道：Discord / DingTalk / Email / Feishu / Matrix / Mochat / QQ / Slack / Telegram / WeCom / WeChat / WhatsApp
- 插件：通过 `entry_points(group="nanobot.channels")` 注册自定义渠道
- 实现 `BaseChannel` 的 `start` / `stop` / `send` 即可接入
