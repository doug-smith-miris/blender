# Miris Blender USD-Exporter Fixes

This branch (`miris/integration`) consolidates 42 fixes to Blender's USD/MaterialX exporter, landed against the stock 4.x exporter to make Blender scenes round-trip cleanly into Karma (Hydra's MaterialX-capable delegate, used by NVIDIA Omniverse, the Miris pipeline, and any other modern USD viewer).

Each fix is one PR on the fork (https://github.com/doug-smith-miris/blender), each with a render-lock regression test under `source/blender/io/usd/tests/test_miris_*.py` that pins the visual behavior in place against future regressions.

## How to use

```
git clone https://github.com/doug-smith-miris/blender.git -b miris/integration
# build per stock Blender instructions, then export with:
# File → Export → Universal Scene Description (.usd)
# Recommended: enable BOTH "UsdPreviewSurface Network" and "MaterialX Network"
```

The MaterialX path is where most fixes live. UsdPreviewSurface still works as the legacy fallback. Both should be enabled — see the **dual-output dropoff** section below.

---

## Material — node coverage

The MaterialX writer was missing or mis-emitting many common Cycles nodes. Symptoms ranged from silent passthroughs (texture stripped, color flat) to render-blocking compile errors in Karma.

| PR | Bug | Fix |
|---|---|---|
| #1 | Legacy `ShaderNodeMixRGB` (every file saved with Blender ≤ 3.3) produced no MaterialX output | Map to `<mix>` |
| #3 | RGB Curves silently passed color through without applying the curve mapping | Emit piecewise-linear network |
| #5 | `thin_film_bsdf` and other 1.39-only nodes left dangling refs that broke Karma compile | Substitute or prune unknown nodes |
| #11 | "Phantom" nodedef stubs from unresolved nodes survived export and broke Karma | Prune phantom shader prims |
| #26 | Same phantom-nodedef path on `swarmfish` group-expanded materials | Specialized pruning for grouped graphs |
| #32 | `<normalmap>` emitted `ND_normalmap_float` (MTLX 1.39 name) — consumers on 1.38 broke | Normalize to `ND_normalmap` |
| #33 | OpenPBR base_color drop when `SHD_OUTPUT_ALL` not present in the material | Pick inliner target the material actually has |
| #36 | UsdTransform2d dropped silently when Cycles Mapping node `vector_type != POINT` | Emit for all vector_type modes |
| #39 | `thin_film_bsdf` wrapped in a `<layer>` even though 1.39 has no thin-film NodeDef | Wire thin-film directly onto dielectric/conductor BSDFs |

## Material — graph traversal

The exporter's upstream walker (`traverse_channel` in `usd_writer_material.cc`) had multiple correctness bugs that caused the wrong textures to be wired to the wrong sockets.

| PR | Bug | Fix |
|---|---|---|
| #4 | Naive DFS recursed through every input of every intermediate node, picking up unrelated control-wire textures | Per-node-type signal-carrier allowlist |
| #10 | Walker didn't cross `ShaderNodeGroup` boundaries; Principled BSDF inputs flattened to UsdPreviewSurface defaults | Recurse into ShaderNodeGroup inputs |
| #13 | `find_bsdf_node` returned the first BSDF in node-tree iteration order, not the one wired to active Material Output | Walk back from Material Output's Surface |
| #14 | Stage 2 of the above for ShaderNodeGroup containing the active BSDF | Descend into groups when locating BSDF |
| #15 | Allowlist regression on a later branch — wrong textures came back | Re-restrict upstream walk to signal-carrying inputs |
| #17 | "Layer" BSDF stack with absent sub-node produced degenerate MaterialX layers | Bypass degenerate `<layer>` nodes |
| #18 | `creature_eyes` / `creature_pupil` wrap shader networks in ShaderNodeGroup; BSDF was unfindable | Recurse into ShaderNodeGroup when locating BSDF |
| #25 | Allowlist regression on yet another stacked branch | Per-node-type whitelist re-applied |

## Material — ShaderNodeGroup expansion

Production materials wrap their networks in `ShaderNodeGroup` for reuse. The exporter ignored groups, producing empty materials.

PRs #2, #14, #18 cover this: detect groups, descend into the contained tree, and treat the group's internal Material Output as the export root.

## Material — opacity / Light-Path cutouts

Cycles's Light-Path / Transparent-BSDF / Mix-Shader cutouts (used for render-visibility — e.g. invisible-to-shadows planes) have no UsdPreviewSurface equivalent. The exporter was either silently zeroing alpha (making bodies invisible) or letting the cutout flow through unchanged into MaterialX where Karma misinterpreted it.

| PR | Bug | Fix |
|---|---|---|
| #27 | Body invisible — alpha chain zeroed to 0 | Resolve to opaque (1.0); flag the unrepresentable chain |
| #34 | After #27, shadows still wrong because Karma rendered the body opaque casting shadows | Author `primvars:karma:object:rendervisibility` no-shadow on material |
| #37 | #34's primvar lived on Material — Karma's actual read site is the bound geometry | Propagate primvar to bound geom |
| #40 | Bake the painterly Base Color chain to a real texture when traversal can't cross the modifiers | Extend bake path to nested-group + Light-Path cutout materials |

## Material — color baking fallback

When a Base Color is reached only through nodes the allowlist won't cross (Hue/Sat, RGB Curves, Mix, ColorRamp), the exporter previously dropped the texture entirely. PRs #31 and #40 add a "bake to a real texture" fallback path so the look survives.

## Material — anisotropy / OpenPBR

| PR | Bug | Fix |
|---|---|---|
| #9 | OpenPBR anisotropy axis 90° off Cycles | Document the bridge math (no code change yet) |
| #16 | Same — actually correct the math | Derive correct OpenPBR anisotropy bridge |

## Material — UV primvar naming

| PR | Bug | Fix |
|---|---|---|
| #19 | `UsdPrimvarReader_float2.varname` not resolving to the geometry's actual UV set when the mesh writer renamed the default UV to `st` | Resolve varname to the exported UV set |

## Material — dual-output behavior

A Material can author both `outputs:surface` (UsdPreviewSurface) and `outputs:mtlx:surface` (MaterialX). Karma and other Hydra delegates **always** prefer MaterialX when both are present. But stock Blender's MaterialX path is incomplete enough that consumers (and CI) sometimes want the PreviewSurface fallback.

| PR | Behavior |
|---|---|
| #23 | Lock the dual-output invariant: enabling both `generate_preview_surface=True` + `generate_materialx_network=True` must emit BOTH arcs |
| #35 | When MaterialX network authors the surface, skip the UsdPreviewSurface arc (cleaner output for MTLX-only pipelines) |
| #41 | Stamp `customLayerData` marker so consumers can detect the MaterialX-only fallback case |

## Geometry — Geometry Nodes & brushstroke meshes

Geometry-Nodes-realized meshes/curves report 0 materials on the evaluated data block, so the writer skipped material binding entirely. Multiple PRs fix this by falling back to the source Object's material slots when the eval data is empty.

| PR | Bug | Fix |
|---|---|---|
| #6 | GN brushstroke meshes lost material assignment | Route GN dupli data through writer dispatch by evaluated data type |
| #12 | GN curves had `totcol=0` on the eval, dropping material binding | Fall back to original Object slots |
| #20 | Same on swarmfish brushstroke-tools fins/tail | Resolve via eval + original-object fallback |

## Geometry — multi-material slots

| PR | Bug | Fix |
|---|---|---|
| #7 | Material slots used by only one face_group were dropped — `material_indices.is_single()` short-circuit skipped them | Author one `UsdGeomSubset` per declared (non-empty) slot, even when empty |
| #28 | Per-curve material_index lost on curves | Author per-curve material_index as UsdGeomSubset |

## Geometry — armature

| PR | Bug | Fix |
|---|---|---|
| #42 | Armature outside the render-eval depsgraph traversal was missing from `armature_export_map_`, so child meshes had no skeleton binding | Author fallback skeleton when armature isn't in the export map |

## Lighting — world dome

| PR | Bug | Fix |
|---|---|---|
| #8 | Background node Strength was dropped when an environment texture was connected; HDRI dome rendered at intensity=1.0 (way too dim) | Author dome intensity, texture format, and Karma camera-visibility |
| #22 | Same fix on a different lineage | Author world-dome intensity from Background Strength |
| #38 | World output picked the first `SH_NODE_OUTPUT_WORLD` regardless of engine; Cycles/Eevee variants got selected wrong | ALL → CYCLES → EEVEE fallback for output selection |

## Test infrastructure (render-locks)

Several PRs add no exporter code, only regression-test harnesses that bake the visual outcome of preceding fixes into pinned PNG comparisons. These are how we ensure later refactors can't silently break landed fixes.

PRs: #21 (swarmfish umbrella), #23 (simple-PBR round-trip), #24 (single-slot UV-sphere geometry), #26 (phantom-nodedef on swarmfish), #29 (Cycles-vs-Karma harness), #30 (isolated-fixture creature-body).

Each test file: `source/blender/io/usd/tests/test_miris_*.py` — 35 in total at time of writing.

---

## Status of upstreaming

These PRs are open against the Miris fork (`doug-smith-miris/blender`), not yet against `blender/blender` upstream. The intent is to clean up, deduplicate (some PRs supersede earlier ones in the same code region), and submit as a smaller series to Blender upstream once the patterns are settled.

## Updating this branch

When a new fix lands on its own `miris/...` branch:

```
cd /path/to/Blender-integration  # or your local worktree on miris/integration
git fetch origin
git merge --no-ff origin/miris/<new-fix-branch>
git push origin miris/integration
```

If the merge conflicts, the incoming branch's version usually represents the latest thinking — merge with `-X theirs` after verifying no other recent fix would be lost.
