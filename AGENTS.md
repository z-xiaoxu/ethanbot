# nanobot-ai 项目文档

## 1. 项目基本信息

| 项 | 值 |
|---|---|
| 包名 | `nanobot-ai` |
| 版本 | `0.1.6` |
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
│   ├── agent/                  # Agent 核心：主循环、Runner、上下文、记忆、技能、子代理
│   │   └── tools/              # LLM 可调用工具（fs/shell/web/mcp/cron/spawn/message）
│   ├── bus/                    # 异步消息总线（双向 Queue 解耦 channel 与 agent）
│   ├── channels/               # IM 渠道适配层（12 个内置 + 插件机制）
│   ├── cli/                    # Typer CLI 子命令、引导安装、流式渲染
│   ├── command/                # 斜杠命令路由与内置命令（/stop /new /status 等）
│   ├── config/                 # Pydantic 配置 schema、加载器、路径解析、迁移
│   ├── cron/                   # 定时任务调度与持久化
│   ├── heartbeat/              # 心跳服务：周期性唤醒 Agent 执行后台任务
│   ├── providers/              # LLM Provider 抽象与多厂商实现
│   ├── security/               # 安全工具（SSRF/内网 URL 拦截）
│   ├── session/                # 会话管理（JSONL 持久化）
│   ├── skills/                 # 内置技能（memory/cron/github/weather/summarize 等 9 个）
│   ├── templates/              # 工作区默认模板（AGENTS/USER/SOUL/TOOLS/HEARTBEAT/MEMORY.md）
│   └── utils/                  # 通用工具（helpers/evaluator）
├── bridge/                     # WhatsApp Bridge（Node.js/TypeScript，Baileys）
├── tests/                      # pytest 测试（与主包对称）
├── docs/                       # 补充文档
├── pyproject.toml              # 包定义与配置
├── Dockerfile / docker-compose.yml
└── CONTRIBUTING.md
```

## 3. 架构分层

```
┌──────────────────────────────────────────┐
│           CLI / Gateway                  │
│  (agent / gateway / onboard / status)    │
└──────┬──────────────────┬────────────────┘
   ┌───▼───────┐   ┌──────▼──────┐
   │ChannelMgr │   │Cron/Heartbeat│
   │12 channels│   │             │
   └───┬───────┘   └──────┬──────┘
   ┌───▼──────────────────▼──────┐
   │       MessageBus            │
   │  inbound Queue ⇄ outbound  │
   └──────────┬──────────────────┘
         ┌────▼─────┐
         │AgentLoop │ → ContextBuilder + AgentRunner
         └────┬─────┘     → ToolRegistry + MemoryConsolidator
         ┌────▼─────┐
         │LLMProvider│ (Anthropic/OpenAI/Azure/Codex/...)
         └──────────┘
