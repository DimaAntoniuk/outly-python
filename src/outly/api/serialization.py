from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any


def camel_case(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.title() for part in tail)


def serialize(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            camel_case(f.name): serialize(getattr(value, f.name)) for f in fields(value)
        }
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize(item) for item in value]
    return value
