"""Custom LangGraph serializer that handles our Pydantic models cleanly.

The default JsonPlusSerializer calls `obj.model_dump()` which returns Python types
(date, Decimal). ormsgpack can't serialize those, so it raises. Override to use
`mode="json"` which produces JSON-primitive types throughout.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


class FiiJsonPlusSerializer(JsonPlusSerializer):
    """Serializer that round-trips our Pydantic v2 models through JSON-mode dump.

    On deserialization we accept the dict back as-is (consumers re-validate as needed).
    """

    def _default(self, obj: Any) -> Any:
        if hasattr(obj, "model_dump") and callable(obj.model_dump):
            try:
                return obj.model_dump(mode="json")
            except Exception:
                return super()._default(obj)
        return super()._default(obj)
