from __future__ import annotations

from dataclasses import dataclass

from frame_proxy.config import Settings
from frame_proxy.domain.errors import (
    ConflictError,
    RevisionConflictError,
    StateError,
)
from frame_proxy.domain.models import (
    InspectionRecord,
    SceneRevisionRecord,
    utc_now,
)
from frame_proxy.domain.policies import RevisionRequest, validate_identifier
from frame_proxy.domain.revisions import (
    apply_revision_operations,
    resolve_revision,
)
from frame_proxy.storage.repository import FileRepository


@dataclass(frozen=True, slots=True)
class RevisionSubmission:
    record: InspectionRecord
    revision: SceneRevisionRecord
    created: bool


class RevisionService:
    def __init__(self, repository: FileRepository, settings: Settings) -> None:
        self._repository = repository
        self._settings = settings

    def submit(
        self,
        inspection_id: str,
        request: RevisionRequest,
    ) -> RevisionSubmission:
        normalized_id = validate_identifier(inspection_id, "inspection_id")
        inspection = self._repository.get_inspection(normalized_id)
        clip = self._repository.get_clip(inspection.clip_id)
        operations = [item.as_dict() for item in request.operations]
        outcome: dict[str, object] = {}

        def mutate(record: InspectionRecord) -> None:
            if record.state != "indexed":
                raise StateError(
                    "scene revisions require an indexed inspection",
                    details={"state": record.state},
                )
            for existing in record.revisions:
                if (
                    existing.submission_id is not None
                    and existing.submission_id == request.submission_id
                ):
                    if (
                        existing.base_revision == request.base_revision
                        and existing.operations == operations
                    ):
                        outcome["revision"] = existing
                        outcome["created"] = False
                        return
                    raise ConflictError(
                        "submission identifier was already used with different content",
                        details={"submission_id": request.submission_id},
                    )
            if request.base_revision != record.accepted_revision:
                raise RevisionConflictError(
                    accepted_revision=record.accepted_revision,
                    base_revision=request.base_revision,
                )
            base = resolve_revision(record, request.base_revision)
            new_scenes = apply_revision_operations(
                base.scenes,
                request.operations,
                frame_count=clip.frame_count,
            )
            cut_frames = [scene.start_frame for scene in new_scenes[1:]]
            revision = SceneRevisionRecord(
                revision=record.accepted_revision + 1,
                kind="correction",
                scenes=new_scenes,
                cut_frames=cut_frames,
                base_revision=request.base_revision,
                operations=operations,
                submission_id=request.submission_id,
                reason=request.reason,
                created_at=utc_now(),
            )
            record.revisions.append(revision)
            record.accepted_revision = revision.revision
            record.scenes = list(new_scenes)
            record.cut_frames = list(cut_frames)
            record.updated_at = utc_now()
            outcome["revision"] = revision
            outcome["created"] = True

        record = self._repository.mutate_inspection(normalized_id, mutate)
        return RevisionSubmission(
            record=record,
            revision=outcome["revision"],
            created=bool(outcome["created"]),
        )
