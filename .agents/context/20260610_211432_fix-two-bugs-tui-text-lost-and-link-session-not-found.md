# fix-two-bugs-tui-text-lost-and-link-session-not-found

> Created: 2026-06-10 21:14:32

# 修复两个 Bug

## Bug 1: 前端 TUI 偶尔吞掉 agent 文字消息

### 现象
工具调用后 LLM 产出文本（后端日志: `工具后续响应: round=1, chunks=45, tool_calls=0`），但前端只显示 `Thinking (6.6s)`，agent 文本消息不见了。

### 根因分析
在 `chatter.py` 的 `_interaction_loop` 中，工具调用路径 (`_handle_tool_calls` 返回后)：

```python
# L243-252
if current:
    if current.reasoning_content:
        await self._stream_thinking_to_frontend(...)
    await session_mgr.broadcast_to_session(session_id, {
        "type": "agent.text",
        "payload": {"content": "", "is_final": True},  # ← content 硬编码为空字符串！
    })
```

虽然流式文本已通过非 final chunks 发送，但如果前端 `_stream_renderer` 因故为 None（如被意外清理），final content="" 会导致前端走 else 分支显示"模型未返回文本输出"，并且 `_active_agent_index` 被设为 None，视觉上 agent 文本"消失"。

同样的问题也存在于 `_handle_tool_calls` 内部的循环逻辑中——多轮工具调用间发送的 `agent.text` final 也是 content=""。

### 修复方案

**后端 (chatter.py)**：
1. 在 `_interaction_loop` 工具调用后发送 `agent.text` final 时，携带 `current.message` 的完整文本（而非空字符串）
2. 在 `_handle_tool_calls` 内部循环中同上处理

**前端 (app.py)**：
3. `_on_agent_text` final 分支中增加防御：如果 `_stream_renderer` 为 None 且 content 为空，但 `_active_agent_index` 不为 None（说明之前已有流式渲染），跳过 else 分支，不显示"模型未返回文本输出"

### 涉及文件
- `C:\Projects\python\MoFox\Neo-MoFox\plugins\coding_agent\chatter.py`
- `src/mofox_code/tui/app.py`

---

## Bug 2: 恢复 session 后 `/link` 报错找不到会话

### 现象
恢复 session → 用 `/link` 关联项目 → 后台报错 "chatter 执行失败，coding agent 找不到会话"

### 根因分析
在 `adapter.py` 的 `_handle_link` 方法中，第6步发送 user envelope 到 core_sink：

```python
# L591-600 (约)
envelope = MessageBuilder()...build()
await self.core_sink.send(envelope)
```

但没有像 `user.message` 处理那样先调用 `extract_stream_id` + `bind_stream_id`：

```python
# user.message 的正确处理 (L296-299)
from src.core.transport.message_receive.utils import extract_stream_id
stream_id = extract_stream_id(envelope["message_info"])
session_mgr.bind_stream_id(session_id=session.id, stream_id=stream_id)
await self.core_sink.send(envelope)
```

chatter 的 `execute()` 方法通过 `get_session_by_stream_id(self.stream_id)` 查找 session。由于 stream_id → session_id 映射未建立，找到 None → 返回 Failure("无法获取 Coding Agent 会话")。

### 修复方案
在 `_handle_link` 中发送 envelope 前，补齐 `extract_stream_id` + `bind_stream_id` 调用。

### 涉及文件
- `C:\Projects\python\MoFox\Neo-MoFox\plugins\coding_agent\adapter.py`

---

## 验收标准
1. **Bug 1**: 工具调用后的 agent 文本不再丢失，流式内容和 final 确认均正常显示
2. **Bug 2**: resume session 后 `/link` 命令能正常关联项目，不再报找不到会话
3. 不引入新的回归问题
