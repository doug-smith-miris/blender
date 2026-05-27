"""Regression test for `swarmfish-eyes-pupil-empty-previewsurface`.

The swarmfish `creature_eyes` and `creature_pupil` materials wrap their entire shader
network inside a #ShaderNodeGroup that exposes only a `Shader` output, which is wired
straight into Material Output's Surface socket. The actual Principled BSDF lives *inside*
that group.

Blender's UsdPreviewSurface writer located the BSDF with `find_bsdf_node()`, which only
scanned the material's top-level node tree (`material->nodetree->all_nodes()`). For these
group-wrapped materials that scan found nothing, so the writer emitted an empty
`def Material "creature_eyes" {}` prim with no surface output and no Shader descendants.
Karma then rendered the bound eye/pupil geometry as default grey.

The fix in usd_writer_material.cc makes `find_bsdf_node()` (and `find_displacement_node()`)
recurse into nested node groups. This test exports the real swarmfish asset and asserts
that `creature_eyes` and `creature_pupil` each carry a connected surface output backed by a
`UsdPreviewSurface` shader prim — i.e. the Material prim is no longer empty.

Run headless with the PATCHED build:
  build_darwin/.../Blender --background <swarmfish.blend> \
      --python test_miris_swarmfish_eyes_pupil_empty_previewsurface.py
"""

import os
import sys
import tempfile

import bpy
from pxr import Usd, UsdShade

SWARMFISH_BLEND = (
    "/Users/d.smith/MirisProjects/AYON/Singularity Files/040_0030/"
    "assets/char/swarmfish/publish/swarmfish-v001.blend"
)

# Materials that, in the source .blend, are Output <- ShaderNodeGroup(Shader) graphs.
GROUP_WRAPPED_MATERIALS = ("creature_eyes", "creature_pupil")


def export_swarmfish_preview():
    out_dir = tempfile.mkdtemp(prefix="miris_swarmfish_eyes_")
    usd_path = os.path.join(out_dir, "swarmfish-preview.usda")

    # Unhide everything so geometry-nodes driven meshes (and their materials) survive.
    for obj in bpy.data.objects:
        obj.hide_viewport = False
        obj.hide_render = False
        obj.hide_set(False)

    bpy.ops.wm.usd_export(
        filepath=usd_path,
        check_existing=False,
        selected_objects_only=False,
        export_materials=True,
        evaluation_mode="RENDER",
        generate_preview_surface=True,
        generate_materialx_network=False,
        root_prim_path="/root",
    )
    return usd_path


def find_material(stage, name):
    for prim in stage.Traverse():
        mat = UsdShade.Material(prim)
        if mat and prim.GetName() == name:
            return mat
    return None


def surface_shader_id(material):
    """Return the shader id token of the prim driving the material's surface output,
    or None if there is no connected surface source."""
    source = material.ComputeSurfaceSource()
    # ComputeSurfaceSource returns (shader, sourceName, sourceType); shader is falsy if unbound.
    shader = source[0] if isinstance(source, tuple) else source
    if not shader:
        return None
    return shader.GetIdAttr().Get()


def main():
    usd_path = export_swarmfish_preview()
    print("MIRIS_EXPORTED_USD:", usd_path)

    stage = Usd.Stage.Open(usd_path)
    assert stage, "Failed to open exported USD stage"

    failures = []
    for mat_name in GROUP_WRAPPED_MATERIALS:
        material = find_material(stage, mat_name)
        if material is None:
            failures.append(f"{mat_name}: Material prim missing entirely")
            continue

        n_shaders = sum(
            1 for p in material.GetPrim().GetChildren() if UsdShade.Shader(p)
        )
        sid = surface_shader_id(material)
        print(f"MIRIS_MATERIAL {mat_name}: surface_id={sid} n_child_shaders={n_shaders}")

        if sid is None:
            failures.append(
                f"{mat_name}: no connected surface source (empty Material prim) "
                f"-- find_bsdf_node failed to descend into the ShaderNodeGroup"
            )
        elif str(sid) != "UsdPreviewSurface":
            failures.append(f"{mat_name}: surface shader id is {sid!r}, expected UsdPreviewSurface")

    assert not failures, "Group-wrapped materials still export empty:\n  " + "\n  ".join(failures)

    print(
        "MIRIS_TEST_PASS: creature_eyes and creature_pupil export a connected "
        "UsdPreviewSurface (ShaderNodeGroup descent works)"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
