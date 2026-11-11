from __future__ import annotations

import hashlib
import threading
from typing import Any

from tools.harness import RunningService, require, require_status, success


def require_coverage(scenes: list[dict[str, Any]], frame_count: int, label: str) -> None:
    require(bool(scenes), f"{label}: revision must contain scenes")
    require(
        scenes[0]["start_frame"] == 0,
        f"{label}: first scene must start at frame zero",
    )
    require(
        scenes[-1]["end_frame"] == frame_count,
        f"{label}: final scene must end at the clip frame count",
    )
    for index, scene in enumerate(scenes):
        require(scene["index"] == index, f"{label}: scene indices must be sequential")
        require(
            scene["start_frame"] < scene["end_frame"],
            f"{label}: scene ranges must be non-empty",
        )
        require(
            scene["start_frame"] <= scene["representative_frame"] < scene["end_frame"],
            f"{label}: representative frames must stay inside their scene",
        )
    for left, right in zip(scenes, scenes[1:]):
        require(
            left["end_frame"] == right["start_frame"],
            f"{label}: scene coverage has a gap or overlap",
        )


def scene_ranges(scenes: list[dict[str, Any]]) -> list[tuple[int, int]]:
    return [(item["start_frame"], item["end_frame"]) for item in scenes]


def post_revision(
    service: RunningService,
    inspection_id: str,
    body: dict[str, Any],
) -> Any:
    return service.request_json(
        "POST",
        f"/v1/inspections/{inspection_id}/revisions",
        body,
    )


