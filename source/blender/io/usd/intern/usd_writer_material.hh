/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

#include "BLI_string_ref.hh"

#include <pxr/usd/usd/prim.h>
#include <pxr/usd/usdShade/material.h>

#include <string>

namespace blender {

struct bNode;
struct Image;
struct Material;
struct ReportList;

namespace io::usd {

struct USDExporterContext;
struct USDExportParams;

/**
 * Create USDMaterial from Blender material.
 *
 * \param active_uvmap_name: used as the default UV set name sampled by the `primvar`
 * reader shaders generated for image texture nodes that don't have an attached UVMap node.
 */
pxr::UsdShadeMaterial create_usd_material(const USDExporterContext &usd_export_context,
                                          pxr::SdfPath usd_path,
                                          Material *material,
                                          const std::string &active_uvmap_name,
                                          ReportList *reports);

/**
 * Returns a USDPreviewSurface token name for a given Blender shader Socket name,
 * or an empty TfToken if the input name is not found in the map.
 */
pxr::TfToken token_for_input(const StringRef input_name);

void export_texture(bNode *node,
                    const pxr::UsdStageRefPtr stage,
                    const bool allow_overwrite = false,
                    ReportList *reports = nullptr);

void export_texture(Image *ima,
                    const pxr::UsdStageRefPtr stage,
                    const bool allow_overwrite = false,
                    ReportList *reports = nullptr);

/**
 * Gets an asset path for the given texture image / node. The resulting path
 * may be absolute, relative to the USD file, or in a 'textures' directory
 * in the same directory as the USD file, depending on the export parameters.
 * The filename is typically the image filepath but might also be automatically
 * generated based on the image name for in-memory textures when exporting textures.
 * This function may return an empty string if the image does not have a filepath
 * assigned and no asset path could be determined.
 */
std::string get_tex_image_asset_filepath(bNode *node,
                                         const pxr::UsdStageRefPtr stage,
                                         const USDExportParams &export_params);

std::string get_tex_image_asset_filepath(Image *ima,
                                         const pxr::UsdStageRefPtr stage,
                                         const USDExportParams &export_params);
/**
 * Return a USD asset path referencing the given texture file.
 * The resulting path may be absolute, relative to the USD file,
 * or in a 'textures' directory in the same directory as the USD file,
 * depending on the export parameters.
 */
std::string get_tex_image_asset_filepath(const std::string &asset_path,
                                         const std::string &stage_path,
                                         const USDExportParams &export_params);

/**
 * Copy `primvars:karma:object:rendervisibility` from `usd_material` onto `bound_prim`
 * if the material authors it. Karma reads this primvar from the geometry prim (not the
 * Material prim), so material-level authoring (PR #34, BL-MAT-OPACITY-LIGHTPATH-DROP)
 * is invisible to the renderer until propagated; mesh and curves writers call this
 * after `UsdShadeMaterialBindingAPI::Bind` so the artist's "camera-visible, no-shadow"
 * intent reaches Karma. No-op when the material does not author the primvar or when
 * the geom already has an authored value (avoids clobbering an explicit override).
 */
void propagate_karma_object_rendervisibility(const pxr::UsdShadeMaterial &usd_material,
                                             const pxr::UsdPrim &bound_prim);

/**
 * Stamp `customLayerData["miris:requiresMaterialX"] = true` on the root layer when
 * at least one UsdShadeMaterial in the stage has a connected `outputs:mtlx:surface`
 * but no connected `outputs:surface`. Such "mtlx-only" materials appear by default
 * since PR #35 (BL-MAT-MATX-SHADOWS-PREVIEWSURFACE) suppressed the dead-weight
 * UsdPreviewSurface arc; they also slip through whenever the preview-surface writer
 * can't reach a BSDF from the active Material Output regardless of the opt-in flag.
 * The marker lets downstream tools (older Hydra Storm, certain Omniverse builds)
 * detect that this layer requires a MaterialX-capable renderer to resolve shading.
 * No-op when every Material with a MaterialX surface also authors the universal arc.
 */
void author_materialx_required_marker(const pxr::UsdStageRefPtr &stage);

}  // namespace io::usd
}  // namespace blender
