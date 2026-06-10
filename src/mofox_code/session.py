"""客户端会话状态。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CheckpointInfo:
    """Checkpoint 信息（从后端接收）。"""
    id: str
    step: int
    tool: str
    description: str
    files_affected: int
    reversible: bool


@dataclass
class ClientSession:
    """客户端会话状态。"""
    session_id: str = ""
    project_name: str = ""
    phase: str = "connecting"  # connecting, researching, ready, thinking, coding, reviewing
    title: str = ""  # 会话标题（由后端生成）
    auto_review_enabled: bool = False
    checkpoints: list[CheckpointInfo] = field(default_factory=list)
    is_agent_busy: bool = False  # agent 正在处理中，不接受新输入
    pending_approval_request_id: str | None = None  # 当前待审批请求

    def update_from_status(self, phase: str, detail: str) -> None:
        self.phase = phase
        self.is_agent_busy = phase not in {"ready", "error"}

    def add_checkpoint(self, info: CheckpointInfo) -> None:
        self.checkpoints.append(info)

    def clear_checkpoints_after(self, checkpoint_id: str) -> None:
        """回滚后清理已回滚的 checkpoints。"""
        idx = None
        for i, cp in enumerate(self.checkpoints):
            if cp.id == checkpoint_id:
                idx = i
                break
        if idx is not None:
            self.checkpoints = self.checkpoints[:idx]
