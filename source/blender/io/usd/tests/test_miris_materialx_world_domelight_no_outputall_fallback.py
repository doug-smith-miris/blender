"""Regression test for materialx-world-domelight-no-outputall-fallback.

Stock Blender's USD exporter scans `scene.world.node_tree` for the first
`SH_NODE_OUTPUT_WORLD` with `NODE_DO_OUTPUT` set, without filtering by engine
target. A real-world failure mode this exposes: a world that has both a
Cycles-target and an EEVEE-target Material Output but no `SHD_OUTPUT_ALL`
universal one. Blender enforces only-one-active across all SH_NODE_OUTPUT_WORLD
nodes regardless of `target`, so whichever the artist (or a tool) marked
active last wins the `NODE_DO_OUTPUT` flag — and the stock dome-light writer
picks that one verbatim. If EEVEE is the most-recently-active output, its
plain-Background sub-network gets exported and the artist's authoritative
Cycles HDRI network is silently dropped from the USD dome light.

The fix in `source/blender/io/usd/intern/usd_light_convert.cc` mirrors PR #33's
ALL→CYCLES→EEVEE engine-target fallback (analogous to
`source/blender/nodes/shader/materialx/material.cc::export_to_materialx`):
scan the world tree for which engine targets it has Material Outputs for,
pick the most universal one present (ALL → CYCLES → EEVEE), then call
`ntreeShaderOutputNode(tree, picked_target)` so the NODE_DO_OUTPUT preference
machinery operates within the chosen target rather than across the whole
tree. Karma/USD consumers get the Cycles-side authoritative HDRI.

Run from the patched Blender binary:

  build_darwin/bin/Blender.app/Contents/MacOS/Blender --background \\
    --python source/blender/io/usd/tests/\\
    test_miris_materialx_world_domelight_no_outputall_fallback.py
"""
import os
import sys
import tempfile

import bpy

MIKASSA_BLEND = (
    "/Users/d.smith/MirisProjects/AYON/Project Gold Files/220_0020-packed/"
    "assets/chars/mikassa/publish/mikassa-v001.blend"
)
HDRI_PATH = (
    "/private/tmp/project-gold-staging/exports/library/lighting_rigs/"
    "lighting_rigs/ibl/brown_photostudio_06_4k.exr"
)

CYCLES_STRENGTH = 5.0
EEVEE_STRENGTH = 1.0


def _build_eevee_active_world(world):
    """Wire two SH_NODE_OUTPUT_WORLDs into `world`:

      - CYCLES-target output (Strength=5.0, Environment Texture HDRI).
      - EEVEE-target output (Strength=1.0, plain Background, NO texture).

    Then set `is_active_output=True` on the EEVEE one — Blender's RNA setter
    clears NODE_DO_OUTPUT on every other SH_NODE_OUTPUT_WORLD in the tree
    (irrespective of its `target`), so EEVEE is the only one with
    NODE_DO_OUTPUT. NO SHD_OUTPUT_ALL output exists — the precondition for
    the engine-target fallback to engage.
    """
    assert os.path.exists(HDRI_PATH), f"Missing HDRI: {HDRI_PATH}"

    world.use_nodes = True
    nt = world.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)

    # CYCLES side — the artist's authoritative HDRI network.
    bg_c = nt.nodes.new("ShaderNodeBackground")
    bg_c.name = "BG_CYCLES"
    bg_c.inputs["Strength"].default_value = CYCLES_STRENGTH
    tex = nt.nodes.new("ShaderNodeTexEnvironment")
    tex.image = bpy.data.images.load(HDRI_PATH, check_existing=True)
    nt.links.new(tex.outputs["Color"], bg_c.inputs["Color"])
    out_c = nt.nodes.new("ShaderNodeOutputWorld")
    out_c.name = "OUT_CYCLES"
    out_c.target = "CYCLES"
    nt.links.new(bg_c.outputs["Background"], out_c.inputs["Surface"])

    # EEVEE side — to be marked active. Plain Background, no texture.
    bg_e = nt.nodes.new("ShaderNodeBackground")
    bg_e.name = "BG_EEVEE"
    bg_e.inputs["Strength"].default_value = EEVEE_STRENGTH
    out_e = nt.nodes.new("ShaderNodeOutputWorld")
    out_e.name = "OUT_EEVEE"
    out_e.target = "EEVEE"
    nt.links.new(bg_e.outputs["Background"], out_e.inputs["Surface"])

    # Mark EEVEE active LAST so its setter clears NODE_DO_OUTPUT on CYCLES
    # too (the RNA setter normalizes across same-typed nodes regardless of
    # `target`). Result: EEVEE has NODE_DO_OUTPUT, CYCLES does not.
    out_e.is_active_output = True
    return out_c, out_e