def run_concurrent(
    service: RunningService,
    inspection_id: str,
    payloads: list[dict[str, Any]],
) -> list[tuple[int, dict[str, Any]]]:
    results: list[tuple[int, dict[str, Any]] | None] = [None] * len(payloads)

    def worker(index: int, body: dict[str, Any]) -> None:
        response = post_revision(service, inspection_id, body)
        results[index] = (response.status, response.json())

    threads = [
        threading.Thread(target=worker, args=(index, body))
        for index, body in enumerate(payloads)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return [item for item in results if item is not None]


def main() -> int:
    with RunningService() as service:
        inspection = service.prepare_inspection(
            clip_id="clip-revision-a",
            inspection_id="inspection-revision-a",
        )
        require(inspection["accepted_revision"] == 1, "automatic result must be revision 1")
        require(inspection["revision"] == 1, "default view must show the accepted revision")
        require(len(inspection["revisions"]) == 1, "one automatic revision expected")
        require(
            inspection["revisions"][0]["kind"] == "automatic",
            "the first revision must be the automatic result",
        )

        read = service.request("GET", "/v1/inspections/inspection-revision-a")
        require_status(read, 200, "default inspection lookup")
        default_view = read.json()["inspection"]
        require(default_view["revision"] == 1, "default view must use the latest accepted revision")
        require(len(default_view["scenes"]) == 4, "unexpected automatic scene count")

        before = service.request_json(
            "POST",
            "/v1/inspections/inspection-revision-a/contact-sheets",
            {
                "sheet_id": "sheet-revision-before",
                "columns": 2,
                "tile_width": 96,
                "gutter": 4,
                "selection": "stored",
                "background": "#20252c",
            },
        )
        require_status(before, 201, "pre-revision contact sheet")
        before_record = before.json()["contact_sheet"]
        require(
            before_record["source_revision"] == 1,
            "default sheet must pin the accepted revision",
        )
        require(len(before_record["tile_map"]) == 4, "pre-revision tile map is incomplete")

        correction = {
            "submission_id": "submission-a-1",
            "base_revision": 1,
            "reason": "split scene two and merge the tail scenes",
            "operations": [
                {"type": "split", "scene_index": 1, "at_frame": 7},
                {"type": "merge", "from_scene_index": 3, "to_scene_index": 4},
            ],
        }
        created = post_revision(service, "inspection-revision-a", correction)
        require_status(created, 201, "correction submission")
        created_body = created.json()
        revision_two = created_body["revision"]
        require(revision_two["revision"] == 2, "correction must become revision 2")
        require(revision_two["kind"] == "correction", "correction kind is wrong")
        require(revision_two["base_revision"] == 1, "correction base is wrong")
        require(
            revision_two["submission_id"] == "submission-a-1",
            "correction submission id is missing",
        )
        require(
            scene_ranges(revision_two["scenes"]) == [(0, 5), (5, 7), (7, 9), (9, 17)],
            f"unexpected corrected scenes: {scene_ranges(revision_two['scenes'])}",
        )
        require_coverage(revision_two["scenes"], 17, "revision 2")
        require(
            created_body["inspection"]["accepted_revision"] == 2,
            "accepted revision did not move to the correction",
        )
        require(
            created_body["inspection"]["revision"] == 2,
            "submission response must show the new revision",
        )

        replay = post_revision(service, "inspection-revision-a", correction)
        require_status(replay, 200, "idempotent replay")
        replay_body = replay.json()
        require(
            replay_body["revision"]["revision"] == 2,
            "replay must return the original revision",
        )
        require(
            replay_body["inspection"]["accepted_revision"] == 2,
            "replay must not create another accepted revision",
        )
        history = service.request("GET", "/v1/inspections/inspection-revision-a/revisions")
        require_status(history, 200, "revision history lookup")
        require(
            len(history.json()["revisions"]) == 2,
            "duplicate submission created a second revision",
        )

        stale = post_revision(
            service,
            "inspection-revision-a",
            {
                "submission_id": "submission-b-1",
                "base_revision": 1,
                "operations": [{"type": "split", "scene_index": 0, "at_frame": 2}],
            },
        )
        require_status(stale, 409, "stale base revision rejection")
        stale_error = stale.json()["error"]
        require(
            stale_error["code"] == "revision_conflict",
            f"stale base returned the wrong error code: {stale_error['code']}",
        )
        require(
            stale_error["details"]["accepted_revision"] == 2,
            "conflict details must report the accepted revision",
        )
        after_stale = service.request("GET", "/v1/inspections/inspection-revision-a")
        require(
            after_stale.json()["inspection"]["accepted_revision"] == 2,
            "stale submission silently replaced the accepted revision",
        )

        rebased = post_revision(
            service,
            "inspection-revision-a",
            {
                "submission_id": "submission-b-1",
                "base_revision": 2,
                "operations": [{"type": "split", "scene_index": 0, "at_frame": 2}],
            },
        )
        require_status(rebased, 201, "rebased correction")
        require(
            rebased.json()["revision"]["revision"] == 3,
            "rebased correction must become revision 3",
        )
        latest = service.request("GET", "/v1/inspections/inspection-revision-a")
        latest_view = latest.json()["inspection"]
        require(latest_view["accepted_revision"] == 3, "accepted revision must be 3")
        require(latest_view["revision"] == 3, "default view must show revision 3")
        require(
            scene_ranges(latest_view["scenes"])
            == [(0, 2), (2, 5), (5, 7), (7, 9), (9, 17)],
            "revision 3 scenes are wrong",
        )
        require_coverage(latest_view["scenes"], 17, "revision 3")

        reused = post_revision(
            service,
            "inspection-revision-a",
            {
                "submission_id": "submission-a-1",
                "base_revision": 3,
                "operations": [{"type": "merge", "from_scene_index": 0, "to_scene_index": 1}],
            },
        )
        require_status(reused, 409, "submission id reuse with different content")
        require(
            reused.json()["error"]["code"] == "conflict",
            "submission id reuse returned the wrong error code",
        )

        expected_ranges = {
            1: [(0, 5), (5, 9), (9, 14), (14, 17)],
            2: [(0, 5), (5, 7), (7, 9), (9, 17)],
            3: [(0, 2), (2, 5), (5, 7), (7, 9), (9, 17)],
        }
        for number, ranges in expected_ranges.items():
            pinned = service.request(
                "GET",
                f"/v1/inspections/inspection-revision-a?revision={number}",
            )
            require_status(pinned, 200, f"pinned read of revision {number}")
            view = pinned.json()["inspection"]
            require(view["revision"] == number, f"pinned read {number} shows the wrong revision")
            require(view["accepted_revision"] == 3, "accepted revision must stay at 3")
            require(
                scene_ranges(view["scenes"]) == ranges,
                f"pinned read {number} returned wrong scenes",
            )
            require_coverage(view["scenes"], 17, f"revision {number}")

        missing_revision = service.request(
            "GET",
            "/v1/inspections/inspection-revision-a?revision=99",
        )
        require_status(missing_revision, 404, "unknown revision rejection")
        require(
            missing_revision.json()["error"]["code"] == "revision_not_found",
            "unknown revision returned the wrong error code",
        )
        invalid_revision = service.request(
            "GET",
            "/v1/inspections/inspection-revision-a?revision=abc",
        )
        require_status(invalid_revision, 400, "malformed revision rejection")

        history = service.request("GET", "/v1/inspections/inspection-revision-a/revisions")
        history_body = history.json()
        require(history_body["accepted_revision"] == 3, "history accepted revision is wrong")
        entries = history_body["revisions"]
        require(len(entries) == 3, "history must list three revisions")
        require(
            [item["kind"] for item in entries] == ["automatic", "correction", "correction"],
            "history kinds are wrong",
        )
        require(
            [item.get("base_revision") for item in entries] == [None, 1, 2],
            "history base revisions are wrong",
        )
        require(
            [item.get("submission_id") for item in entries]
            == [None, "submission-a-1", "submission-b-1"],
            "history submission ids are wrong",
        )
        require(
            entries[1]["operations"]
            == [
                {"type": "split", "scene_index": 1, "at_frame": 7},
                {"type": "merge", "from_scene_index": 3, "to_scene_index": 4},
            ],
            "history must record the submitted operations",
        )

        after = service.request_json(
            "POST",
            "/v1/inspections/inspection-revision-a/contact-sheets",
            {
                "sheet_id": "sheet-revision-after",
                "columns": 3,
                "tile_width": 96,
                "gutter": 4,
                "selection": "stored",
                "background": "#20252c",
            },
        )
        require_status(after, 201, "post-revision contact sheet")
        after_record = after.json()["contact_sheet"]
        require(
            after_record["source_revision"] == 3,
            "new sheet must pin the latest accepted revision",
        )
        require(
            after_record["source_revision"] == latest_view["revision"],
            "queried scenes and new sheet source must share the accepted revision",
        )
        require(len(after_record["tile_map"]) == 5, "post-revision tile map is incomplete")

        pinned_sheet = service.request_json(
            "POST",
            "/v1/inspections/inspection-revision-a/contact-sheets",
            {
                "sheet_id": "sheet-revision-pinned",
                "columns": 2,
                "tile_width": 96,
                "gutter": 4,
                "selection": "stored",
                "background": "#20252c",
                "revision": 1,
            },
        )
        require_status(pinned_sheet, 201, "pinned contact sheet")
        require(
            pinned_sheet.json()["contact_sheet"]["source_revision"] == 1,
            "pinned sheet must record its requested revision",
        )
        require(
            len(pinned_sheet.json()["contact_sheet"]["tile_map"]) == 4,
            "pinned sheet must use the pinned revision scenes",
        )

        missing_sheet = service.request_json(
            "POST",
            "/v1/inspections/inspection-revision-a/contact-sheets",
            {
                "sheet_id": "sheet-revision-missing",
                "columns": 2,
                "tile_width": 96,
                "gutter": 4,
                "selection": "stored",
                "background": "#20252c",
                "revision": 42,
            },
        )
        require_status(missing_sheet, 404, "sheet pinned to a missing revision")
        require(
            missing_sheet.json()["error"]["code"] == "revision_not_found",
            "missing sheet revision returned the wrong error code",
        )
        require_status(
            service.request("GET", "/v1/contact-sheets/sheet-revision-missing"),
            404,
            "rejected pinned sheet must not persist",
        )

        before_reread = service.request("GET", "/v1/contact-sheets/sheet-revision-before")
        require_status(before_reread, 200, "delivered sheet lookup")
        delivered = before_reread.json()["contact_sheet"]
        require(
            delivered["source_revision"] == 1,
            "delivered sheet source revision changed after corrections",
        )
        require(
            delivered["artifact_digest"] == before_record["artifact_digest"],
            "delivered sheet artifact changed after corrections",
        )
        artifact = service.request("GET", "/v1/contact-sheets/sheet-revision-before/artifact")
        require_status(artifact, 200, "delivered sheet artifact fetch")
        require(
            hashlib.sha256(artifact.body).hexdigest() == delivered["artifact_digest"],
            "delivered sheet artifact digest mismatch",
        )

        invalid_submissions = [
            (
                {
                    "submission_id": "submission-c-1",
                    "base_revision": 3,
                    "operations": [{"type": "split", "scene_index": 0, "at_frame": 9}],
                },
                "split outside the scene",
            ),
            (
                {
                    "submission_id": "submission-c-2",
                    "base_revision": 3,
                    "operations": [],
                },
                "empty operations",
            ),
            (
                {
                    "submission_id": "submission-c-3",
                    "base_revision": 3,
                    "operations": [
                        {"type": "merge", "from_scene_index": 1, "to_scene_index": 1}
                    ],
                },
                "single scene merge",
            ),
            (
                {
                    "submission_id": "submission-c-4",
                    "base_revision": 3,
                    "operations": [{"type": "noop", "scene_index": 0}],
                },
                "unknown operation type",
            ),
            (
                {
                    "base_revision": 3,
                    "operations": [{"type": "split", "scene_index": 0, "at_frame": 1}],
                },
                "missing submission id",
            ),
            (
                {
                    "submission_id": "submission-c-5",
                    "operations": [{"type": "split", "scene_index": 0, "at_frame": 1}],
                },
                "missing base revision",
            ),
        ]
        for body, label in invalid_submissions:
            rejected = post_revision(service, "inspection-revision-a", body)
            require_status(rejected, 400, f"invalid submission: {label}")
        final_view = service.request("GET", "/v1/inspections/inspection-revision-a")
        require(
            final_view.json()["inspection"]["accepted_revision"] == 3,
            "invalid submissions changed the accepted revision",
        )
        require(
            len(final_view.json()["inspection"]["revisions"]) == 3,
            "invalid submissions added revisions",
        )

        unknown = post_revision(
            service,
            "inspection-revision-unknown",
            {
                "submission_id": "submission-c-6",
                "base_revision": 1,
                "operations": [{"type": "split", "scene_index": 0, "at_frame": 1}],
            },
        )
        require_status(unknown, 404, "revision on a missing inspection")

        service.prepare_inspection(
            clip_id="clip-revision-b",
            inspection_id="inspection-revision-b",
        )
        racing = run_concurrent(
            service,
            "inspection-revision-b",
            [
                {
                    "submission_id": "submission-race-1",
                    "base_revision": 1,
                    "operations": [{"type": "split", "scene_index": 0, "at_frame": 2}],
                },
                {
                    "submission_id": "submission-race-2",
                    "base_revision": 1,
                    "operations": [{"type": "split", "scene_index": 1, "at_frame": 6}],
                },
            ],
        )
        statuses = sorted(status for status, _ in racing)
        require(
            statuses == [201, 409],
            f"concurrent stale submissions must yield one 201 and one 409, got {statuses}",
        )
        conflict_body = next(body for status, body in racing if status == 409)
        require(
            conflict_body["error"]["code"] == "revision_conflict",
            "losing submission must report a revision conflict",
        )
        raced = service.request("GET", "/v1/inspections/inspection-revision-b")
        raced_view = raced.json()["inspection"]
        require(
            raced_view["accepted_revision"] == 2,
            "concurrent submissions must accept exactly one revision",
        )
        require(
            len(raced_view["revisions"]) == 2,
            "concurrent submissions must not create two valid revisions",
        )
        require_coverage(raced_view["scenes"], 17, "raced revision")

        duplicate_payload = {
            "submission_id": "submission-race-3",
            "base_revision": 2,
            "operations": [{"type": "merge", "from_scene_index": 0, "to_scene_index": 1}],
        }
        duplicated = run_concurrent(
            service,
            "inspection-revision-b",
            [dict(duplicate_payload), dict(duplicate_payload)],
        )
        duplicate_statuses = sorted(status for status, _ in duplicated)
        require(
            duplicate_statuses == [200, 201],
            "concurrent duplicates must yield one 201 and one 200 replay",
        )
        duplicate_revisions = {body["revision"]["revision"] for _, body in duplicated}
        require(
            duplicate_revisions == {3},
            "concurrent duplicates must reference the same revision",
        )
        settled = service.request("GET", "/v1/inspections/inspection-revision-b")
        settled_view = settled.json()["inspection"]
        require(
            settled_view["accepted_revision"] == 3,
            "duplicate submissions must accept exactly one new revision",
        )
        require(
            len(settled_view["revisions"]) == 3,
            "duplicate submissions produced two valid results",
        )
        require_coverage(settled_view["scenes"], 17, "settled revision")

    success(
        "revise-scene-boundaries",
        {
            "accepted_revision": settled_view["accepted_revision"],
            "revisions": len(settled_view["revisions"]),
            "stale_status": stale.status,
            "replay_status": replay.status,
            "sheet_source_revision": after_record["source_revision"],
            "delivered_source_revision": delivered["source_revision"],
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
