from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from frame_proxy.domain.errors import ErrorCode, ValidationError


IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,47}$")
HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True, slots=True)
class InspectionRequest:
    inspection_id: str
    sensitivity: float
    sample_stride: int
    min_scene_frames: int


@dataclass(frozen=True, slots=True)
class ContactSheetRequest:
    sheet_id: str
    columns: int
    tile_width: int
    gutter: int
    selection: str
    background_rgb: tuple[int, int, int]
    revision: int | None


@dataclass(frozen=True, slots=True)
class RevisionOperation:
    kind: str
    scene_index: int | None = None
    at_frame: int | None = None
    from_scene_index: int | None = None
    to_scene_index: int | None = None

    def as_dict(self) -> dict[str, Any]:
        if self.kind == "split":
            return {
                "type": "split",
                "scene_index": self.scene_index,
                "at_frame": self.at_frame,
            }
        return {
            "type": "merge",
            "from_scene_index": self.from_scene_index,
            "to_scene_index": self.to_scene_index,
        }


@dataclass(frozen=True, slots=True)
class RevisionRequest:
    submission_id: str
    base_revision: int
    operations: tuple[RevisionOperation, ...]
    reason: str | None


def validate_identifier(value: str, field_name: str) -> str:
    text = value.strip().lower()
    if not IDENTIFIER_PATTERN.fullmatch(text):
        raise ValidationError(
            f"{field_name} must match {IDENTIFIER_PATTERN.pattern}",
            code=ErrorCode.INVALID_IDENTIFIER,
            details={"field": field_name},
        )
    return text


def validate_clip_id(value: str) -> str:
    return validate_identifier(value, "clip_id")


def _require_object(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ValidationError("request body must be a JSON object")
    return body


def _number(
    body: dict[str, Any],
    key: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw = body.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValidationError(f"{key} must be numeric", details={"field": key})
    value = float(raw)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValidationError(
            f"{key} must be between {minimum} and {maximum}",
            details={"field": key},
        )
    return value


def _integer(
    body: dict[str, Any],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = body.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValidationError(f"{key} must be an integer", details={"field": key})
    if not minimum <= raw <= maximum:
        raise ValidationError(
            f"{key} must be between {minimum} and {maximum}",
            details={"field": key},
        )
    return raw


def _required_integer(
    body: dict[str, Any],
    key: str,
    minimum: int,
    maximum: int,
) -> int:
    raw = body.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValidationError(f"{key} must be an integer", details={"field": key})
    if not minimum <= raw <= maximum:
        raise ValidationError(
            f"{key} must be between {minimum} and {maximum}",
            details={"field": key},
        )
    return raw


def _optional_revision(body: dict[str, Any]) -> int | None:
    raw = body.get("revision")
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValidationError(
            "revision must be a positive integer",
            details={"field": "revision"},
        )
    return raw


def validate_inspection_request(body: Any) -> InspectionRequest:
    value = _require_object(body)
    inspection_id = validate_identifier(
        str(value.get("inspection_id", "")),
        "inspection_id",
    )
    return InspectionRequest(
        inspection_id=inspection_id,
        sensitivity=_number(value, "sensitivity", 0.55, 0.05, 0.95),
        sample_stride=_integer(value, "sample_stride", 1, 1, 4),
        min_scene_frames=_integer(value, "min_scene_frames", 2, 1, 300),
    )


def _parse_color(raw: Any) -> tuple[int, int, int]:
    text = str(raw or "#20252c")
    if not HEX_COLOR_PATTERN.fullmatch(text):
        raise ValidationError(
            "background must use #RRGGBB notation",
            details={"field": "background"},
        )
    return (
        int(text[1:3], 16),
        int(text[3:5], 16),
        int(text[5:7], 16),
    )


def validate_contact_sheet_request(body: Any) -> ContactSheetRequest:
    value = _require_object(body)
    selection = str(value.get("selection", "stored"))
    if selection not in {"stored", "midpoint", "first"}:
        raise ValidationError(
            "selection must be stored, midpoint, or first",
            details={"field": "selection"},
        )
    return ContactSheetRequest(
        sheet_id=validate_identifier(str(value.get("sheet_id", "")), "sheet_id"),
        columns=_integer(value, "columns", 3, 1, 6),
        tile_width=_integer(value, "tile_width", 160, 32, 320),
        gutter=_integer(value, "gutter", 6, 0, 20),
        selection=selection,
        background_rgb=_parse_color(value.get("background", "#20252c")),
        revision=_optional_revision(value),
    )


def _validate_operation(item: Any) -> RevisionOperation:
    if not isinstance(item, dict):
        raise ValidationError(
            "each operation must be an object",
            details={"field": "operations"},
        )
    kind = item.get("type")
    if kind == "split":
        return RevisionOperation(
            kind="split",
            scene_index=_required_integer(item, "scene_index", 0, 1_000_000),
            at_frame=_required_integer(item, "at_frame", 1, 1_000_000_000),
        )
    if kind == "merge":
        operation = RevisionOperation(
            kind="merge",
            from_scene_index=_required_integer(item, "from_scene_index", 0, 1_000_000),
            to_scene_index=_required_integer(item, "to_scene_index", 0, 1_000_000),
        )
        if operation.to_scene_index <= operation.from_scene_index:
            raise ValidationError(
                "merge requires to_scene_index greater than from_scene_index",
                details={"field": "operations"},
            )
        return operation
    raise ValidationError(
        "operation type must be split or merge",
        details={"field": "operations"},
    )


def validate_revision_request(body: Any, max_operations: int) -> RevisionRequest:
    value = _require_object(body)
    submission_id = validate_identifier(
        str(value.get("submission_id", "")),
        "submission_id",
    )
    base_revision = _required_integer(value, "base_revision", 1, 1_000_000)
    raw_operations = value.get("operations")
    if not isinstance(raw_operations, list) or not raw_operations:
        raise ValidationError(
            "operations must be a non-empty list",
            details={"field": "operations"},
        )
    if len(raw_operations) > max_operations:
        raise ValidationError(
            "operations exceed the configured limit",
            details={"field": "operations", "max_operations": max_operations},
        )
    operations = tuple(_validate_operation(item) for item in raw_operations)
    reason = value.get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 200):
        raise ValidationError(
            "reason must be a string of at most 200 characters",
            details={"field": "reason"},
        )
    return RevisionRequest(
        submission_id=submission_id,
        base_revision=base_revision,
        operations=operations,
        reason=reason,
    )

