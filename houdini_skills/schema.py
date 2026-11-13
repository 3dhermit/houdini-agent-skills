"""Tiny JSON-schema validator for skill inputs.

We only need the subset agents actually hit: type, required, enum, defaults,
nested objects, min/max, and unknown-key rejection. Pulling in jsonschema would
make the package unrunnable inside hython without a pip install, and hython's
Python is not the Python you pip into.
"""
from __future__ import annotations

from .errors import SkillFailure

_TYPE_MAP = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
    "null": (type(None),),
}


def _type_name(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def validate(payload, schema, *, path: str = "payload", strict: bool = True):
    """Validate and normalise `payload` against `schema`.

    Returns the normalised payload (defaults filled, numbers coerced). Raises
    SkillFailure('invalid_input') with a dotted path on the first problem.
    """
    if not isinstance(schema, dict):
        raise SkillFailure("invalid_schema", f"{path}: schema must be an object")

    if schema.get("type") == "object" and not isinstance(payload, dict):
        raise SkillFailure("invalid_input",
                           f"{path}: expected object, got {_type_name(payload)}")

    props = schema.get("properties", {}) or {}
    required = schema.get("required", []) or []
    result: dict = {}

    if isinstance(payload, dict):
        for key in required:
            if key not in payload or payload[key] is None:
                raise SkillFailure("invalid_input",
                                   f"{path}.{key}: required",
                                   hint=f"supply {key} (type: "
                                        f"{props.get(key, {}).get('type', 'any')})")
        for key, spec in props.items():
            if key in payload:
                result[key] = _check(payload[key], spec, f"{path}.{key}")
            elif "default" in spec:
                result[key] = spec["default"]
        if strict:
            extra = sorted(set(payload) - set(props))
            if extra:
                raise SkillFailure(
                    "invalid_input",
                    f"{path}: unknown key(s) {extra}",
                    hint="known keys: " + ", ".join(sorted(props)),
                    details={"unknown": extra},
                )
        for key, value in payload.items():
            if key not in props:
                result[key] = value  # preserved, but only when strict=False
        return result

    return _check(payload, schema, path)


def _check(value, spec: dict, path: str):
    types = spec.get("type")
    if types is None:
        return value
    if isinstance(types, str):
        types = [types]
    py = tuple(t for name in types for t in (_TYPE_MAP.get(name) or ()))
    if py and isinstance(value, bool) and "boolean" not in types:
        raise SkillFailure("invalid_input", f"{path}: expected {'|'.join(types)}, got boolean")
    if py and not isinstance(value, py):
        # integer accepts an int-valued float (JSON writes 5.0 for 5 sometimes)
        if "integer" in types and isinstance(value, float) and float(value).is_integer():
            return int(value)
        raise SkillFailure("invalid_input",
                           f"{path}: expected {'|'.join(types)}, got {_type_name(value)}")
    enum = spec.get("enum")
    if enum is not None and value not in enum:
        raise SkillFailure("invalid_input",
                           f"{path}: {value!r} not in {enum}",
                           hint="valid values: " + ", ".join(map(str, enum)))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in spec and value < spec["minimum"]:
            raise SkillFailure("invalid_input", f"{path}: {value} < minimum {spec['minimum']}")
        if "maximum" in spec and value > spec["maximum"]:
            raise SkillFailure("invalid_input", f"{path}: {value} > maximum {spec['maximum']}")
    if isinstance(value, str) and "minLength" in spec and len(value) < spec["minLength"]:
        raise SkillFailure("invalid_input", f"{path}: shorter than minLength {spec['minLength']}")
    if isinstance(value, list):
        item_spec = spec.get("items")
        if item_spec:
            return [_check(v, item_spec, f"{path}[{i}]") for i, v in enumerate(value)]
    if isinstance(value, dict) and spec.get("properties"):
        return validate(value, spec, path=path, strict=False)
    return value
