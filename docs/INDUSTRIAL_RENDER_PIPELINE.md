# Industrial Render Pipeline

## Goal

Turn the current ComfyUI integration into a production-safe render lane for comic drama shots.

## Production Policy

- Only pipelines with `ready == true` may be activated for production renders
- Experimental pipelines may be inspected in UI, but not switched into active state without override
- Every render task must finish in one of these states:
  - `completed`
  - `workflow_error`
  - `transport_error`
  - `timeout`
  - `failed_validation`

## Shot Contract

The canonical render input is `shot.v1`:

- `scene_id`
- `shot_id`
- `location`
- `mood`
- `narration`
- `style_guide`
- `scene_asset`
- `camera`
- `lighting`
- `characters`
- `dialogue`
- `references`

This schema is stored in `render_payload` and then adapted per pipeline.

## Pipeline Mapping Strategy

### Pipeline C

- Engine: `Animagine XL 3.1 + AnimateDiff`
- Mapping:
  - shot text fields -> positive prompt
  - negative prompt -> static policy
  - frame rate / frame count -> workflow animation nodes
  - checkpoint / LoRA / ControlNet -> injected through adapters
- Status: production

### Pipeline A/B

- Engine: `Flux 2 + Wan 2.2`
- Current role: experimental
- Required before promotion:
  - final workflow export from ComfyUI
  - verified node names against live `/object_info`
  - verified Wan model path
  - end-to-end render with output validation
  - fallback and retry policy

## Execution Rules

- UI stage switch may not bypass readiness checks
- Queue task completion must mean output files were actually produced
- Render jobs and task queue status must stay consistent
- Missing nodes or missing models must be surfaced as operator-facing blockers, not hidden as generic timeout

## Recommended Near-Term Roadmap

1. Keep `C` as the single production path
2. Finish Flux/Wan workflow wiring in ComfyUI GUI and export the real workflow
3. Add per-pipeline output validation
4. Add approval states after render and before export
5. Add regression test scenes for each supported pipeline
