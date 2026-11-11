# Frame Proxy Inspector

## Service goal

Frame Proxy Inspector is a Python backend for archive and finishing teams
that need a bounded, deterministic first pass over uncompressed AVI footage.
The service records a clip only after its container and frame geometry are
verified, indexes visible shot changes, and composes a contact sheet from one
representative frame per detected scene.

This service is intentionally not an editor, transcoder, or assembly surface.
It concentrates on inspection artifacts that another stage can
review before expensive media work begins.

Automated tests, test fixtures, and a test command are intentionally deferred.
The downstream task stage will add focused unit, integration, and end-to-end
verification on top of this healthy baseline.

## Users

- Archivists who need a quick visual index of a newly received clip.
- Media pipeline engineers who validate frame layout before derivative work.
- Finishing reviewers who compare scene boundaries with a contact sheet.

## Runtime shape

- Shape: backend.
- Language: Python 3.11 or newer.
- HTTP surface: Python standard modules, using `ThreadingHTTPServer`.
- Persistence: a protected data root containing immutable clip bytes,
  JSON records, and generated PPM sheets.
- External services: none.

## Core entities

### Video clip

A clip is a registered RIFF/AVI asset with one uncompressed 24-bit BGR video
stream. Its record stores the unique clip identifier, SHA-256 digest, frame
count, frame rate ratio, width, height, row stride, and the first frame byte
offset.

Invariants:

- The RIFF `movi` chunk must contain exactly the declared number of frame chunks.
- Frame payload size, row stride, and image dimensions must agree.
- Only bottom-up or top-down uncompressed DIB frames are accepted.
- Width and height stay within the configured decoding limit.
- A failed intake never leaves a visible clip record or partial media.

State machine: `probing -> registered`. A rejected probe does not persist a
state.

### Shot inspection

An inspection belongs to one registered clip and stores the sensitivity,
minimum scene length, sampled frame fingerprints, cut frame numbers, scene
ranges, and representative frames.

Once indexed, an inspection also carries an append-only revision history.
Revision 1 is the immutable automatic detector output. Each accepted reviewer
correction appends a revision built from ordered split and merge operations
and moves the `accepted_revision` pointer to it. The top-level scene ranges
and cut frames always mirror the accepted revision.

Invariants:

- Every scene range is half-open and remains inside the clip frame count.
- Scene ranges are ordered, contiguous, and non-overlapping.
- The first scene always begins at frame zero.
- The final scene ends exactly at the clip frame count.
- Every accepted revision preserves complete clip coverage; a correction can
  never introduce a gap or an overlap.
- A correction is accepted only when its `base_revision` matches the accepted
  revision, so a late submission never silently replaces an accepted one.
- A repeated `submission_id` replays the original revision instead of
  creating a second valid result.
- The automatic result and every older revision remain readable by revision
  number after newer revisions are accepted.
- A failed inspection keeps a stable error code and never exposes partial
  scene data as successful output.

State machine:

`pending -> scanning -> indexed`

`pending -> failed`, `scanning -> failed`. The `indexed` and `failed` states are
terminal. Revisions do not move the state; they version the scene layout of an
`indexed` inspection.

### Contact sheet

A contact sheet belongs to one indexed inspection. It contains a bounded grid
of representative frames, gutter color, exact tile dimensions, a PPM payload,
and a tile map that links every cell to its source scene.

Invariants:

- The rendered grid dimensions and digest match the stored record.
- Each scene appears at most once.
- The record pins the `source_revision` it was composed from; delivery freezes
  the source revision, tile map, and artifact against later corrections.
- Sheets default to the latest accepted revision; an explicit revision request
  composes from that revision instead.
- The request is rejected before composition when its pixel budget is exceeded.
- A failed composition removes temporary output and commits no ready record.

State machine:

`planned -> compositing -> ready`

`planned -> failed`, `compositing -> failed`. The `ready` and `failed` states
are terminal.

## Modules

- `http`: request parsing, identifiers, status mapping, and response encoding.
- `domain`: entity records, limits, errors, and legal state transitions.
- `media`: RIFF/AVI parsing, frame decoding, frame fingerprints, cut detection,
  PPM encoding, and grid composition.
- `service`: workflow orchestration and observable failure behavior.
- `storage`: namespaced files, exclusive creation, state updates, and atomic
  publication.
- `tools`: executable production smoke paths for each workflow.

