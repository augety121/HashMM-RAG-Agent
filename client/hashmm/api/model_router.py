"""Model Router v17 — route tasks to optimal model based on complexity.

Strategy:
  - trivial tasks (greeting, short Q&A) → fast/cheap model
  - medium tasks (code, docs) → standard model
  - complex tasks (multi-file projects, long analysis) → best model
  - fallback chain: primary → secondary → error

Supports: DeepSeek, OpenAI, Anthropic, local models.
"""
from __future__ import annotations
import time, logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("hashmm.router")


@dataclass
class ModelConfig:
    name: str                      # display name
    provider: str                  # "deepseek" / "openai" / "anthropic" / "local"
    model_id: str                  # API model string
    api_base: str = ""             # custom endpoint
    api_key: str = ""              # API key
    max_tokens: int = 4096
    supports_tools: bool = True
    supports_streaming: bool = True
    cost_per_1k_input: float = 0.001   # ¥
    cost_per_1k_output: float = 0.002  # ¥
    tier: str = "standard"         # "fast" / "standard" / "best"


@dataclass
class ModelRegistry:
    """Registry of available models with routing logic."""
    models: dict[str, ModelConfig] = field(default_factory=dict)
    default_model: str = ""
    routing_rules: dict[str, str] = field(default_factory=dict)

    def register(self, config: ModelConfig):
        self.models[config.name] = config
        if not self.default_model:
            self.default_model = config.name

    def get_model_for_task(self, task_type: str, complexity: str = "medium") -> ModelConfig | None:
        """Route task to optimal model."""
        # Check routing rules
        rule_key = f"{task_type}:{complexity}"
        if rule_key in self.routing_rules:
            name = self.routing_rules[rule_key]
            if name in self.models:
                return self.models[name]

        # Default routing by complexity
        tier_map = {
            "trivial": "fast",
            "simple": "fast",
            "medium": "standard",
            "complex": "best",
        }
        target_tier = tier_map.get(complexity, "standard")

        # Find model matching tier
        for m in self.models.values():
            if m.tier == target_tier:
                return m

        # Fallback to default
        return self.models.get(self.default_model)

    def list_models(self) -> list[dict]:
        return [
            {"name": m.name, "provider": m.provider, "model_id": m.model_id,
             "tier": m.tier, "supports_tools": m.supports_tools}
            for m in self.models.values()
        ]


# ═══════════════════════════════════════════════════════════════════
# Complexity estimator
# ═══════════════════════════════════════════════════════════════════

def estimate_complexity(query: str, intent: dict, workspace_file_count: int = 0) -> str:
    """Estimate task complexity for model routing."""
    q = query.strip()

    # Trivial: very short greetings (Chinese is denser, so lower threshold)
    chinese_chars = sum(1 for c in q if '\u4e00' <= c <= '\u9fff')
    effective_len = len(q) if chinese_chars == 0 else chinese_chars * 2 + (len(q) - chinese_chars)
    if effective_len < 12:
        return "trivial"

    # Complex signals
    complex_signals = ["项目", "project", "重构", "refactor", "系统", "framework",
                       "完整", "多文件", "multi-file", "全部", "整个"]
    if any(s in q.lower() for s in complex_signals):
        return "complex"

    # Complex if workspace has many files
    if workspace_file_count > 10:
        return "complex"

    # Medium for code/document tasks
    task = intent.get("task", "chat")
    if task in ("code", "document", "data", "modify"):
        return "medium"

    # Simple for knowledge Q&A
    if task == "chat":
        return "simple"

    return "medium"


# ═══════════════════════════════════════════════════════════════════
# Default registry setup
# ═══════════════════════════════════════════════════════════════════

_registry = ModelRegistry()

def get_registry() -> ModelRegistry:
    return _registry

def setup_default_models(deepseek_key: str = "", deepseek_base: str = ""):
    """Setup default DeepSeek models with tiered routing."""
    _registry.register(ModelConfig(
        name="deepseek-chat",
        provider="deepseek",
        model_id="deepseek-chat",
        api_base=deepseek_base or "https://api.deepseek.com",
        api_key=deepseek_key,
        tier="fast",
        cost_per_1k_input=0.0005,
        cost_per_1k_output=0.001,
    ))
    _registry.register(ModelConfig(
        name="deepseek-v4-pro",
        provider="deepseek",
        model_id="deepseek-v4-pro",
        api_base=deepseek_base or "https://api.deepseek.com",
        api_key=deepseek_key,
        tier="standard",
        cost_per_1k_input=0.001,
        cost_per_1k_output=0.002,
    ))
    _registry.default_model = "deepseek-v4-pro"