```

调用链：Channel → MessageBus → AgentLoop → ContextBuilder + AgentRunner → LLMProvider + ToolRegistry → OutboundMessage → MessageBus → Channel

旁路：CronService / HeartbeatService → `AgentLoop.process_direct` → MessageBus outbound

## 4. 核心业务模块

### 4.1 Agent 主循环 (`agent/loop.py`)
- `run()`：消费 inbound 队列，session_key 串行 + 全局 `Semaphore` 限流
- `_dispatch()`：priority 命令无锁处理，普通消息走 session 锁
- `_process_message()`：slash 命令 → 上下文构建 → Runner 迭代 → session 落盘 → 记忆整理
- `process_direct()`：CLI / Cron / Heartbeat 直连入口
- `_analyze_skill_usage()`：后台检测技能使用信号，LLM 决策生成 `skill-proposals/*.json`
- `_connect_mcp()`：懒初始化 MCP 连接（`AsyncExitStack`），失败下次重试

### 4.2 AgentRunner (`agent/runner.py`)
- 业务无关的 LLM + 工具多轮循环：`run(spec)` → `provider.chat` → tool_calls → `ToolRegistry.execute` → 回注
- `AgentRunSpec`：支持 `concurrent_tools`（并行执行）、`fail_on_tool_error`
- `AgentHook`：生命周期钩子（before/after iteration、流式、重试）

### 4.3 上下文构建 (`agent/context.py`)
- `build_system_prompt()`：身份 + bootstrap + 记忆 + always skills + skills 摘要
- `build_messages()`：合并 runtime 元数据与用户消息（含多模态）

### 4.4 记忆系统 (`agent/memory.py`)
- `MemoryStore`：`MEMORY.md`（长期）+ `HISTORY.md`（追加）+ `memory/topics/`（主题）
- 三层记忆：Core / Topic / Event；`list_topics` / `read_topic` / `write_topic`
- `MemoryConsolidator`：按 token 预算切块归档，维护 `last_consolidated` 偏移

### 4.5 技能 (`agent/skills.py`) / 子代理 (`agent/subagent.py`)
- `SkillsLoader`：合并工作区与内置 skills，frontmatter 解析，`always: true` 常驻，`install_missing_deps`
- `SubagentManager.spawn()`：独立 ToolRegistry（含 fs/exec/web，无 message/spawn/cron/mcp）

### 4.6 消息总线 (`bus/`) / 会话 (`session/`) / 命令 (`command/`)
- `MessageBus`：双 `asyncio.Queue`；`InboundMessage` 含 `session_key_override`
- `SessionManager`：内存缓存 + JSONL 持久化 + legacy 迁移；`get_history()` 对齐 tool 边界
- `CommandRouter`：4 级路由（priority / exact / prefix / intercept）；内置 `/stop /restart /new /status /help`

### 4.7 定时任务 (`cron/`) / 心跳 (`heartbeat/`)
- `CronService`：JSON 持久化、mtime 热加载、单计时器链式唤醒；`every`/`cron`/`at` 三种调度
- `HeartbeatService`：周期读 `HEARTBEAT.md`，LLM 结构化决策 skip/run；`evaluate_response` 控制通知

## 5. 代码模式与规范

### 5.1 AgentLoop 初始化

```python
loop = AgentLoop(
    bus=bus, provider=provider, workspace=workspace, model=model,
    cron_service=cron_service, session_manager=session_manager,
    mcp_servers=config.tools.mcp_servers,
    restrict_to_workspace=config.tools.restrict_to_workspace,
    context_window_tokens=65536, max_iterations=40, ...
)
await loop.run()
```

### 5.2 Channel 实现模板

```python
class XxxChannel(BaseChannel):
    name = "xxx"
    display_name = "Xxx"
    def __init__(self, config, bus):
        if isinstance(config, dict): config = XxxConfig.model_validate(config)
        super().__init__(config, bus)
    async def start(self): ...   # 连接平台 → self._handle_message(sender, chat, content)
    async def stop(self): ...
    async def send(self, msg): ...  # 发送 OutboundMessage，失败抛异常供重试
```

### 5.3 错误处理
- LLM：`chat_with_retry` 指数退避，非短暂且含图时去图重试
- 工具：`ToolRegistry.execute` 捕获异常返回字符串错误 + `_HINT`
- Channel：`_send_with_retry` 最多 `send_max_retries` 次退避重试

### 5.4 消息处理标准流程
1. 消费 InboundMessage → 2. priority 命令无锁执行 → 3. session 锁 → 4. slash 命令分发 → 5. ContextBuilder 构建 → 6. AgentRunner 迭代 → 7. 落盘 Session → 8. 后台 MemoryConsolidator + skill 分析 → 9. OutboundMessage → Channel

## 6. 基础设施层

### 6.1 LLM Provider

| 实现类 | backend | 覆盖的逻辑 Provider |
|--------|---------|---------------------|
| `AnthropicProvider` | `anthropic` | anthropic |
| `OpenAICompatProvider` | `openai_compat` | openai, openrouter, aihubmix, siliconflow, volcengine, byteplus, github_copilot, deepseek, gemini, zhipu, dashscope, moonshot, minimax, mistral, stepfun, vllm, ollama, ovms, groq, custom 等 |
| `OpenAICodexProvider` | `openai_codex` | openai_codex |
| `AzureOpenAIProvider` | `azure_openai` | azure_openai |
| `GroqTranscriptionProvider` | — | 语音转写（Whisper），非 LLM 路径 |

Provider 选择：`Config._match_provider` → 前缀匹配 → 关键词匹配 → 本地回退 → 全局回退。

### 6.2 Agent 工具

| 工具名 | 功能 | 条件 |
|--------|------|------|
| `read_file` | 分页读文件/图片多模态 | 始终 |
| `write_file` / `edit_file` / `list_dir` | 写文件/行匹配替换/列目录 | 始终 |
| `exec` | 异步 shell（超时、截断、危险拦截） | `exec.enable=true`（默认） |
| `web_search` / `web_fetch` | 搜索/URL 抓取（SSRF 防护） | 始终 |
| `message` | 向渠道发消息 | 始终 |
| `spawn` | 后台子代理 | 始终 |
| `cron` | 定时任务 CRUD | 注入 CronService |
| `mcp_<server>_<tool>` | MCP 协议工具代理 | 配置 mcp_servers |

### 6.3 配置系统
- 文件：`~/.nanobot/config.json`（`--config` 多实例）；`Config(BaseSettings)` + `env_prefix="NANOBOT_"` + `env_nested_delimiter="__"`
- 路径：`config/paths.py` 基于配置文件目录推导 `sessions/`、`media/`、`cron/`、`logs/` 等
- 迁移：`_migrate_config` 处理历史字段

## 7. 数据模型

| 数据 | 存储方式 | 路径 |
|------|----------|------|
| 配置 | JSON | `~/.nanobot/config.json` |
| 会话 | JSONL（首行 metadata） | `workspace/sessions/<key>.jsonl` |
| 长期记忆 | Markdown | `workspace/memory/MEMORY.md` |
| 历史记忆 | Markdown（追加） | `workspace/memory/HISTORY.md` |
| 主题记忆 | Markdown | `workspace/memory/topics/*.md` |
| 技能提案 | JSON | `workspace/memory/skill-proposals/*.json` |
| 心跳任务 | Markdown | `workspace/HEARTBEAT.md` |
| 定时任务 | JSON | `~/.nanobot/cron/jobs.json` |
| 技能 | SKILL.md + scripts | `workspace/skills/` 与 `nanobot/skills/` |

## 8. 构建与启动

| 方式 | 命令 |
|------|------|
| 本地安装 | `uv pip install .` |
| 开发安装 | `uv pip install -e ".[dev]"` |
| Docker | `docker compose build && docker compose up nanobot-gateway` |

| CLI 命令 | 说明 |
|----------|------|
| `nanobot onboard` | 初始化配置与工作区 |
| `nanobot gateway` | 启动网关（Channel + Cron + Heartbeat） |
| `nanobot agent` | 直连 Agent（`-m` 单次 / 交互式） |
| `nanobot status` | 配置与 Provider 状态 |
| `nanobot channels status/login` | 渠道状态/登录 |
| `nanobot plugins list` | 内置 + 插件渠道列表 |
| `nanobot provider login` | OAuth Provider 登录 |

```
nanobot gateway
  ├── load_config → Config
  ├── sync_workspace_templates（含 HEARTBEAT.md 迁移）
  ├── create Provider / MessageBus / SessionManager
  ├── create CronService / HeartbeatService
  ├── create AgentLoop (bus, provider, tools, mcp, ...)
  ├── create ChannelManager
  └── asyncio.gather(loop.run, channels.start_all, cron.start, heartbeat.start)
```

## 9. 开发注意事项

### 9.1 测试
- `pytest` + `pytest-asyncio`（`asyncio_mode = "auto"`）；`pytest tests/` 或 `pytest tests/agent/`
- 测试目录：`tests/{agent,channels,cli,config,cron,providers,security,tools}/`

### 9.2 代码风格
- Linter：`ruff`（`line-length=100`, `target-version="py311"`）；规则 `E, F, I, N, W`（忽略 `E501`）
- 命名：camelCase 配置别名（JSON），snake_case 代码

### 9.3 安全
- `security/network.py`：SSRF（`validate_url_target`、`validate_resolved_url`、`contains_internal_url`）
- `ExecTool`：危险命令拦截 + 内网 URL 检测
- Channel `allow_from`：空列表全拒、`"*"` 全开、白名单

### 9.4 关键模式
- **异步队列生产者-消费者**：Channel → MessageBus → AgentLoop
- **会话级互斥 + 全局限流**：`asyncio.Lock` per session + `Semaphore`
- **注册表模式**：ToolRegistry、Channel Registry、Provider Registry
- **Hook / 策略对象**：AgentHook 暴露 Runner 生命周期
- **懒初始化**：MCP `AsyncExitStack`，失败下次重试
- **单计时器链式唤醒**：CronService 只维护一个 timer task
- **文件型状态机**：session JSONL、cron JSON、memory markdown、skill proposals JSON

### 9.5 渠道插件
- 内置 12 渠道：Discord / DingTalk / Email / Feishu / Matrix / Mochat / QQ / Slack / Telegram / WeCom / WeChat / WhatsApp
- 插件：`entry_points(group="nanobot.channels")` 注册；实现 `BaseChannel` 的 `start/stop/send`

### 9.6 工具函数
- `utils/helpers.py`：消息拼装、token 粗估、模板同步（`sync_workspace_templates`）、图片检测、HEARTBEAT 迁移
- `utils/evaluator.py`：`evaluate_response` — 后台任务结束后 LLM 判断是否通知用户