The HTTP layer depends on service interfaces. Services depend on domain and
media contracts. Storage implements record access and has no dependency on
HTTP.

## Workflows

### 1. Register video clip

Entry point: `PUT /v1/clips/{clip_id}` with an `application/x-avi` body.

Steps:

1. The HTTP boundary accepts only a validated identifier and bounded request
   body.
2. The domain gate checks byte limits and requested metadata stability.
3. The AVI adapter parses RIFF, `avih`, `strh`, `strf`, and frame chunks.
4. The service computes the digest and builds the clip record.
5. Storage acquires the clip key, writes media and metadata through temporary
   paths, and commits both only after the complete payload is durable.

Success returns the immutable clip record. Failure returns a stable validation
error and leaves no visible clip.

### 2. Index shot boundaries

Entry point: `POST /v1/clips/{clip_id}/inspections`.

Steps:

1. The HTTP boundary validates `inspection_id`, sensitivity, sample stride, and
   minimum scene length.
2. The service loads the registered clip and creates a `pending` inspection.
3. The state gate moves the record to `scanning`.
4. The frame pipeline decodes sampled frames and computes luma/color
   fingerprints.
5. The detector selects hard cuts whose distance exceeds the sensitivity
   threshold while honoring the minimum scene length.
6. Scene ranges and representative frames are persisted and the inspection
   moves to `indexed`.

Any decode, range, or persistence error moves the record to `failed` with a
stable code. The automatic result is stored as revision 1 and stays readable
by revision number for the life of the inspection.

### 3. Revise scene boundaries

Entry point: `POST /v1/inspections/{inspection_id}/revisions`.

Steps:

1. The HTTP boundary validates `submission_id`, `base_revision`, and the
   ordered split/merge operations.
2. The service loads the indexed inspection and its clip frame count.
3. Under the inspection lock, the stored record is re-read and the submission
   is matched against the accepted revision.
4. A repeated `submission_id` with identical content replays the original
   revision with HTTP 200; with different content it is rejected with HTTP 409.
5. A `base_revision` that is not the accepted revision is rejected with HTTP
   409 `revision_conflict`.
6. The operations are applied to the base revision scenes and the result is
   validated for complete, contiguous, gap-free clip coverage.
7. The new revision is appended, `accepted_revision` advances, and the record
   is committed atomically in one write.

Success returns HTTP 201 with the new revision and the inspection view pinned
to it. Rejections never mutate the stored record.

### 4. Create contact sheet

Entry point: `POST /v1/inspections/{inspection_id}/contact-sheets`.

Steps:

1. The HTTP boundary validates grid width, tile size, gutter, background, and
   selection policy.
2. The service resolves an `indexed` inspection and pins the requested scene
   revision, defaulting to the latest accepted revision.
3. The job enters `planned`, then `compositing`, with `source_revision` fixed
   on the record before any frame work starts.
4. The media layer decodes only the selected representative frames and resizes
   them into a fixed grid.
5. PPM bytes and a tile map are written through temporary files.
6. The service verifies dimensions and digest, commits the record, and moves
   the job to `ready`.

An invalid budget or frame error moves the job to `failed`; temporary bytes are
removed and no artifact URL becomes available.

## API surface

- `GET /health`
- `PUT /v1/clips/{clip_id}`
- `GET /v1/clips/{clip_id}`
- `POST /v1/clips/{clip_id}/inspections`
- `GET /v1/inspections/{inspection_id}` (latest accepted revision; `?revision=N` pins an older one)
- `POST /v1/inspections/{inspection_id}/revisions`
- `GET /v1/inspections/{inspection_id}/revisions`
- `POST /v1/inspections/{inspection_id}/contact-sheets`
- `GET /v1/contact-sheets/{sheet_id}`
- `GET /v1/contact-sheets/{sheet_id}/artifact`

Errors are JSON objects with `error.code`, `error.message`, and an optional
`error.details` object. Validation errors use HTTP 400, missing records use
HTTP 404, conflicting identifiers and stale revision bases use HTTP 409, and
bounded decoder failures use HTTP 422.

## Validation plan

Each workflow has a bounded production smoke path under `tools/`. The checks
start the real HTTP application on a loopback port, create media through the
production AVI writer, and exercise success plus a representative failure.
They also verify persisted state and output digests. The build command only
compiles the Python source and does not require network access.

Automated tests are deferred to the later task boundary. This baseline keeps
the smoke paths deterministic and local so future tests can replace or extend
them without changing the public workflows.
