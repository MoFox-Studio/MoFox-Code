"""客户端配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ClientConfig:
    """TUI 客户端配置。"""
    server_url: str = "ws://localhost:8765/coding-agent/ws"
    auto_review: bool = False
    theme: str = "dark"  # dark / light / monokai
    project_dir: str = ""  # 默认 cwd
    verbose: bool = False

    # 内部状态
    config_dir: Path = field(default_factory=lambda: Path.home() / ".mofox-code")

    def __post_init__(self) -> None:
        if not self.project_dir:
            self.project_dir = str(Path.cwd())

    @staticmethod
    def _generate_default_config(config_file: Path) -> None:
        """首次运行时生成默认配置文件。"""
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(
            '[server]\n'
            'url = "ws://localhost:8765/coding-agent/ws"\n'
            '\n'
            '[preferences]\n'
            'theme = "dark"\n'
            'auto_review = false\n',
            encoding="utf-8",
        )

    @classmethod
    def load(cls, overrides: dict | None = None) -> ClientConfig:
        """加载配置。

        优先级：CLI 参数 > 配置文件 > 默认值
        """
        config = cls()

        # 读取/生成配置文件
        config_file = config.config_dir / "config.toml"
        if not config_file.exists():
            config._generate_default_config(config_file)
        try:
            import tomllib
            data = tomllib.loads(config_file.read_text(encoding="utf-8"))
            server_cfg = data.get("server", {})
            if "url" in server_cfg:
                config.server_url = server_cfg["url"]
            prefs = data.get("preferences", {})
            if "theme" in prefs:
                config.theme = prefs["theme"]
            if "auto_review" in prefs:
                config.auto_review = bool(prefs["auto_review"])
        except Exception:
            pass

        # 应用 CLI overrides
        if overrides:
            for k, v in overrides.items():
                if hasattr(config, k) and v is not None:
                    setattr(config, k, v)

        return config
