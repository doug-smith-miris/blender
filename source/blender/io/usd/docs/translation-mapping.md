# Blender Shader Node → MaterialX node mapping

The translation table for the Miris fork of Blender's USD exporter (MaterialX path).
Each row is added by one architectural-mode build cycle of the Improvement Agent.
Reviewer (Doug) approves via PR merge.

> **Note on terminology.** The architectural-mapping mission template references
> *UE Material expressions* but the Miris fork being extended here is Blender's
> USD exporter. The equivalent concept in Blender is a `ShaderNode*` (e.g.
> `ShaderNodeMixRGB`, `ShaderNodeTexImage`). All references below use Blender
> terminology.

Each entry corresponds to a PR that:
1. Updated this doc
2. Added/extended the `NODE_SHADER_MATERIALX_BEGIN ... END` block in the matching
   `source/blender/nodes/shader/nodes/node_shader_*.cc` file
3. Added a `blender --background --python` test script under
   `source/blender/io/usd/docs/tests/` that constructs + exports a material using
   the target node
4. Verified end-to-end (compile + headless export + USD inspection via hython)

The "fix surface" is the per-node `node_shader_<name>.cc` file's
`NODE_SHADER_MATERIALX_BEGIN ... END` block (driven by `NodeParser::compute`).
See `source/blender/nodes/shader/materialx/node_parser.h` for the available
`NodeItem` helpers and the supported `NodeItem::Type` enum.

## Status

| Blender node | MaterialX node | Modes / scope | PR | Date |
|---|---|---|---|---|
| `ShaderNodeMixRGB` (legacy) | `mix` (standard) | `MA_RAMP_BLEND` only (default linear mix); other 18 blend modes emit a warning + best-effort linear mix | (this PR) | 2026-05-25 |

## Notes per node

### `ShaderNodeMixRGB` (legacy "Mix (Legacy)" node)

**File:** `source/blender/nodes/shader/nodes/node_shader_mix_rgb.cc`

**Why a separate mapping from the modern `ShaderNodeMix`:** Files saved with
Blender ≤ 3.3 use the legacy `ShaderNodeMixRGB` (DNA type
`SH_NODE_MIX_RGB_LEGACY`). The newer `ShaderNodeMix` (`node_shader_mix.cc`)
already has a MaterialX implementation, but the legacy node previously had none,
so every material that contained one was emitted as a partial network with the
node's downstream connections dangling — exactly the failure mode the diagnostic
agent observed on swarmfish (`io.materialx WARNING Unsupported node: …` followed
by `Reference to undefined variable` errors in Karma's compile step). See
`discovered-context.md` from pipeline run
`edefbd0e-499d-4d13-b3e0-1d2418b96960`.

**Translation (default mode, `MA_RAMP_BLEND`):**

```
ShaderNodeMixRGB(fac, color1, color2)        MaterialX:
    └─ MA_RAMP_BLEND                         <mix bg=color1 fg=color2 mix=clamp(fac, 0, 1)>
    └─ custom2 & SHD_MIXRGB_CLAMP             └─ wrapped in <clamp low=0 high=1> if set
```

The legacy node's GPU implementation always clamps `Fac` to `[0,1]` (see
`gpu_shader_mix_rgb`); we preserve that semantic by always calling
`fac.clamp()`. The optional `clamp_result` flag (legacy `custom2 &
SHD_MIXRGB_CLAMP`) wraps the result in a `<clamp>`.

**Edge cases / behavior of unsupported blend modes:** Every non-`MA_RAMP_BLEND`
mode (Add, Multiply, Screen, Overlay, Soft/Linear light, Hue/Sat/Val/Color,
Burn/Dodge, Difference, Exclusion, Dark/Light) currently emits a
`CLOG_WARN(LOG_IO_MATERIALX, …)` and falls back to a linear mix. This is
strictly better than the prior behavior (silent drop → broken USD) but it
**does not match the source render**. Each remaining blend mode is a candidate
mission — most map to a fixed compound MaterialX network, e.g.:

- `MA_RAMP_ADD` → `mix(c1, add(c1, c2), fac)`
- `MA_RAMP_MULT` → `mix(c1, multiply(c1, c2), fac)`
- `MA_RAMP_SUB` → `mix(c1, subtract(c1, c2), fac)`
- `MA_RAMP_SCREEN` → `mix(c1, subtract(1, multiply(subtract(1, c1), subtract(1, c2))), fac)`
- The HSV-family modes need `rgbtohsv` + `hsvtorgb` (both standard) plus per-channel substitution.
- `MA_RAMP_OVERLAY` / `_SOFT` / `_LINEAR` have no single-node MaterialX equivalent and need branched compositions.

## Nodes with NO MaterialX equivalent (catalog as discovered)

These nodes will get a `CLOG_WARN` emitted at export time (or the writer's
"Unsupported node" warning, until per-node handling is added) so the divergence
is observable in `discovered-context.md`-style logs.

| Blender node | Why no equivalent | Behavior in current fork |
|---|---|---|
| `ShaderNodeShaderToRGB` | Eevee-internal concept (samples a shader as RGB); no MaterialX surfaceshader→color conversion. | Network dropped silently (pre-existing bug — to be addressed by emitting an explicit warning). |
| `ShaderNodeScript` | Arbitrary OSL / GLSL; cannot be transpiled to MaterialX in general. | Network dropped silently. |
| `ShaderNodeTexMagic` | Algorithm is Blender-specific (cascaded sin/cos with `depth` levels); not in MaterialX standard library. | Network dropped silently. |
| `ShaderNodeWavelength` | Requires a CIE-XYZ color table lookup. | Network dropped silently. |
| `ShaderNodeBsdfHair` / `ShaderNodeBsdfHairPrincipled` | Marschner/Chiang fiber BSDFs; not in MaterialX PBR. | Network dropped silently. |
| `ShaderNodeBsdfToon` | NPR-shaped toon BSDF; not in MaterialX PBR. | Network dropped silently. |
| `ShaderNodeEeveeSpecular` | Eevee-only legacy specular model. | Network dropped silently. |

## Change log

- 2026-05-25 — `ShaderNodeMixRGB` (legacy) → MaterialX `mix` (MA_RAMP_BLEND only). Other blend modes emit a warning and fall through to a linear mix as a best-effort approximation. Doc created.
