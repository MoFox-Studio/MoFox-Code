"""WebSocket 客户端。"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Callable, Awaitable
from uuid import uuid4

import websockets
from websockets.asyncio.client import ClientConnection

from .config import ClientConfig
from .session import ClientSession, CheckpointInfo


class CodingAgentClient:
    """Coding Agent WebSocket 客户端。"""

    def __init__(self, config: ClientConfig) -> None:
        self._config = config
        self._ws: ClientConnection | None = None
        self._session = ClientSession()
        self._message_handlers: dict[str, Callable] = {}
        self._connected = False

    @property
    def session(self) -> ClientSession:
        return self._session

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    def on(self, message_type: str, handler: Callable[[dict], Awaitable[None]]) -> None:
        """注册消息处理器。"""
        self._message_handlers[message_type] = handler

    async def connect(self) -> None:
        """建立 WebSocket 连接。内置最多 3 次重试，每次间隔 2 秒。"""
        max_retries = 3
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                self._ws = await websockets.connect(
                    self._config.server_url,
                    ping_interval=20,
                    ping_timeout=10,
                )
                self._connected = True
                return
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
        raise ConnectionError(
            f"无法连接到 {self._config.server_url}（已重试 {max_retries} 次）: {last_error}"
        )

    async def disconnect(self) -> None:
        """断开连接。"""
        if self._ws:
            await self._ws.close()
            self._ws = None
            self._connected = False

    async def send(self, msg_type: str, payload: dict | None = None) -> str:
        """发送消息到后端。"""
        if not self.is_connected or self._ws is None:
            raise ConnectionError("WebSocket 未连接，无法发送消息")
        msg = {
            "type": msg_type,
            "id": str(uuid4()),
            "session_id": self._session.session_id,
            "payload": payload or {},
            "timestamp": time.time(),
        }
        await self._ws.send(json.dumps(msg))
        return msg["id"]

    async def init_session(self, force_research: bool = False, session_id: str = "") -> None:
        """发送 session.init 并等待 session.ready。session_id 非空时恢复会话。"""
        payload: dict = {
            "working_directory": self._config.project_dir,
            "force_research": force_research,
        }
        if session_id:
            payload["session_id"] = session_id
        await self.send("session.init", payload)

    async def close_session(self) -> None:
        """发送 session.close。"""
        await self.send("session.close")

    async def list_sessions(self) -> None:
        """请求历史会话列表。"""
        await self.send("session.list")

    async def delete_session(self, session_id: str) -> None:
        """删除指定会话。"""
        await self.send("session.delete", {"session_id": session_id})

    async def resume_session(self, session_id: str) -> None:
        """恢复指定会话：断开当前，用指定 session_id 重新 init。"""
        await self.close_session()
        await self.init_session(session_id=session_id)

    async def send_user_message(self, content: str, kind: str = "message") -> None:
        """发送用户消息。"""
        self._session.is_agent_busy = True
        await self.send("user.message", {"content": content, "kind": kind})

    async def send_guidance_message(self, content: str) -> None:
        """发送工作中的补充引导消息。"""
        await self.send_user_message(content, kind="guidance")

    async def send_approval(
        self, request_id: str, decision: str, prefix: str = "", reason: str = ""
    ) -> None:
        """发送审批决策。"""
        self._session.is_agent_busy = True
        payload = {
            "request_id": request_id,
            "decision": decision,
            "prefix": prefix,
        }
        if reason:
            payload["reason"] = reason
        await self.send("bash.approval", payload)
        if self._session.pending_approval_request_id == request_id:
            self._session.pending_approval_request_id = None

    async def send_interrupt(self) -> None:
        """发送中断信号。"""
        await self.send("user.interrupt")

    async def toggle_auto_review(self, enabled: bool) -> None:
        """切换自动审查。"""
        await self.send("auto_review.toggle", {"enabled": enabled})
        self._session.auto_review_enabled = enabled

    async def toggle_yolo(self, enabled: bool) -> None:
        """切换 YOLO 无审查模式。"""
        await self.send("yolo.toggle", {"enabled": enabled})
        self._session.yolo_mode = enabled

    async def send_goal(self, text: str) -> None:
        """设定目标并进入目标模式。

        仅发送 goal.set 到后端，后端 handler 会注入首次消息触发 agent。
        不额外发送 user.message 以避免双重触发。
        """
        await self.send("goal.set", {"text": text})
        self._session.is_agent_busy = True
        self._session.goal_mode = True
        self._session.goal_text = text

    async def request_rollback(
        self, mode: str = "last", checkpoint_id: str = ""
    ) -> None:
        """请求回滚。"""
        payload: dict = {"mode": mode}
        if checkpoint_id:
            payload["checkpoint_id"] = checkpoint_id
        await self.send("checkpoint.rollback", payload)

    async def request_checkpoint_list(self) -> None:
        """请求 checkpoint 列表。"""
        await self.send("checkpoint.list")

    async def send_link(self, path: str) -> None:
        """发送 session.link 消息，关联外部项目目录。"""
        await self.send("session.link", {"path": path})

    async def new_session(self) -> None:
        """开启新对话：重新 init 不带 session_id。"""
        await self.init_session()

    async def receive_loop(self) -> None:
        """消息接收循环（在后台 task 中运行）。

        连接断开时设置 _connected=False 并退出循环，
        由调用方（TUIApp.run）负责检测并显示错误提示。
        """
        while self._connected:
            msg_type = "?"  # 防止 JSON 解析阶段异常导致 NameError
            try:
                raw = await self._ws.recv()
                msg = json.loads(raw)
                msg_type = msg.get("type", "")
                payload = msg.get("payload", {})

                # 更新 session 状态
                if msg_type == "session.ready":
                    self._session.session_id = payload.get("session_id", "")
                    self._session.project_name = payload.get("project_name", "")
                    self._session.title = payload.get("title", "")
                    self._session.phase = "ready"
                elif msg_type == "agent.status":
                    self._session.update_from_status(
                        payload.get("phase", ""), payload.get("detail", "")
                    )
                elif msg_type == "agent.text" and payload.get("is_final"):
                    self._session.is_agent_busy = False
                elif msg_type == "bash.approval_request":
                    self._session.pending_approval_request_id = payload.get("request_id")
                    self._session.is_agent_busy = False  # 暂停，等待用户审批
                elif msg_type == "checkpoint.created":
                    self._session.add_checkpoint(CheckpointInfo(**payload))

                # 调用注册的 handler
                handler = self._message_handlers.get(msg_type)
                if handler:
                    await handler(payload)

            except websockets.ConnectionClosed as e:
                self._connected = False
                # 区分正常关闭与异常断开
                if e.code in (1000, 1001):
                    sys.stderr.write(f"[ws] 连接正常关闭 (code={e.code})\n")
                else:
                    sys.stderr.write(f"[ws] 连接异常断开 (code={e.code}: {e.reason})\n")
                break
            except Exception as e:
                if not self._connected:
                    break
                sys.stderr.write(f"[ws] handler error for '{msg_type}': {e}\n")
                continue
