from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(slots=True)
class Transition:
    from_state: str
    to_state: str
    at: str
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["from"] = value.pop("from_state")
        value["to"] = value.pop("to_state")
        if value["reason"] is None:
            value.pop("reason")
        return value


@dataclass(slots=True)
class ClipRecord:
    clip_id: str
    state: str
    digest: str
    byte_size: int
    width: int
    height: int
    frame_count: int
    fps_numerator: int
    fps_denominator: int
    row_stride: int
    frame_payload_size: int
    top_down: bool
    frame_offsets: list[int]
    handler: str
    transitions: list[Transition] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "clip_id": self.clip_id,
            "state": self.state,
            "digest": self.digest,
            "byte_size": self.byte_size,
            "width": self.width,
            "height": self.height,
            "frame_count": self.frame_count,
            "fps": {
                "numerator": self.fps_numerator,
                "denominator": self.fps_denominator,
            },
            "row_stride": self.row_stride,
            "frame_payload_size": self.frame_payload_size,
            "top_down": self.top_down,
            "frame_offsets": list(self.frame_offsets),
            "handler": self.handler,
            "transitions": [item.as_dict() for item in self.transitions],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ClipRecord":
        fps = data.get("fps") or {}
        transitions = [
            Transition(
                from_state=str(item.get("from", "")),
                to_state=str(item.get("to", "")),
                at=str(item.get("at", "")),
                reason=item.get("reason"),
            )
            for item in data.get("transitions", [])
            if isinstance(item, dict)
        ]
        return cls(
            clip_id=str(data["clip_id"]),
            state=str(data["state"]),
            digest=str(data["digest"]),
            byte_size=int(data["byte_size"]),
            width=int(data["width"]),
            height=int(data["height"]),
            frame_count=int(data["frame_count"]),
            fps_numerator=int(fps["numerator"]),
            fps_denominator=int(fps["denominator"]),
            row_stride=int(data["row_stride"]),
            frame_payload_size=int(data["frame_payload_size"]),
            top_down=bool(data["top_down"]),
            frame_offsets=[int(value) for value in data.get("frame_offsets", [])],
            handler=str(data["handler"]),
            transitions=transitions,
            created_at=str(data["created_at"]),
        )


