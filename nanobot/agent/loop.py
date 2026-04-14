"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from contextlib import AsyncExitStack, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.context import ContextBuilder
from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.agent.memory import MemoryConsolidator
from nanobot.agent.runner import AgentRunner, AgentRunResult, AgentRunSpec
from nanobot.agent.skills import BUILTIN_SKILLS_DIR
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.command import CommandContext, CommandRouter, register_builtin_commands
from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.session.manager import Session, SessionManager

if TYPE_CHECKING:
    from nanobot.config.schema import ChannelsConfig, ExecToolConfig, WebSearchConfig
    from nanobot.cron.service import CronService

_PATCH_ANALYSIS_TOOL_NAME = "submit_skill_patch_analysis"


def _skill_patch_analysis_tool_definition() -> dict[str, Any]:
    """OpenAI-style tool matching ``contracts/patch-analysis-tool.v1.schema.json`` (relaxed if/then)."""
    return {
        "type": "function",
        "function": {
            "name": _PATCH_ANALYSIS_TOOL_NAME,
            "description": (
                "After a skill's SKILL.md was read and a tool error later recovered, "
                "decide whether the skill instructions should be patched."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["should_patch"],
                "properties": {
                    "should_patch": {"type": "boolean"},
                    "reason": {"type": "string"},
                    "diff_description": {"type": "string"},
                    "proposed_content": {"type": "string"},
                    "original_content": {"type": "string"},
                },
            },
        },
    }


