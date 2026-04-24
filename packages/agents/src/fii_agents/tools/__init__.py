"""Specialist tools. Each module exposes:
- `TOOLS` — list of JSON Schema definitions passed to Claude
- a `dispatch(name, input_, ctx) -> dict` coroutine that routes tool calls
"""