@dataclass(slots=True)
class SceneRecord:
    index: int
    start_frame: int
    end_frame: int
    representative_frame: int
    mean_luma: float
    contrast: float
    dominant_rgb: tuple[int, int, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "representative_frame": self.representative_frame,
            "mean_luma": round(self.mean_luma, 4),
            "contrast": round(self.contrast, 4),
            "dominant_rgb": list(self.dominant_rgb),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SceneRecord":
        rgb = tuple(int(value) for value in data["dominant_rgb"])
        return cls(
            index=int(data["index"]),
            start_frame=int(data["start_frame"]),
            end_frame=int(data["end_frame"]),
            representative_frame=int(data["representative_frame"]),
            mean_luma=float(data["mean_luma"]),
            contrast=float(data["contrast"]),
            dominant_rgb=(rgb[0], rgb[1], rgb[2]),
        )


@dataclass(slots=True)
class SceneRevisionRecord:
    revision: int
    kind: str
    scenes: list[SceneRecord]
    cut_frames: list[int]
    base_revision: int | None
    operations: list[dict[str, Any]]
    submission_id: str | None
    reason: str | None
    created_at: str = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "revision": self.revision,
            "kind": self.kind,
            "scenes": [item.as_dict() for item in self.scenes],
            "cut_frames": list(self.cut_frames),
            "operations": [dict(item) for item in self.operations],
            "created_at": self.created_at,
        }
        if self.base_revision is not None:
            value["base_revision"] = self.base_revision
        if self.submission_id is not None:
            value["submission_id"] = self.submission_id
        if self.reason is not None:
            value["reason"] = self.reason
        return value

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SceneRevisionRecord":
        return cls(
            revision=int(data["revision"]),
            kind=str(data["kind"]),
            scenes=[
                SceneRecord.from_dict(item)
                for item in data.get("scenes", [])
                if isinstance(item, dict)
            ],
            cut_frames=[int(value) for value in data.get("cut_frames", [])],
            base_revision=(
                int(data["base_revision"])
                if data.get("base_revision") is not None
                else None
            ),
            operations=[
                dict(item)
                for item in data.get("operations", [])
                if isinstance(item, dict)
            ],
            submission_id=data.get("submission_id"),
            reason=data.get("reason"),
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(slots=True)
class InspectionRecord:
    inspection_id: str
    clip_id: str
    state: str
    sensitivity: float
    sample_stride: int
    min_scene_frames: int
    sampled_frames: list[int] = field(default_factory=list)
    cut_frames: list[int] = field(default_factory=list)
    scenes: list[SceneRecord] = field(default_factory=list)
    accepted_revision: int = 0
    revisions: list[SceneRevisionRecord] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "inspection_id": self.inspection_id,
            "clip_id": self.clip_id,
            "state": self.state,
            "sensitivity": self.sensitivity,
            "sample_stride": self.sample_stride,
            "min_scene_frames": self.min_scene_frames,
            "sampled_frames": list(self.sampled_frames),
            "cut_frames": list(self.cut_frames),
            "scenes": [item.as_dict() for item in self.scenes],
            "accepted_revision": self.accepted_revision,
            "revisions": [item.as_dict() for item in self.revisions],
            "transitions": [item.as_dict() for item in self.transitions],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.error_code is not None:
            value["error_code"] = self.error_code
        if self.error_message is not None:
            value["error_message"] = self.error_message
        return value

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InspectionRecord":
        transitions = [
            Transition(
                from_state=str(item.get("from", "")),
                to_state=str(item.get("to", "")),
                at=str(item.get("at", "")),
                reason=item.get("reason"),
            )
            for item in data.get("transitions", [])
            if isinstance(item, dict)
        ]
        scenes = [
            SceneRecord.from_dict(item)
            for item in data.get("scenes", [])
            if isinstance(item, dict)
        ]
        cut_frames = [int(value) for value in data.get("cut_frames", [])]
        revisions = [
            SceneRevisionRecord.from_dict(item)
            for item in data.get("revisions", [])
            if isinstance(item, dict)
        ]
        accepted_revision = int(data.get("accepted_revision", 0))
        state = str(data["state"])
        created_at = str(data["created_at"])
        if state == "indexed" and scenes and not revisions:
            # Records written before revision tracking existed keep their
            # original automatic scenes readable as revision 1.
            revisions = [
                SceneRevisionRecord(
                    revision=1,
                    kind="automatic",
                    scenes=[SceneRecord.from_dict(item.as_dict()) for item in scenes],
                    cut_frames=list(cut_frames),
                    base_revision=None,
                    operations=[],
                    submission_id=None,
                    reason="automatic shot detection",
                    created_at=created_at,
                )
            ]
            accepted_revision = 1
        return cls(
            inspection_id=str(data["inspection_id"]),
            clip_id=str(data["clip_id"]),
            state=state,
            sensitivity=float(data["sensitivity"]),
            sample_stride=int(data["sample_stride"]),
            min_scene_frames=int(data["min_scene_frames"]),
            sampled_frames=[int(value) for value in data.get("sampled_frames", [])],
            cut_frames=cut_frames,
            scenes=scenes,
            accepted_revision=accepted_revision,
            revisions=revisions,
            transitions=transitions,
            error_code=data.get("error_code"),
            error_message=data.get("error_message"),
            created_at=created_at,
            updated_at=str(data["updated_at"]),
        )


@dataclass(slots=True)
class ContactSheetRecord:
    sheet_id: str
    inspection_id: str
    state: str
    columns: int
    tile_width: int
    tile_height: int
    gutter: int
    background_rgb: tuple[int, int, int]
    selection: str
    rows: int
    image_width: int
    image_height: int
    artifact_name: str
    artifact_digest: str | None
    tile_map: list[dict[str, int]]
    source_revision: int = 1
    transitions: list[Transition] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "sheet_id": self.sheet_id,
            "inspection_id": self.inspection_id,
            "state": self.state,
            "columns": self.columns,
            "tile_width": self.tile_width,
            "tile_height": self.tile_height,
            "gutter": self.gutter,
            "background_rgb": list(self.background_rgb),
            "selection": self.selection,
            "rows": self.rows,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "artifact_name": self.artifact_name,
            "artifact_digest": self.artifact_digest,
            "tile_map": list(self.tile_map),
            "source_revision": self.source_revision,
            "transitions": [item.as_dict() for item in self.transitions],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.error_code is not None:
            value["error_code"] = self.error_code
        if self.error_message is not None:
            value["error_message"] = self.error_message
        return value

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContactSheetRecord":
        transitions = [
            Transition(
                from_state=str(item.get("from", "")),
                to_state=str(item.get("to", "")),
                at=str(item.get("at", "")),
                reason=item.get("reason"),
            )
            for item in data.get("transitions", [])
            if isinstance(item, dict)
        ]
        background = tuple(int(value) for value in data["background_rgb"])
        return cls(
            sheet_id=str(data["sheet_id"]),
            inspection_id=str(data["inspection_id"]),
            state=str(data["state"]),
            columns=int(data["columns"]),
            tile_width=int(data["tile_width"]),
            tile_height=int(data["tile_height"]),
            gutter=int(data["gutter"]),
            background_rgb=(background[0], background[1], background[2]),
            selection=str(data["selection"]),
            rows=int(data["rows"]),
            image_width=int(data["image_width"]),
            image_height=int(data["image_height"]),
            artifact_name=str(data["artifact_name"]),
            artifact_digest=data.get("artifact_digest"),
            tile_map=[
                {key: int(value) for key, value in item.items()}
                for item in data.get("tile_map", [])
                if isinstance(item, dict)
            ],
            source_revision=int(data.get("source_revision", 1)),
            transitions=transitions,
            error_code=data.get("error_code"),
            error_message=data.get("error_message"),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
        )