class _AgentLoopRunHook(AgentHook):
    """Per-run hook: streaming, progress, reflection checkpoints."""

    REFLECTION_INTERVAL = 15

    def __init__(
        self,
        loop_self: Any,
        *,
        on_progress: Callable[..., Awaitable[None]] | None,
        on_stream: Callable[[str], Awaitable[None]] | None,
        on_stream_end: Callable[..., Awaitable[None]] | None,
        channel: str,
        chat_id: str,
        message_id: str | None,
    ) -> None:
        self._loop = loop_self
        self._on_progress = on_progress
        self._on_stream = on_stream
        self._on_stream_end = on_stream_end
        self._channel = channel
        self._chat_id = chat_id
        self._message_id = message_id
        self._stream_buf = ""

    def wants_streaming(self) -> bool:
        return self._on_stream is not None

    async def before_iteration(self, context: AgentHookContext) -> None:
        if (
            context.iteration > 0
            and self.REFLECTION_INTERVAL > 0
            and context.iteration % self.REFLECTION_INTERVAL == 0
        ):
            context.messages.append({
                "role": "user",
                "content": (
                    "[Reflection checkpoint — not from user]\n"
                    "Pause and briefly assess:\n"
                    "1. Am I making progress or repeating failed approaches?\n"
                    "2. Is there a simpler strategy I haven't tried?\n"
                    "Respond in 1-2 sentences, then continue."
                ),
            })

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        from nanobot.utils.helpers import strip_think

        prev_clean = strip_think(self._stream_buf)
        self._stream_buf += delta
        new_clean = strip_think(self._stream_buf)
        incremental = new_clean[len(prev_clean):]
        if incremental and self._on_stream:
            await self._on_stream(incremental)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        if self._on_stream_end:
            await self._on_stream_end(resuming=resuming)
        self._stream_buf = ""

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        if self._on_progress:
            if not self._on_stream:
                thought = self._loop._strip_think(
                    context.response.content if context.response else None,
                )
                if thought:
                    await self._on_progress(thought)
            tool_hint = self._loop._strip_think(self._loop._tool_hint(context.tool_calls))
            await self._on_progress(tool_hint, tool_hint=True)
        for tc in context.tool_calls:
            args_str = json.dumps(tc.arguments, ensure_ascii=False)
            logger.info("Tool call: {}({})", tc.name, args_str[:200])
        self._loop._set_tool_context(self._channel, self._chat_id, self._message_id)

    async def on_retry(self, attempt: int, max_attempts: int, reason: str) -> None:
        if self._on_progress:
            await self._on_progress(
                f"AI model is temporarily unavailable, retrying... "
                f"(attempt {attempt}/{max_attempts})",
            )

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        return self._loop._strip_think(content)


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    _TOOL_RESULT_MAX_CHARS = 16_000

    _ERROR_CLASSIFICATIONS: list[tuple[tuple[str, ...], str]] = [
        (
            ("401", "unauthorized", "authentication", "invalid_api_key", "api key", "api_key",
             "认证失败", "密钥无效", "鉴权失败"),
            "The API key is invalid or expired. Please check your provider configuration.",
        ),
        (
            ("403", "forbidden", "permission", "权限不足", "无权访问"),
            "Access denied by the AI provider. Please verify your API permissions.",
        ),
        (
            ("429", "rate limit", "rate_limit", "too many requests", "quota exceeded",
             "请求过多", "限流", "频率限制"),
            "The AI provider rate limit has been reached. Please try again shortly.",
        ),
        (
            ("insufficient", "billing", "payment", "余额不足", "欠费", "配额"),
            "The AI provider account has insufficient balance or quota. "
            "Please check your account.",
        ),
        (
            ("context length", "context_length", "maximum context length",
             "max tokens", "token limit", "上下文长度", "超出长度限制"),
            "The conversation is too long for the model to process. "
            "Please start a new conversation with /new.",
        ),
        (
            ("model not found", "model_not_found", "does not exist",
             "invalid model", "model not available", "模型不存在", "模型不可用"),
            "The configured model is not available. Please check your model settings.",
        ),
        (
            ("timeout", "timed out", "deadline", "超时", "响应超时"),
            "The AI model response timed out. Please try again.",
        ),
        (
            ("overloaded", "503", "502", "500", "server error",
             "temporarily unavailable", "504",
             "负载", "服务繁忙", "请稍后重试", "稍后再试", "服务不可用", "服务异常"),
            "The AI service is temporarily unavailable. Please try again in a moment.",
        ),
        (
            ("connection", "network", "dns", "resolve", "网络", "连接失败"),
            "Unable to connect to the AI provider. Please check your network connection.",
        ),
        (
            ("content filter", "content_filter", "safety", "blocked", "moderation",
             "内容审核", "内容过滤", "违规"),
            "The request was blocked by the AI provider's content filter. "
            "Please rephrase your message.",
        ),
    ]

    _DEFAULT_CLASSIFIED_ERROR = (
        "Sorry, an error occurred while calling the AI model. Please try again later."
    )

    @staticmethod
    def _classify_error(raw: str | None) -> str:
        """Map a raw LLM/provider error string to a user-friendly message."""
        if not raw:
            return AgentLoop._DEFAULT_CLASSIFIED_ERROR
        lower = raw.lower()
        for markers, message in AgentLoop._ERROR_CLASSIFICATIONS:
            if any(m in lower for m in markers):
                return message
        snippet = raw[:100].rstrip()
        return f"{AgentLoop._DEFAULT_CLASSIFIED_ERROR} (Error: {snippet})"

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        context_window_tokens: int = 65_536,
        web_search_config: WebSearchConfig | None = None,
        web_proxy: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        timezone: str | None = None,
    ):
        from nanobot.config.schema import ExecToolConfig, WebSearchConfig

        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.context_window_tokens = context_window_tokens
        self.web_search_config = web_search_config or WebSearchConfig()
        self.web_proxy = web_proxy
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self._start_time = time.time()
        self._last_usage: dict[str, int] = {}

        self.context = ContextBuilder(workspace, timezone=timezone)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.runner = AgentRunner(provider)
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            web_search_config=self.web_search_config,
            web_proxy=web_proxy,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._background_tasks: list[asyncio.Task] = []
        self._session_locks: dict[str, asyncio.Lock] = {}
        # NANOBOT_MAX_CONCURRENT_REQUESTS: <=0 means unlimited; default 3.
        _max = int(os.environ.get("NANOBOT_MAX_CONCURRENT_REQUESTS", "3"))
        self._concurrency_gate: asyncio.Semaphore | None = (
            asyncio.Semaphore(_max) if _max > 0 else None
        )
        self.memory_consolidator = MemoryConsolidator(
            workspace=workspace,
            provider=provider,
            model=self.model,
            sessions=self.sessions,
            context_window_tokens=context_window_tokens,
            build_messages=self.context.build_messages,
            get_tool_definitions=self.tools.get_definitions,
            max_completion_tokens=provider.generation.max_tokens,
        )
        self._register_default_tools()
        self.commands = CommandRouter()
        register_builtin_commands(self.commands)

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None
        self.tools.register(ReadFileTool(workspace=self.workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read))
        for cls in (WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        if self.exec_config.enable:
            self.tools.register(ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
                path_append=self.exec_config.path_append,
            ))
        self.tools.register(WebSearchTool(config=self.web_search_config, proxy=self.web_proxy))
        self.tools.register(WebFetchTool(proxy=self.web_proxy))
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(
                CronTool(self.cron_service, default_timezone=self.context.timezone or "UTC")
            )

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from nanobot.agent.tools.mcp import connect_mcp_servers
        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
        except BaseException as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("message", "spawn", "cron"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        from nanobot.utils.helpers import strip_think
        return strip_think(text) or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hint, e.g. 'web_search("query")'."""
        def _fmt(tc):
            args = (tc.arguments[0] if isinstance(tc.arguments, list) else tc.arguments) or {}
            val = next(iter(args.values()), None) if isinstance(args, dict) else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}…")' if len(val) > 40 else f'{tc.name}("{val}")'
        return ", ".join(_fmt(tc) for tc in tool_calls)

    @staticmethod
    def _is_skill_md_read_event(ev: dict[str, str]) -> bool:
        if ev.get("name") != "read_file" or ev.get("status") != "ok":
            return False
        path = (ev.get("path") or "").replace("\\", "/")
        if path.endswith("/SKILL.md") or path.endswith("SKILL.md"):
            return True
        return "SKILL.md" in (ev.get("detail") or "")

    @staticmethod
    def _extract_skill_name_from_path(path: str | None) -> str | None:
        if not path:
            return None
        norm = path.replace("\\", "/").strip()
        parts = [p for p in norm.split("/") if p]
        if len(parts) >= 2 and parts[-1] == "SKILL.md":
            return parts[-2] or None
        return None

    @staticmethod
    def _extract_skill_name_from_detail(detail: str) -> str | None:
        m = re.search(r"skills[/\\]([^/\\]+)[/\\]SKILL\.md", detail)
        return m.group(1) if m else None

    @classmethod
    def _find_skill_patch_signal(
        cls,
        tool_events: list[dict[str, str]],
    ) -> tuple[str, dict[str, str]] | None:
        """Most recent SKILL.md read followed by error then later ok (same turn)."""
        for r in range(len(tool_events) - 1, -1, -1):
            ev = tool_events[r]
            if not cls._is_skill_md_read_event(ev):
                continue
            err_i: int | None = None
            for i in range(r + 1, len(tool_events)):
                if tool_events[i].get("status") == "error":
                    err_i = i
                    break
            if err_i is None:
                continue
            recovered = False
            for j in range(err_i + 1, len(tool_events)):
                if tool_events[j].get("status") == "ok":
                    recovered = True
                    break
            if not recovered:
                continue
            path = ev.get("path")
            name = cls._extract_skill_name_from_path(path) or cls._extract_skill_name_from_detail(
                ev.get("detail") or "",
            )
            if name:
                return name, ev
        return None

    @staticmethod
    def _parse_patch_analysis_tool_args(response: LLMResponse) -> dict[str, Any] | None:
        if not response.tool_calls:
            return None
        for tc in response.tool_calls:
            if tc.name == _PATCH_ANALYSIS_TOOL_NAME and isinstance(tc.arguments, dict):
                return tc.arguments
        return None

    async def _analyze_skill_usage(self, result: AgentRunResult, session_key: str) -> None:
        """Background: propose a skill patch after read + error→recovery (LLM-gated)."""
        try:
            if not result.tool_events:
                return
            found = self._find_skill_patch_signal(result.tool_events)
            if not found:
                return
            skill_name, _ev = found
            skill_path = self.workspace / "skills" / skill_name / "SKILL.md"
            if not skill_path.is_file():
                return
            current = skill_path.read_text(encoding="utf-8", errors="replace")
            events_lines = [
                f"- {e.get('name', '')} status={e.get('status', '')} path={e.get('path', '')} "
                f"detail={e.get('detail', '')[:160]}"
                for e in result.tool_events
            ]
            user_prompt = (
                "You are analyzing a completed agent turn for a possible skill instruction patch.\n\n"
                f"Session: {session_key}\n\n"
                "Tool events (chronological):\n"
                + "\n".join(events_lines)
                + "\n\nCurrent SKILL.md content:\n```markdown\n"
                + current[:24_000]
                + "\n```\n"
                "Call the structured tool with your decision."
            )
            messages = [
                {"role": "system", "content": "Respond only via the provided tool."},
                {"role": "user", "content": user_prompt},
            ]
            tools = [_skill_patch_analysis_tool_definition()]
            tool_choice: dict[str, Any] = {
                "type": "function",
                "function": {"name": _PATCH_ANALYSIS_TOOL_NAME},
            }
            llm_response = await self.provider.chat_with_retry(
                messages=messages,
                tools=tools,
                model=self.model,
                max_tokens=2048,
                temperature=0.2,
                tool_choice=tool_choice,
            )
            args = self._parse_patch_analysis_tool_args(llm_response)
            if not args or not args.get("should_patch"):
                return
            proposed = args.get("proposed_content")
            diff_desc = args.get("diff_description")
            reason = args.get("reason")
            if not isinstance(proposed, str) or not proposed.strip():
                return
            if not isinstance(diff_desc, str) or not diff_desc.strip():
                return
            if not isinstance(reason, str) or not reason.strip():
                return

            proposal_dir = self.workspace / "memory" / "skill-proposals"
            proposal_dir.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
            short_id = secrets.token_hex(4)
            prop_id = f"prop-{date_str}-{short_id}"
            created = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            orig_in = args.get("original_content")
            original_content = orig_in if isinstance(orig_in, str) else current
            doc: dict[str, Any] = {
                "id": prop_id,
                "type": "patch",
                "status": "pending",
                "skill_name": skill_name,
                "reason": reason.strip(),
                "proposed_content": proposed,
                "source_threads": [session_key],
                "created_at": created,
                "target_skill": skill_name,
                "diff_description": diff_desc.strip(),
                "original_content": original_content,
            }
            out_path = proposal_dir / f"{prop_id}.json"
            out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            logger.info("Wrote skill patch proposal {}", out_path.name)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Skill usage analysis failed: {}", exc)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        *,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
    ) -> AgentRunResult:
        """Run the agent iteration loop.

        *on_stream*: called with each content delta during streaming.
        *on_stream_end(resuming)*: called when a streaming session finishes.
        ``resuming=True`` means tool calls follow (spinner should restart);
        ``resuming=False`` means this is the final response.
        """
        result = await self.runner.run(AgentRunSpec(
            initial_messages=initial_messages,
            tools=self.tools,
            model=self.model,
            max_iterations=self.max_iterations,
            hook=_AgentLoopRunHook(
                self,
                on_progress=on_progress,
                on_stream=on_stream,
                on_stream_end=on_stream_end,
                channel=channel,
                chat_id=chat_id,
                message_id=message_id,
            ),
            error_message="Sorry, I encountered an error calling the AI model.",
            concurrent_tools=True,
        ))
        self._last_usage = result.usage
        if result.stop_reason == "max_iterations":
            logger.warning("Max iterations ({}) reached", self.max_iterations)
        elif result.stop_reason == "error":
            raw = result.final_content or ""
            logger.error("LLM returned error: {}", raw[:200])
            result.final_content = self._classify_error(raw)
        return result

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                # Preserve real task cancellation so shutdown can complete cleanly.
                # Only ignore non-task CancelledError signals that may leak from integrations.
                if not self._running or asyncio.current_task().cancelling():
                    raise
                continue
            except Exception as e:
                logger.warning("Error consuming inbound message: {}, continuing...", e)
                continue

            raw = msg.content.strip()
            if self.commands.is_priority(raw):
                ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw=raw, loop=self)
                result = await self.commands.dispatch_priority(ctx)
                if result:
                    for k, v in (msg.metadata or {}).items():
                        result.metadata.setdefault(k, v)
                    await self.bus.publish_outbound(result)
                continue
            task = asyncio.create_task(self._dispatch(msg))
            self._active_tasks.setdefault(msg.session_key, []).append(task)
            task.add_done_callback(lambda t, k=msg.session_key: self._active_tasks.get(k, []) and self._active_tasks[k].remove(t) if t in self._active_tasks.get(k, []) else None)

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message: per-session serial, cross-session concurrent."""
        lock = self._session_locks.setdefault(msg.session_key, asyncio.Lock())
        gate = self._concurrency_gate or nullcontext()
        async with lock, gate:
            try:
                on_stream = on_stream_end = None
                has_streamed = False
                if msg.metadata.get("_wants_stream"):
                    stream_base_id = f"{msg.session_key}:{time.time_ns()}"
                    stream_segment = 0

                    def _current_stream_id() -> str:
                        return f"{stream_base_id}:{stream_segment}"

                    async def on_stream(delta: str) -> None:
                        nonlocal has_streamed
                        has_streamed = True
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=msg.channel, chat_id=msg.chat_id,
                            content=delta,
                            metadata={
                                "_stream_delta": True,
                                "_stream_id": _current_stream_id(),
                            },
                        ))

                    async def on_stream_end(*, resuming: bool = False) -> None:
                        nonlocal stream_segment
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=msg.channel, chat_id=msg.chat_id,
                            content="",
                            metadata={
                                "_stream_end": True,
                                "_resuming": resuming,
                                "_stream_id": _current_stream_id(),
                            },
                        ))
                        stream_segment += 1

                response = await self._process_message(
                    msg, on_stream=on_stream, on_stream_end=on_stream_end,
                )
                if response is not None:
                    if not has_streamed:
                        response.metadata.pop("_streamed", None)
                    for k, v in (msg.metadata or {}).items():
                        response.metadata.setdefault(k, v)
                    await self.bus.publish_outbound(response)
                elif msg.channel == "cli":
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="", metadata=msg.metadata or {},
                    ))
            except asyncio.CancelledError:
                logger.info("Task cancelled for session {}", msg.session_key)
                raise
            except Exception as exc:
                logger.exception("Error processing message for session {}", msg.session_key)
                error_meta = dict(msg.metadata or {})
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=self._classify_error(str(exc)),
                    metadata=error_meta,
                ))

    async def close_mcp(self) -> None:
        """Drain pending background archives, then close MCP connections."""
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def _schedule_background(self, coro) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown)."""
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(self._background_tasks.remove)

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        # System messages: parse origin from chat_id ("channel:chat_id")
        if msg.channel == "system":
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id
                                else ("cli", msg.chat_id))
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            await self.memory_consolidator.maybe_consolidate_by_tokens(session)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
            history = session.get_history(max_messages=0)
            current_role = "assistant" if msg.sender_id == "subagent" else "user"
            messages = self.context.build_messages(
                history=history,
                current_message=msg.content, channel=channel, chat_id=chat_id,
                current_role=current_role,
            )
            run_result = await self._run_agent_loop(
                messages, channel=channel, chat_id=chat_id,
                message_id=msg.metadata.get("message_id"),
            )
            final_content = run_result.final_content
            all_msgs = run_result.messages
            self._save_turn(session, all_msgs, 1 + len(history))
            self.sessions.save(session)
            self._schedule_background(self.memory_consolidator.maybe_consolidate_by_tokens(session))
            self._schedule_background(self._analyze_skill_usage(run_result, key))
            return OutboundMessage(channel=channel, chat_id=chat_id,
                                  content=final_content or "Background task completed.")

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)

        # Slash commands
        raw = msg.content.strip()
        ctx = CommandContext(msg=msg, session=session, key=key, raw=raw, loop=self)
        if result := await self.commands.dispatch(ctx):
            return result

        await self.memory_consolidator.maybe_consolidate_by_tokens(session)

        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = session.get_history(max_messages=0)
        initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel, chat_id=msg.chat_id,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta,
            ))

        run_result = await self._run_agent_loop(
            initial_messages,
            on_progress=on_progress or _bus_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            channel=msg.channel, chat_id=msg.chat_id,
            message_id=msg.metadata.get("message_id"),
        )
        final_content = run_result.final_content
        all_msgs = run_result.messages

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        self._save_turn(session, all_msgs, 1 + len(history))
        self.sessions.save(session)
        self._schedule_background(self.memory_consolidator.maybe_consolidate_by_tokens(session))
        self._schedule_background(self._analyze_skill_usage(run_result, key))

        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)

        meta = dict(msg.metadata or {})
        if on_stream is not None:
            meta["_streamed"] = True
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=final_content,
            metadata=meta,
        )

    @staticmethod
    def _image_placeholder(block: dict[str, Any]) -> dict[str, str]:
        """Convert an inline image block into a compact text placeholder."""
        path = (block.get("_meta") or {}).get("path", "")
        return {"type": "text", "text": f"[image: {path}]" if path else "[image]"}

    def _sanitize_persisted_blocks(
        self,
        content: list[dict[str, Any]],
        *,
        truncate_text: bool = False,
        drop_runtime: bool = False,
    ) -> list[dict[str, Any]]:
        """Strip volatile multimodal payloads before writing session history."""
        filtered: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                filtered.append(block)
                continue

            if (
                drop_runtime
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
                and block["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
            ):
                continue

            if (
                block.get("type") == "image_url"
                and block.get("image_url", {}).get("url", "").startswith("data:image/")
            ):
                filtered.append(self._image_placeholder(block))
                continue

            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text = block["text"]
                if truncate_text and len(text) > self._TOOL_RESULT_MAX_CHARS:
                    text = text[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
                filtered.append({**block, "text": text})
                continue

            filtered.append(block)

        return filtered

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime
        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "tool":
                if isinstance(content, str) and len(content) > self._TOOL_RESULT_MAX_CHARS:
                    entry["content"] = content[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
                elif isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, truncate_text=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    # Strip the runtime-context prefix, keep only the user text.
                    parts = content.split("\n\n", 1)
                    if len(parts) > 1 and parts[1].strip():
                        entry["content"] = parts[1]
                    else:
                        continue
                if isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, drop_runtime=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a message directly and return the outbound payload."""
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        return await self._process_message(
            msg, session_key=session_key, on_progress=on_progress,
            on_stream=on_stream, on_stream_end=on_stream_end,
        )