def _dump_outputs(world):
    return sorted(
        (n.name, n.target, bool(n.is_active_output))
        for n in world.node_tree.nodes
        if n.type == "OUTPUT_WORLD"
    )


def main():
    assert os.path.exists(MIKASSA_BLEND), f"Missing real-corpus asset: {MIKASSA_BLEND}"

    bpy.ops.wm.open_mainfile(filepath=MIKASSA_BLEND)
    world = bpy.context.scene.world
    assert world is not None, "mikassa-v001.blend has no world"

    out_c, out_e = _build_eevee_active_world(world)
    outputs = _dump_outputs(world)
    print(f"  world outputs: {outputs}")

    targets = {n.target for n in world.node_tree.nodes if n.type == "OUTPUT_WORLD"}
    assert "ALL" not in targets, (
        f"Test precondition broken: ALL-target world output present "
        f"(targets={targets!r}); the fallback only engages without ALL."
    )
    assert "CYCLES" in targets and "EEVEE" in targets, (
        f"Test precondition broken: world missing CYCLES or EEVEE "
        f"(targets={targets!r})."
    )
    assert out_e.is_active_output, (
        "Test precondition broken: EEVEE output not marked active. The "
        "discriminator depends on stock picking the EEVEE-side based on "
        "NODE_DO_OUTPUT."
    )
    assert not out_c.is_active_output, (
        "Test precondition broken: CYCLES output is also marked active. "
        "Blender's RNA setter should have cleared NODE_DO_OUTPUT on it "
        "when EEVEE was set active."
    )

    out_dir = tempfile.mkdtemp(prefix="miris_world_domelight_")
    out_usd = os.path.join(out_dir, "mikassa_dome.usda")
    bpy.ops.wm.usd_export(
        filepath=out_usd,
        export_materials=False,
        export_lights=True,
        export_cameras=False,
        convert_world_material=True,
        relative_paths=False,
    )
    assert os.path.exists(out_usd), f"USD export missing: {out_usd}"

    from pxr import Usd, UsdLux

    stage = Usd.Stage.Open(out_usd)
    assert stage, f"Could not open exported stage: {out_usd}"

    dome = None
    for p in stage.Traverse():
        if p.IsA(UsdLux.DomeLight):
            dome = UsdLux.DomeLight(p)
            break

    failures = []

    if dome is None:
        failures.append(
            "No UsdLuxDomeLight emitted — the picked World Output didn't "
            "carry a recognizable background+color/texture chain."
        )
    else:
        tex_attr = dome.GetTextureFileAttr()
        tex_val = (
            tex_attr.Get() if tex_attr.HasAuthoredValue() else None
        )
        if tex_val is None:
            failures.append(
                "DomeLight inputs:texture:file is unauthored — the exporter "
                "picked the EEVEE-target output (plain Background, no "
                "Environment Texture) instead of the CYCLES one carrying "
                "the HDRI. Engine-target fallback regression."
            )
        else:
            tex_path = tex_val.path if hasattr(tex_val, "path") else str(tex_val)
            if "brown_photostudio_06_4k" not in tex_path:
                failures.append(
                    f"DomeLight inputs:texture:file = {tex_path!r}, expected "
                    "a path referencing brown_photostudio_06_4k.exr from "
                    "the CYCLES-target output."
                )

    if failures:
        print("FAIL materialx-world-domelight-no-outputall-fallback:")
        for f in failures:
            print(f"  - {f}")
        print(f"  USD: {out_usd}")
        sys.exit(1)

    print("PASS materialx-world-domelight-no-outputall-fallback")
    print(f"  USD: {out_usd}")
    print(f"  dome at {dome.GetPath()}")
    tex_val = dome.GetTextureFileAttr().Get()
    print(f"  texture = {tex_val.path}")


if __name__ == "__main__":
    main()
