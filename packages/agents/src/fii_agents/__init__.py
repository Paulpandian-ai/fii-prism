"""LangGraph orchestrator and specialist agents for FII-PRISM."""

from fii_agents.advisor import (
    ADVISOR_TOOLS,
    AdvisorToolContext,
    AdvisorTurn,
    StreamFrame,
    run_advisor_turn,
    stream_advisor_turn,
)
from fii_agents.budget import Budget, estimate_cost_usd
from fii_agents.model import MODEL_HAIKU, MODEL_OPUS, MODEL_SONNET, Model, is_fake_mode
from fii_agents.orchestrator import (
    build_graph,
    ensure_checkpoint_tables,
    run_analysis,
    stream_analysis,
)
from fii_agents.prompts import DEFAULT_PROMPTS, PromptRecord, load_active_prompt, seed_defaults
from fii_agents.state import AnalysisState, SpecialistError

__version__ = "0.1.0"

__all__ = [
    "ADVISOR_TOOLS",
    "DEFAULT_PROMPTS",
    "MODEL_HAIKU",
    "MODEL_OPUS",
    "MODEL_SONNET",
    "AdvisorToolContext",
    "AdvisorTurn",
    "AnalysisState",
    "Budget",
    "Model",
    "PromptRecord",
    "SpecialistError",
    "StreamFrame",
    "build_graph",
    "ensure_checkpoint_tables",
    "estimate_cost_usd",
    "is_fake_mode",
    "load_active_prompt",
    "run_advisor_turn",
    "run_analysis",
    "seed_defaults",
    "stream_advisor_turn",
    "stream_analysis",
]
