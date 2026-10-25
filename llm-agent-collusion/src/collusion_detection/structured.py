"""Build strict provider schemas from our trusted system output contract."""

import copy
import json

SCHEMA_MARKER = "\nOUTPUT SCHEMA:\n"


def response_contract(messages):
    """Source/user messages cannot alter the provider output schema."""
    for message in messages:
        if message.get("role") == "system" and SCHEMA_MARKER in message.get("content", ""):
            return json.loads(message["content"].rsplit(SCHEMA_MARKER, 1)[1])
    return None


def strict_schema(schema):
    """Strict compatible endpoints require every object property to be required.

    Optional Pydantic fields remain nullable, with explicit nulls in responses.
    Pydantic validates the richer cross-field constraints after transport.
    """
    result = copy.deepcopy(schema)

    def visit(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(result)
    return result


def provider_format(messages):
    schema = response_contract(messages)
    if schema is None:
        return None
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema.get("title", "oversight_output"),
            "strict": True,
            "schema": strict_schema(schema),
        },
    }
