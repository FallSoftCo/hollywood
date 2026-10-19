"""Bounded schema diagnostics without echoing request values or unknown keys."""
import re

def details(error, model):
    names = set()
    def schema_names(value):
        if isinstance(value, dict):
            names.update(value.get("properties", {}))
            if isinstance(value.get("const"), str):
                names.add(value["const"])
            for child in value.values():
                schema_names(child)
        elif isinstance(value, list):
            for child in value:
                schema_names(child)
    schema_names(model.model_json_schema())
    result = []
    for item in error.errors(include_input=False, include_url=False)[:8]:
        path = [part if isinstance(part, str) and part in names
                else "<item>" if type(part) is int else "<field>"
                for part in item["loc"][:12]]
        code = item["type"]
        if not re.fullmatch(r"[a-z_]{1,80}", code):
            code = "invalid_field"
        constraints = {key: value for key, value in (item.get("ctx") or {}).items()
                       if key in {"min_length", "max_length", "ge", "le", "gt", "lt"}
                       and type(value) is int}
        result.append({"path": path, "code": code, "constraints": constraints})
    return result
