"""Execution profile v1 的獨立 wire schema 與 canonical key。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import re
import struct
from types import MappingProxyType
from typing import NoReturn

__all__ = [
    "MAX_DEPTH",
    "MAX_NODES",
    "MAX_SEMANTIC_BYTES",
    "MAX_TEXT_BYTES",
    "ExecutionProfileError",
    "ExecutionProfileContractError",
    "ProfileContractError",
    "ExecutionProfileDescriptor",
    "ExecutionProfile",
    "Descriptor",
    "Profile",
    "parse_descriptor",
    "parse_profile",
    "validate_effort_value",
    "canonical_profile_bytes",
    "profile_key",
    "actual_condition_key",
    "actual_condition_missing_fields",
]

SCHEMA_VERSION = 1
MAX_DEPTH = 16
MAX_NODES = 4096
MAX_SEMANTIC_BYTES = 65536
MAX_TEXT_BYTES = 1048576
_DOMAIN_PREFIX = b"cortex.execution-profile"
_ID_FIELDS = frozenset(("id", "version", "revision"))
_PLANES = frozenset(("requested", "resolved", "observed"))
_CONDITION_FIELDS = (
    "adapter",
    "model",
    "effort",
    "loadout",
    "toolset",
    "sandbox",
    "permissions",
    "toolchain",
)
_SET_CONDITIONS = frozenset(("toolset", "permissions"))
_REQUIREMENT_FIELDS = ("role", "minimum_quality", "pin", "independence")
_METADATA_FIELDS = frozenset(
    (
        "pricing",
        "pricing_provenance",
        "timestamps",
        "evidence_refs",
        "approval_receipt_refs",
        "discovery",
    )
)
_DESCRIPTOR_FIELDS = (
    "schema_version",
    "id",
    "adapter",
    "model",
    "effort_grammar",
    "provenance",
    "metadata",
)
_PROFILE_FIELDS = (
    "schema_version",
    "plane",
    "conditions",
    "requirements",
    "provenance",
    "metadata",
)
_REF_NAMESPACE_RE = re.compile(r"[a-z][a-z0-9+.-]{1,31}\Z")
_REF_BODY_RE = re.compile(r"[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+\Z")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class ExecutionProfileError(ValueError):
    """profile wire 資料錯誤；訊息不包含欄位值。"""

    def __init__(self, code: str, locator: tuple[str | int, ...] = ()) -> None:
        self.code = code
        self.locator = tuple(locator)
        message = code
        if locator:
            message += " at " + ".".join(str(part) for part in locator)
        super().__init__(message)


ExecutionProfileContractError = ExecutionProfileError
ProfileContractError = ExecutionProfileError


def _fail(code: str, locator: tuple[str | int, ...] = ()) -> NoReturn:
    raise ExecutionProfileError(code, locator)


def _normalize_string(value: str, locator: tuple[str | int, ...]) -> str:
    """拒絕孤立 surrogate，並合併以兩個 code point 表示的 surrogate pair。"""
    result: list[str] = []
    index = 0
    while index < len(value):
        codepoint = ord(value[index])
        if 0xD800 <= codepoint <= 0xDBFF:
            if index + 1 >= len(value) or not 0xDC00 <= ord(value[index + 1]) <= 0xDFFF:
                _fail("invalid_unicode", locator)
            following = ord(value[index + 1])
            result.append(chr(0x10000 + ((codepoint - 0xD800) << 10) + following - 0xDC00))
            index += 2
            continue
        if 0xDC00 <= codepoint <= 0xDFFF:
            _fail("invalid_unicode", locator)
        result.append(value[index])
        index += 1
    return "".join(result)


def _clone_native(value: object, locator: tuple[str | int, ...] = ()) -> object:
    """複製 JSON 原生型別，不接受循環、非有限數或非字串 mapping key。"""
    active: set[int] = set()

    def clone(current: object, path: tuple[str | int, ...]) -> object:
        if type(current) is str:
            return _normalize_string(current, path)
        if current is None or type(current) is bool or type(current) is int:
            return current
        if type(current) is float:
            if not math.isfinite(current):
                _fail("non_finite_number", path)
            return current
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in active:
                _fail("cyclic_value", path)
            active.add(identity)
            copied: dict[str, object] = {}
            try:
                for key, item in current.items():
                    if type(key) is not str:
                        _fail("invalid_type", path + ("<key>",))
                    normalized = _normalize_string(key, path + ("<key>",))
                    if normalized in copied:
                        _fail("duplicate_key", path + ("<duplicate>",))
                    copied[normalized] = clone(item, path + (normalized,))
            finally:
                active.remove(identity)
            return copied
        if type(current) is list:
            identity = id(current)
            if identity in active:
                _fail("cyclic_value", path)
            active.add(identity)
            try:
                return [clone(item, path + (index,)) for index, item in enumerate(current)]
            finally:
                active.remove(identity)
        _fail("invalid_type", path)

    return clone(value, locator)


def _json_constant(value: str) -> NoReturn:
    del value
    _fail("non_finite_number")


def _json_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        _fail("non_finite_number")
    return value


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        normalized = _normalize_string(key, ("<key>",))
        if normalized in result:
            _fail("duplicate_key", ("<duplicate>",))
        result[normalized] = value
    return result


def _decode_input(value: object, locator: tuple[str | int, ...]) -> object:
    if isinstance(value, str) or isinstance(value, (bytes, bytearray)):
        if isinstance(value, str):
            text = value
            try:
                raw = text.encode("utf-8")
            except UnicodeEncodeError:
                _fail("invalid_unicode", locator)
        else:
            raw = bytes(value)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                _fail("invalid_unicode", locator)
        if len(raw) > MAX_TEXT_BYTES:
            _fail("transport_too_large", locator)
        if text.startswith("\ufeff"):
            _fail("invalid_unicode", locator)
        try:
            decoded = json.loads(
                text,
                object_pairs_hook=_json_object,
                parse_int=int,
                parse_float=_json_float,
                parse_constant=_json_constant,
            )
        except ExecutionProfileError:
            raise
        except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
            _fail("invalid_json", locator)
        return _clone_native(decoded, locator)
    return _clone_native(value, locator)


def _expect_mapping(value: object, locator: tuple[str | int, ...]) -> dict[str, object]:
    if type(value) is not dict:
        _fail("invalid_type", locator)
    return value


def _expect_list(value: object, locator: tuple[str | int, ...]) -> list[object]:
    if type(value) is not list:
        _fail("invalid_type", locator)
    return value


def _exact_keys(
    payload: dict[str, object],
    expected: tuple[str, ...] | frozenset[str],
    locator: tuple[str | int, ...],
) -> None:
    if set(payload) - set(expected):
        _fail("unknown_field", locator + ("<unknown>",))
    for key in expected:
        if key not in payload:
            _fail("missing_field", locator + (key,))


def _allowed_keys(
    payload: dict[str, object],
    allowed: tuple[str, ...] | frozenset[str],
    locator: tuple[str | int, ...],
) -> None:
    if set(payload) - set(allowed):
        _fail("unknown_field", locator + ("<unknown>",))


def _schema_version(value: object, locator: tuple[str | int, ...]) -> None:
    if type(value) is not int:
        _fail("invalid_type", locator)
    if value != SCHEMA_VERSION:
        _fail("unsupported_schema", locator)


def _id_string(value: object, locator: tuple[str | int, ...]) -> None:
    if type(value) is not str:
        _fail("invalid_type", locator)
    if not value or value != value.strip() or _CONTROL_RE.search(value):
        _fail("invalid_identifier", locator)


def _reason(value: object, locator: tuple[str | int, ...]) -> None:
    if type(value) is not str:
        _fail("invalid_type", locator)
    if not value or value != value.strip():
        _fail("invalid_reason", locator)


def _reference(value: object, locator: tuple[str | int, ...]) -> None:
    if type(value) is not str:
        _fail("invalid_type", locator)
    if not value.isascii() or len(value) > 1024 or ":" not in value:
        _fail("invalid_reference", locator)
    namespace, _, body = value.partition(":")
    if not _REF_NAMESPACE_RE.fullmatch(namespace) or not body or not _REF_BODY_RE.fullmatch(body):
        _fail("invalid_reference", locator)


def _parse_provenance(value: object, locator: tuple[str | int, ...]) -> None:
    for index, entry in enumerate(_expect_list(value, locator)):
        path = locator + (index,)
        item = _expect_mapping(entry, path)
        _exact_keys(item, ("kind", "ref"), path)
        _id_string(item["kind"], path + ("kind",))
        _reference(item["ref"], path + ("ref",))


def _parse_metadata(value: object, locator: tuple[str | int, ...]) -> None:
    item = _expect_mapping(value, locator)
    if set(item) - _METADATA_FIELDS:
        _fail("unknown_field", locator + ("<unknown>",))


def _parse_adapter(value: object, locator: tuple[str | int, ...]) -> None:
    item = _expect_mapping(value, locator)
    expected = ("id", "protocol_id", "protocol_version", "runtime_version")
    _exact_keys(item, expected, locator)
    for key in expected:
        _id_string(item[key], locator + (key,))


def _parse_model(value: object, locator: tuple[str | int, ...]) -> None:
    item = _expect_mapping(value, locator)
    _exact_keys(item, ("id", "revision"), locator)
    _id_string(item["id"], locator + ("id",))
    _id_string(item["revision"], locator + ("revision",))


def _parse_versioned_ref(value: object, locator: tuple[str | int, ...]) -> None:
    item = _expect_mapping(value, locator)
    _exact_keys(item, ("id", "version"), locator)
    _id_string(item["id"], locator + ("id",))
    _id_string(item["version"], locator + ("version",))


def _validate_grammar(
    value: object, locator: tuple[str | int, ...], *, top_level: bool
) -> dict[str, object]:
    grammar = _expect_mapping(value, locator)
    if "type" not in grammar or type(grammar["type"]) is not str:
        _fail("missing_field" if "type" not in grammar else "invalid_type", locator + ("type",))
    kind = grammar["type"]
    if kind in {"none", "boolean", "null"}:
        _exact_keys(grammar, ("type",), locator)
        if (kind == "none" and not top_level) or (kind in {"boolean", "null"} and top_level):
            _fail("invalid_grammar", locator + ("type",))
        return grammar
    if kind == "string":
        _exact_keys(grammar, ("type", "enum") if "enum" in grammar else ("type",), locator)
        if "enum" in grammar:
            values = _expect_list(grammar["enum"], locator + ("enum",))
            if not values:
                _fail("invalid_grammar", locator + ("enum",))
            seen: set[str] = set()
            for index, item in enumerate(values):
                if type(item) is not str:
                    _fail("invalid_type", locator + ("enum", index))
                if item in seen:
                    _fail("duplicate_value", locator + ("enum", index))
                seen.add(item)
        return grammar
    if kind == "integer":
        _allowed_keys(grammar, ("type", "min", "max"), locator)
        for bound in ("min", "max"):
            if bound in grammar and type(grammar[bound]) is not int:
                _fail("invalid_type", locator + (bound,))
        if "min" in grammar and "max" in grammar and grammar["min"] > grammar["max"]:
            _fail("invalid_grammar", locator)
        return grammar
    if kind == "number":
        _allowed_keys(grammar, ("type", "min", "max"), locator)
        for bound in ("min", "max"):
            if bound in grammar and (type(grammar[bound]) is not float or not math.isfinite(grammar[bound])):
                _fail("invalid_type", locator + (bound,))
        if "min" in grammar and "max" in grammar and grammar["min"] > grammar["max"]:
            _fail("invalid_grammar", locator)
        return grammar
    if kind == "array":
        _exact_keys(grammar, ("type", "items"), locator)
        _validate_grammar(grammar["items"], locator + ("items",), top_level=False)
        return grammar
    if kind == "object":
        _exact_keys(grammar, ("type", "properties", "required"), locator)
        properties = _expect_mapping(grammar["properties"], locator + ("properties",))
        for name, child in properties.items():
            if type(name) is not str or not name:
                _fail("invalid_identifier", locator + ("properties", "<unknown>"))
            _validate_grammar(child, locator + ("properties", name), top_level=False)
        required = _expect_list(grammar["required"], locator + ("required",))
        seen: set[str] = set()
        for index, name in enumerate(required):
            if type(name) is not str:
                _fail("invalid_type", locator + ("required", index))
            if name in seen or name not in properties:
                _fail("invalid_grammar", locator + ("required", index))
            seen.add(name)
        return grammar
    _fail("invalid_grammar", locator + ("type",))


def _validate_grammar_value(
    value: object, grammar: dict[str, object], locator: tuple[str | int, ...]
) -> None:
    kind = grammar["type"]
    if kind == "string":
        if type(value) is not str:
            _fail("invalid_type", locator)
        if grammar.get("enum") is not None and value not in grammar["enum"]:
            _fail("invalid_value", locator)
        return
    if kind == "integer":
        if type(value) is not int:
            _fail("invalid_type", locator)
        if "min" in grammar and value < grammar["min"] or "max" in grammar and value > grammar["max"]:
            _fail("out_of_range", locator)
        return
    if kind == "number":
        if type(value) is not float:
            _fail("invalid_type", locator)
        if not math.isfinite(value):
            _fail("non_finite_number", locator)
        if "min" in grammar and value < grammar["min"] or "max" in grammar and value > grammar["max"]:
            _fail("out_of_range", locator)
        return
    if kind == "boolean":
        if type(value) is not bool:
            _fail("invalid_type", locator)
        return
    if kind == "null":
        if value is not None:
            _fail("invalid_type", locator)
        return
    if kind == "array":
        for index, item in enumerate(_expect_list(value, locator)):
            _validate_grammar_value(item, grammar["items"], locator + (index,))
        return
    if kind == "object":
        payload = _expect_mapping(value, locator)
        properties = grammar["properties"]
        if set(payload) - set(properties):
            _fail("unknown_field", locator + ("<unknown>",))
        for name in grammar["required"]:
            if name not in payload:
                _fail("missing_field", locator + (name,))
        for name, item in payload.items():
            _validate_grammar_value(item, properties[name], locator + (name,))
        return
    _fail("invalid_grammar", locator)


def _parse_tagged(
    value: object,
    locator: tuple[str | int, ...],
    *,
    parse_known=None,
    allow_not_applicable: bool = False,
    only_not_applicable: bool = False,
) -> dict[str, object]:
    payload = _expect_mapping(value, locator)
    if "state" not in payload:
        _fail("missing_field", locator + ("state",))
    state = payload["state"]
    if type(state) is not str:
        _fail("invalid_type", locator + ("state",))
    if state == "known":
        if only_not_applicable:
            _fail("invalid_value", locator + ("state",))
        _exact_keys(payload, ("state", "value"), locator)
        if parse_known is not None:
            parse_known(payload["value"], locator + ("value",))
        return payload
    if state == "unknown":
        if only_not_applicable:
            _fail("invalid_value", locator + ("state",))
        _exact_keys(payload, ("state", "reason"), locator)
        _reason(payload["reason"], locator + ("reason",))
        return payload
    if state == "not_applicable":
        _exact_keys(payload, ("state",), locator)
        if not allow_not_applicable and not only_not_applicable:
            _fail("invalid_value", locator + ("state",))
        return payload
    _fail("invalid_value", locator + ("state",))


def _parse_effort_tagged(
    value: object, locator: tuple[str | int, ...], grammar: dict[str, object]
) -> None:
    if grammar["type"] == "none":
        _parse_tagged(value, locator, only_not_applicable=True, allow_not_applicable=True)
    else:
        _parse_tagged(
            value,
            locator,
            parse_known=lambda item, path: _validate_grammar_value(item, grammar, path),
        )


def _parse_condition_value(name: str, value: object, locator: tuple[str | int, ...]) -> None:
    if name in {"adapter", "model", "loadout", "sandbox", "toolchain"}:
        if name == "adapter":
            _parse_adapter(value, locator)
        elif name == "model":
            _parse_model(value, locator)
        else:
            _parse_versioned_ref(value, locator)
        return
    if name in _SET_CONDITIONS:
        for index, entry in enumerate(_expect_list(value, locator)):
            _parse_versioned_ref(entry, locator + (index,))
        return
    _fail("invalid_field", locator)


def _validate_descriptor(payload: dict[str, object]) -> None:
    _exact_keys(payload, _DESCRIPTOR_FIELDS, ())
    _schema_version(payload["schema_version"], ("schema_version",))
    _id_string(payload["id"], ("id",))
    _parse_adapter(payload["adapter"], ("adapter",))
    _parse_model(payload["model"], ("model",))
    _validate_grammar(payload["effort_grammar"], ("effort_grammar",), top_level=True)
    _parse_provenance(payload["provenance"], ("provenance",))
    _parse_metadata(payload["metadata"], ("metadata",))


def _validate_requirements(payload: dict[str, object]) -> None:
    _exact_keys(payload, _REQUIREMENT_FIELDS, ("requirements",))

    def parse_role(value: object, locator: tuple[str | int, ...]) -> None:
        if value is not None:
            _id_string(value, locator)

    _parse_tagged(payload["role"], ("requirements", "role"), parse_known=parse_role)
    for name in _REQUIREMENT_FIELDS[1:]:
        _parse_tagged(payload[name], ("requirements", name))


def _validate_profile(
    payload: dict[str, object], descriptor: "ExecutionProfileDescriptor"
) -> None:
    _exact_keys(payload, _PROFILE_FIELDS, ())
    _schema_version(payload["schema_version"], ("schema_version",))
    plane = payload["plane"]
    if type(plane) is not str or plane not in _PLANES:
        _fail("invalid_value", ("plane",))
    conditions = _expect_mapping(payload["conditions"], ("conditions",))
    _exact_keys(conditions, _CONDITION_FIELDS, ("conditions",))
    for name in _CONDITION_FIELDS:
        locator = ("conditions", name)
        if name == "effort":
            _parse_effort_tagged(conditions[name], locator, descriptor._wire["effort_grammar"])
        else:
            _parse_tagged(
                conditions[name],
                locator,
                parse_known=lambda item, path, field=name: _parse_condition_value(field, item, path),
            )
    adapter = conditions["adapter"]
    if adapter.get("state") == "known" and adapter["value"] != descriptor._wire["adapter"]:
        _fail("descriptor_mismatch", ("conditions", "adapter", "value"))
    model = conditions["model"]
    if model.get("state") == "known" and model["value"] != descriptor._wire["model"]:
        _fail("descriptor_mismatch", ("conditions", "model", "value"))
    _validate_requirements(_expect_mapping(payload["requirements"], ("requirements",)))
    _parse_provenance(payload["provenance"], ("provenance",))
    _parse_metadata(payload["metadata"], ("metadata",))


@dataclass(frozen=True)
class _Stats:
    depth: int
    nodes: int
    semantic_bytes: int


def _measure(value: object, locator: tuple[str | int, ...]) -> _Stats:
    nodes = 0
    maximum_depth = 0
    active: set[int] = set()

    def visit(current: object, depth: int, path: tuple[str | int, ...]) -> None:
        nonlocal nodes, maximum_depth
        if depth > MAX_DEPTH:
            _fail("depth_exceeded", path)
        nodes += 1
        if nodes > MAX_NODES:
            _fail("node_count_exceeded", path)
        maximum_depth = max(maximum_depth, depth)
        if isinstance(current, Mapping) or type(current) is list:
            identity = id(current)
            if identity in active:
                _fail("cyclic_value", path)
            active.add(identity)
            try:
                if isinstance(current, Mapping):
                    for key, item in current.items():
                        visit(key, depth + 1, path + ("<key>",))
                        visit(item, depth + 1, path + (str(key),))
                else:
                    for index, item in enumerate(current):
                        visit(item, depth + 1, path + (index,))
            finally:
                active.remove(identity)

    visit(value, 1, locator)
    try:
        semantic_bytes = len(_typed_json_bytes(value))
    except (UnicodeEncodeError, RecursionError):
        _fail("invalid_unicode", locator)
    if semantic_bytes > MAX_SEMANTIC_BYTES:
        _fail("semantic_size_exceeded", locator)
    return _Stats(maximum_depth, nodes, semantic_bytes)


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw(item) for item in value]
    return value


def _typed(value: object) -> list[object]:
    if value is None:
        return ["n"]
    if type(value) is bool:
        return ["b", value]
    if type(value) is int:
        return ["i", str(value)]
    if type(value) is float:
        if not math.isfinite(value):
            _fail("non_finite_number")
        bits = struct.unpack(">Q", struct.pack(">d", value))[0]
        return ["f", f"{bits:016x}"]
    if type(value) is str:
        return ["s", value]
    if type(value) is list or type(value) is tuple:
        return ["a", [_typed(item) for item in value]]
    if isinstance(value, Mapping):
        members: list[tuple[str, list[object]]] = []
        for key, item in value.items():
            if type(key) is not str:
                _fail("invalid_type", ("<key>",))
            members.append((key, _typed(item)))
        members.sort(key=lambda item: item[0].encode("utf-8"))
        return ["o", [[key, item] for key, item in members]]
    _fail("invalid_type")


def _render_string(value: str) -> str:
    pieces = ['"']
    for character in value:
        codepoint = ord(character)
        if character == '"':
            pieces.append('\\"')
        elif character == "\\":
            pieces.append("\\\\")
        elif codepoint <= 0x1F:
            pieces.append(f"\\u{codepoint:04x}")
        else:
            pieces.append(character)
    pieces.append('"')
    return "".join(pieces)


def _render_typed(value: object) -> str:
    if type(value) is str:
        return _render_string(value)
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is list:
        return "[" + ",".join(_render_typed(item) for item in value) + "]"
    _fail("invalid_type")


def _typed_json_bytes(value: object) -> bytes:
    return _render_typed(_typed(value)).encode("utf-8")


class _ImmutableRecord:
    __slots__ = ("_wire", "_stats")

    def __init__(self, wire: object, stats: _Stats, token: object) -> None:
        if token is not _CONSTRUCTOR_TOKEN:
            raise TypeError("profile record 必須經 parse 函式建立")
        object.__setattr__(self, "_wire", wire)
        object.__setattr__(self, "_stats", stats)

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del value
        raise AttributeError(f"{type(self).__name__} is immutable")

    def to_dict(self) -> dict[str, object]:
        result = _thaw(self._wire)
        if type(result) is not dict:
            raise TypeError("record wire must be an object")
        return result

    @property
    def depth(self) -> int:
        return self._stats.depth

    @property
    def node_count(self) -> int:
        return self._stats.nodes

    @property
    def semantic_bytes(self) -> int:
        return self._stats.semantic_bytes


class ExecutionProfileDescriptor(_ImmutableRecord):
    __slots__ = ()

    @property
    def descriptor_id(self) -> str:
        return self._wire["id"]

    @property
    def schema_version(self) -> int:
        return self._wire["schema_version"]

    @property
    def id(self) -> str:
        return self._wire["id"]

    @property
    def adapter(self) -> Mapping[str, object]:
        return self._wire["adapter"]

    @property
    def model(self) -> Mapping[str, object]:
        return self._wire["model"]

    @property
    def effort_grammar(self) -> Mapping[str, object]:
        return self._wire["effort_grammar"]

    @property
    def provenance(self) -> tuple[object, ...]:
        return self._wire["provenance"]

    @property
    def metadata(self) -> Mapping[str, object]:
        return self._wire["metadata"]

    def __eq__(self, other: object) -> bool:
        return type(self) is type(other) and self._wire == other._wire

    def __hash__(self) -> int:
        return hash(_typed_json_bytes(self._wire))


class ExecutionProfile(_ImmutableRecord):
    __slots__ = ("_descriptor",)

    def __init__(
        self,
        wire: object,
        stats: _Stats,
        descriptor: ExecutionProfileDescriptor,
        token: object,
    ):
        super().__init__(wire, stats, token)
        object.__setattr__(self, "_descriptor", descriptor)

    @property
    def descriptor(self) -> ExecutionProfileDescriptor:
        return self._descriptor

    @property
    def schema_version(self) -> int:
        return self._wire["schema_version"]

    @property
    def plane(self) -> str:
        return self._wire["plane"]

    @property
    def conditions(self) -> Mapping[str, object]:
        return self._wire["conditions"]

    @property
    def requirements(self) -> Mapping[str, object]:
        return self._wire["requirements"]

    @property
    def provenance(self) -> tuple[object, ...]:
        return self._wire["provenance"]

    @property
    def metadata(self) -> Mapping[str, object]:
        return self._wire["metadata"]

    def __eq__(self, other: object) -> bool:
        return (
            type(self) is type(other)
            and self._wire == other._wire
            and self._descriptor == other._descriptor
        )

    def __hash__(self) -> int:
        return hash(_typed_json_bytes(self._wire))


_CONSTRUCTOR_TOKEN = object()

Descriptor = ExecutionProfileDescriptor
Profile = ExecutionProfile


def parse_descriptor(value: object) -> ExecutionProfileDescriptor:
    payload = _expect_mapping(_decode_input(value, ("descriptor",)), ())
    stats = _measure(payload, ("descriptor",))
    _validate_descriptor(payload)
    return ExecutionProfileDescriptor(_freeze(payload), stats, _CONSTRUCTOR_TOKEN)


def parse_profile(
    value: object,
    descriptor: ExecutionProfileDescriptor | Mapping[str, object] | str | bytes,
) -> ExecutionProfile:
    parsed_descriptor = descriptor if isinstance(descriptor, ExecutionProfileDescriptor) else parse_descriptor(descriptor)
    payload = _expect_mapping(_decode_input(value, ("profile",)), ())
    stats = _measure(payload, ("profile",))
    if parsed_descriptor.node_count + stats.nodes > MAX_NODES:
        _fail("node_count_exceeded", ("profile",))
    if parsed_descriptor.semantic_bytes + stats.semantic_bytes > MAX_SEMANTIC_BYTES:
        _fail("semantic_size_exceeded", ("profile",))
    _validate_profile(payload, parsed_descriptor)
    combined = _Stats(
        max(parsed_descriptor.depth, stats.depth),
        parsed_descriptor.node_count + stats.nodes,
        parsed_descriptor.semantic_bytes + stats.semantic_bytes,
    )
    return ExecutionProfile(
        _freeze(payload), combined, parsed_descriptor, _CONSTRUCTOR_TOKEN
    )


def validate_effort_value(
    descriptor: ExecutionProfileDescriptor | Mapping[str, object], value: object
) -> object:
    """以 descriptor 的原生 grammar 驗證 effort，不轉成共通 enum。"""
    parsed = descriptor if isinstance(descriptor, ExecutionProfileDescriptor) else parse_descriptor(descriptor)
    grammar = _thaw(parsed._wire["effort_grammar"])
    if grammar["type"] == "none":
        _fail("effort_not_applicable", ("effort",))
    _validate_grammar_value(value, grammar, ("effort",))
    return value


def _normalized_conditions(profile: ExecutionProfile) -> dict[str, object]:
    conditions = _thaw(profile._wire["conditions"])
    for name in _SET_CONDITIONS:
        wrapper = conditions[name]
        if wrapper["state"] != "known":
            continue
        keyed = [(_typed_json_bytes(item), item) for item in wrapper["value"]]
        keyed.sort(key=lambda item: item[0])
        unique: list[object] = []
        previous: bytes | None = None
        for encoded, item in keyed:
            if encoded != previous:
                unique.append(item)
                previous = encoded
        wrapper["value"] = unique
    return conditions


def _record_projection(profile: ExecutionProfile) -> dict[str, object]:
    return {
        "schema_version": profile._wire["schema_version"],
        "plane": profile._wire["plane"],
        "conditions": _normalized_conditions(profile),
        "requirements": _thaw(profile._wire["requirements"]),
    }


def _actual_projection(profile: ExecutionProfile) -> dict[str, object]:
    return {
        "schema_version": profile._wire["schema_version"],
        "conditions": _normalized_conditions(profile),
    }


def canonical_profile_bytes(profile: ExecutionProfile) -> bytes:
    if not isinstance(profile, ExecutionProfile):
        _fail("invalid_profile")
    return _typed_json_bytes(_record_projection(profile))


def _framed_key(domain: str, canonical: bytes) -> str:
    framed = (
        _DOMAIN_PREFIX
        + b"\0v1\0"
        + domain.encode("ascii")
        + b"\0"
        + struct.pack(">Q", len(canonical))
        + canonical
    )
    return f"epk:v1:{domain}:{sha256(framed).hexdigest()}"


def profile_key(profile: ExecutionProfile) -> str:
    if not isinstance(profile, ExecutionProfile):
        _fail("invalid_profile")
    domain = "request" if profile.plane == "requested" else profile.plane
    return _framed_key(domain, canonical_profile_bytes(profile))


def actual_condition_missing_fields(
    profile: ExecutionProfile,
) -> tuple[tuple[str | int, ...], ...]:
    if not isinstance(profile, ExecutionProfile) or profile.plane != "observed":
        return (("plane",),)
    missing = []
    for name in _CONDITION_FIELDS:
        wrapper = profile._wire["conditions"][name]
        if wrapper["state"] == "known":
            continue
        if name == "effort" and wrapper["state"] == "not_applicable":
            continue
        missing.append(("conditions", name))
    return tuple(sorted(missing))


def actual_condition_key(profile: ExecutionProfile) -> str | None:
    if not isinstance(profile, ExecutionProfile):
        _fail("invalid_profile")
    if actual_condition_missing_fields(profile):
        return None
    return _framed_key("actual", _typed_json_bytes(_actual_projection(profile)))
