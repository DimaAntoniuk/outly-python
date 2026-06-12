import re

VARIABLE_PATTERN = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


def parse_variables(content: str) -> list[str]:
    seen: dict[str, None] = {}
    for match in VARIABLE_PATTERN.finditer(content):
        seen.setdefault(match.group(1))
    return list(seen)


def resolve_variables(content: str, variables: dict[str, str]) -> str:
    lowered = {key.lower(): value for key, value in variables.items()}

    def substitute(match: re.Match[str]) -> str:
        value = lowered.get(match.group(1).lower())
        return value if value is not None else match.group(0)

    return VARIABLE_PATTERN.sub(substitute, content)


def resolve_for_recipient(
    subject: str, body: str, column_data: dict[str, str]
) -> tuple[str, str]:
    return resolve_variables(subject, column_data), resolve_variables(body, column_data)
