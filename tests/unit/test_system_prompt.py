"""The agent system prompt must exist and stay static (no template placeholders)."""

from apps.backend.app.main import SYSTEM_PROMPT_PATH

TOOLS = [
    "search_assets",
    "list_alarms",
    "get_alarm_context",
    "analyze_alarms",
    "find_correlated_alarms",
    "search_similar_tickets",
    "find_tickets",
    "create_ticket",
    "update_ticket",
    "search_knowledge_base",
]


def test_system_prompt_is_static_and_names_every_tool():
    text = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    assert text.strip()
    assert "{{" not in text and "}}" not in text
    for tool in TOOLS:
        assert f"`{tool}" in text, tool
