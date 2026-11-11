from __future__ import annotations

from typing import Any, Sequence

from frame_proxy.domain.errors import (
    ErrorCode,
    NotFoundError,
    ValidationError,
)
from frame_proxy.domain.models import (
    InspectionRecord,
    SceneRecord,
    SceneRevisionRecord,
)
from frame_proxy.domain.policies import RevisionOperation


def resolve_revision(
    record: InspectionRecord,
    revision: int | None = None,
) -> SceneRevisionRecord:
    if not record.revisions:
        raise NotFoundError(
            ErrorCode.REVISION_NOT_FOUND,
            "inspection has no accepted scene revision",
            details={"inspection_id": record.inspection_id},
        )
    target = record.accepted_revision if revision is None else revision
    for candidate in record.revisions:
        if candidate.revision == target:
            return candidate
    raise NotFoundError(
        ErrorCode.REVISION_NOT_FOUND,
        "scene revision was not found",
        details={
            "inspection_id": record.inspection_id,
            "revision": target,
            "accepted_revision": record.accepted_revision,
        },
    )


def validate_scene_coverage(
    scenes: Sequence[SceneRecord],
    *,
    frame_count: int,
) -> None:
    if not scenes:
        raise ValidationError(
            "a revision must contain at least one scene",
            code=ErrorCode.SCENE_COVERAGE_INVALID,
        )
    if scenes[0].start_frame != 0:
        raise ValidationError(
            "the first scene must begin at frame zero",
            code=ErrorCode.SCENE_COVERAGE_INVALID,
            details={"start_frame": scenes[0].start_frame},
        )
    if scenes[-1].end_frame != frame_count:
        raise ValidationError(
            "the final scene must end at the clip frame count",
            code=ErrorCode.SCENE_COVERAGE_INVALID,
            details={
                "end_frame": scenes[-1].end_frame,
                "frame_count": frame_count,
            },
        )
    for scene in scenes:
        if scene.start_frame >= scene.end_frame:
            raise ValidationError(
                "scene ranges must be non-empty and half-open",
                code=ErrorCode.SCENE_COVERAGE_INVALID,
                details={
                    "scene_index": scene.index,
                    "start_frame": scene.start_frame,
                    "end_frame": scene.end_frame,
                },
            )
        if not scene.start_frame <= scene.representative_frame < scene.end_frame:
            raise ValidationError(
                "representative frames must stay inside their scene",
                code=ErrorCode.SCENE_COVERAGE_INVALID,
                details={
                    "scene_index": scene.index,
                    "representative_frame": scene.representative_frame,
                },
            )
    for left, right in zip(scenes, scenes[1:]):
        if left.end_frame != right.start_frame:
            raise ValidationError(
                "scene ranges must be contiguous without gaps or overlaps",
                code=ErrorCode.SCENE_COVERAGE_INVALID,
                details={
                    "left_scene_index": left.index,
                    "left_end_frame": left.end_frame,
                    "right_scene_index": right.index,
                    "right_start_frame": right.start_frame,
                },
            )


def _split_scene(scene: SceneRecord, at_frame: int) -> tuple[SceneRecord, SceneRecord]:
    def representative(start: int, end: int) -> int:
        if start <= scene.representative_frame < end:
            return scene.representative_frame
        return start + (end - start) // 2

    left = SceneRecord(
        index=scene.index,
        start_frame=scene.start_frame,
        end_frame=at_frame,
        representative_frame=representative(scene.start_frame, at_frame),
        mean_luma=scene.mean_luma,
        contrast=scene.contrast,
        dominant_rgb=scene.dominant_rgb,
    )
    right = SceneRecord(
        index=scene.index,
        start_frame=at_frame,
        end_frame=scene.end_frame,
        representative_frame=representative(at_frame, scene.end_frame),
        mean_luma=scene.mean_luma,
        contrast=scene.contrast,
        dominant_rgb=scene.dominant_rgb,
    )
    return left, right


def _merge_scenes(group: Sequence[SceneRecord]) -> SceneRecord:
    total_frames = sum(item.end_frame - item.start_frame for item in group)
    mean_luma = (
        sum(item.mean_luma * (item.end_frame - item.start_frame) for item in group)
        / total_frames
    )
    contrast = (
        sum(item.contrast * (item.end_frame - item.start_frame) for item in group)
        / total_frames
    )
    dominant = tuple(
        round(
            sum(
                item.dominant_rgb[channel] * (item.end_frame - item.start_frame)
                for item in group
            )
            / total_frames
        )
        for channel in range(3)
    )
    longest = max(
        group,
        key=lambda item: (item.end_frame - item.start_frame, -item.index),
    )
    return SceneRecord(
        index=group[0].index,
        start_frame=group[0].start_frame,
        end_frame=group[-1].end_frame,
        representative_frame=longest.representative_frame,
        mean_luma=mean_luma,
        contrast=contrast,
        dominant_rgb=(dominant[0], dominant[1], dominant[2]),
    )


