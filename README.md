# Frame Proxy Inspector

Frame Proxy Inspector is a small Python backend for turning uncompressed AVI
footage into two review artifacts:

- a deterministic shot index with contiguous scene ranges;
- a bounded PPM contact sheet with one tile per indexed scene.

The service verifies the RIFF/AVI container, frame geometry, and frame chunk
count before it admits a clip. Shot indexing then computes luma and color
fingerprints from sampled frames and applies a minimum-scene guard to hard
cuts. Contact sheet requests are checked against a fixed pixel budget before
frame decoding starts.

Reviewers can correct the automatic segmentation with traceable scene
revisions. Every accepted correction is appended as a new revision built from
split and merge operations; the original automatic result stays readable as
revision 1, and delivered contact sheets keep the revision they were composed
from.

Automated unit, integration, and browser tests are intentionally deferred to a
later task stage. This baseline ships executable workflow checks instead.

## Requirements

- Python 3.11 or newer
- No third-party packages
- No external media service

## Start the service

```bash
export PYTHONPATH=src
python3 -m frame_proxy serve --host 127.0.0.1 --port 8765
```

The service prints its bound URL. Press `Ctrl+C` to stop it.

## Generate demonstration footage

```bash
PYTHONPATH=src python3 -m frame_proxy demo-avi \
  --output demo/scene-walk.avi \
  --width 128 \
  --height 72 \
  --fps 12
```

The generated file is an uncompressed 24-bit AVI with four color scenes and
17 total frames. It is suitable for exercising all three workflows.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | service check |
| `PUT` | `/v1/clips/{clip_id}` | admit an `application/x-avi` payload |
| `GET` | `/v1/clips/{clip_id}` | read clip geometry and digest |
| `POST` | `/v1/clips/{clip_id}/inspections` | index shot boundaries |
| `GET` | `/v1/inspections/{inspection_id}` | read the accepted revision (pin with `?revision=N`) |
| `POST` | `/v1/inspections/{inspection_id}/revisions` | submit a split/merge correction |
| `GET` | `/v1/inspections/{inspection_id}/revisions` | list the revision history |
| `POST` | `/v1/inspections/{inspection_id}/contact-sheets` | compose a PPM grid |
| `GET` | `/v1/contact-sheets/{sheet_id}` | read the tile map and digest |
| `GET` | `/v1/contact-sheets/{sheet_id}/artifact` | fetch the PPM bytes |

Identifiers use lowercase letters, digits, `_`, and `-`, begin with a letter,
and contain 3 to 48 characters.

Example inspection request:

```json
{
  "inspection_id": "inspection-demo-a",
  "sensitivity": 0.55,
  "sample_stride": 1,
  "min_scene_frames": 2
}
```

Example contact sheet request:

```json
{
  "sheet_id": "sheet-demo-a",
  "columns": 3,
  "tile_width": 96,
  "gutter": 4,
  "selection": "stored",
  "background": "#20252c"
}
```

`selection` may be `stored`, `midpoint`, or `first`. The rendered PPM is
binary P6 with a maximum value of 255.

## Scene revisions

An indexed inspection keeps every accepted scene layout as a numbered
revision. Revision 1 is the automatic detector output and never changes.
Corrections append new revisions built from ordered `split` and `merge`
operations:

```json
{
  "submission_id": "submission-review-01",
  "base_revision": 1,
  "reason": "split the panning shot and merge the tail",
  "operations": [
    {"type": "split", "scene_index": 1, "at_frame": 7},
    {"type": "merge", "from_scene_index": 3, "to_scene_index": 4}
  ]
}
```

Operation indices refer to the scene list as the previous operations left it.
Every accepted revision must keep complete, gap-free coverage of the clip:
the first scene starts at frame zero, ranges are contiguous half-open
intervals, and the final scene ends at the clip frame count.

Concurrency and retry rules:

- `base_revision` must match the currently accepted revision. A submission
  based on a stale revision is rejected with `409 revision_conflict` so a
  later correction never silently replaces an accepted one; re-read the
  inspection and resubmit against the new `accepted_revision`.
- `submission_id` is the idempotency key. Repeating the same submission
  returns `200` with the original revision instead of creating a second one;
  reusing the identifier with different content returns `409 conflict`.

Read defaults and pinning:

- `GET /v1/inspections/{id}` returns the latest accepted revision. The
  response carries `accepted_revision`, the viewed `revision`, and a compact
  `revisions` history.
- `GET /v1/inspections/{id}?revision=N` reads the scenes exactly as accepted
  in revision `N`, including the original automatic result at revision 1.
- Contact sheets default to the latest accepted revision and record it as
  `source_revision`; pass `"revision": N` in the request to compose from an
  older revision. A delivered sheet keeps its `source_revision`, tile map,
  and artifact regardless of later corrections.

## Data layout

`FRAME_PROXY_DATA_DIR` selects the data root. Its default is
`.frame-proxy-data` in the current working directory.

```text
.frame-proxy-data/
├── clips/<clip_id>/
│   ├── clip.avi
│   └── record.json
├── inspections/<inspection_id>.json
├── contact-sheets/<sheet_id>/
│   ├── record.json
│   └── sheet.ppm
└── locks/
```

Records are written through temporary files and renamed into place. Contact
sheet bytes and their ready record are verified before the record becomes
visible. Inspection records embed their full revision history, so accepting a
correction is a single atomic record update guarded by the inspection lock.

## Source layout

```text
src/frame_proxy/
├── domain/      records, request rules, and state transitions
├── http/        request routing and response mapping
├── media/       AVI parsing, frame access, fingerprints, and PPM grids
├── service/     workflow orchestration and failure handling
└── storage/     namespaced files, locks, and atomic writes

tools/
├── harness.py
├── check_clip_intake.py
├── check_shot_index.py
├── check_contact_sheet.py
└── check_scene_revision.py
```

## Build and workflow checks

Build:

```bash
python3 -m compileall -q src tools
```

Run one workflow check at a time:

```bash
PYTHONPATH=src:. python3 tools/check_clip_intake.py
PYTHONPATH=src:. python3 tools/check_shot_index.py
PYTHONPATH=src:. python3 tools/check_contact_sheet.py
PYTHONPATH=src:. python3 tools/check_scene_revision.py
```

Each check starts a real loopback HTTP server, generates media through the
production AVI writer, completes the named workflow, and verifies persisted
state or artifact bytes. Temporary records are removed when the check exits.

## Environment variables

| Name | Default | Purpose |
| --- | --- | --- |
| `FRAME_PROXY_DATA_DIR` | `.frame-proxy-data` | durable data root |

Clip, upload, JSON, frame dimension, frame count, contact sheet, and revision
operation limits are declared in `src/frame_proxy/config.py`.

## Input boundary

The current adapter accepts one uncompressed 24-bit BGR video stream in a
RIFF/AVI container. Audio streams, compressed video, and additional container
formats are intentionally outside this baseline. The extension seams for those
capabilities are recorded in `.task-source.json`.