def apply_revision_operations(
    base_scenes: Sequence[SceneRecord],
    operations: Sequence[RevisionOperation],
    *,
    frame_count: int,
) -> list[SceneRecord]:
    scenes = [
        SceneRecord.from_dict(item.as_dict()) for item in base_scenes
    ]
    for operation in operations:
        if operation.kind == "split":
            scene_index = int(operation.scene_index)
            at_frame = int(operation.at_frame)
            if scene_index >= len(scenes):
                raise ValidationError(
                    "split references a scene that does not exist",
                    details={
                        "scene_index": scene_index,
                        "scene_count": len(scenes),
                    },
                )
            scene = scenes[scene_index]
            if not scene.start_frame < at_frame < scene.end_frame:
                raise ValidationError(
                    "split frame must lie strictly inside the scene",
                    details={
                        "scene_index": scene_index,
                        "at_frame": at_frame,
                        "start_frame": scene.start_frame,
                        "end_frame": scene.end_frame,
                    },
                )
            left, right = _split_scene(scene, at_frame)
            scenes[scene_index : scene_index + 1] = [left, right]
        else:
            from_index = int(operation.from_scene_index)
            to_index = int(operation.to_scene_index)
            if to_index >= len(scenes):
                raise ValidationError(
                    "merge references a scene that does not exist",
                    details={
                        "from_scene_index": from_index,
                        "to_scene_index": to_index,
                        "scene_count": len(scenes),
                    },
                )
            merged = _merge_scenes(scenes[from_index : to_index + 1])
            scenes[from_index : to_index + 1] = [merged]

    reindexed = [
        SceneRecord(
            index=index,
            start_frame=item.start_frame,
            end_frame=item.end_frame,
            representative_frame=item.representative_frame,
            mean_luma=item.mean_luma,
            contrast=item.contrast,
            dominant_rgb=item.dominant_rgb,
        )
        for index, item in enumerate(scenes)
    ]
    validate_scene_coverage(reindexed, frame_count=frame_count)
    return reindexed


def revision_summary(revision: SceneRevisionRecord) -> dict[str, Any]:
    value: dict[str, Any] = {
        "revision": revision.revision,
        "kind": revision.kind,
        "operations": [dict(item) for item in revision.operations],
        "scene_count": len(revision.scenes),
        "cut_frames": list(revision.cut_frames),
        "created_at": revision.created_at,
    }
    if revision.base_revision is not None:
        value["base_revision"] = revision.base_revision
    if revision.submission_id is not None:
        value["submission_id"] = revision.submission_id
    if revision.reason is not None:
        value["reason"] = revision.reason
    return value


def revision_detail(revision: SceneRevisionRecord) -> dict[str, Any]:
    value = revision_summary(revision)
    value["scenes"] = [item.as_dict() for item in revision.scenes]
    return value


def inspection_view(
    record: InspectionRecord,
    revision: int | None = None,
) -> dict[str, Any]:
    if record.revisions:
        selected = resolve_revision(record, revision)
        scenes = selected.scenes
        cut_frames = selected.cut_frames
        viewed_revision: int | None = selected.revision
    else:
        if revision is not None:
            resolve_revision(record, revision)
        scenes = record.scenes
        cut_frames = record.cut_frames
        viewed_revision = None
    value: dict[str, Any] = {
        "inspection_id": record.inspection_id,
        "clip_id": record.clip_id,
        "state": record.state,
        "sensitivity": record.sensitivity,
        "sample_stride": record.sample_stride,
        "min_scene_frames": record.min_scene_frames,
        "sampled_frames": list(record.sampled_frames),
        "cut_frames": list(cut_frames),
        "scenes": [item.as_dict() for item in scenes],
        "accepted_revision": record.accepted_revision,
        "revisions": [revision_summary(item) for item in record.revisions],
        "transitions": [item.as_dict() for item in record.transitions],
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    if viewed_revision is not None:
        value["revision"] = viewed_revision
    if record.error_code is not None:
        value["error_code"] = record.error_code
    if record.error_message is not None:
        value["error_message"] = record.error_message
    return value
