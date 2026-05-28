/* SPDX-FileCopyrightText: 2011-2022 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include <MaterialXFormat/XmlIo.h>

#include "node_parser.h"

#include "BKE_lib_id.hh"
#include "BKE_node_legacy_types.hh"

#include "DEG_depsgraph.hh"

#include "DNA_material_types.h"

#include "NOD_shader.h"
#include "NOD_shader_nodes_inline.hh"

#include "material.h"

namespace blender::nodes::materialx {

class DefaultMaterialNodeParser : public NodeParser {
 public:
  using NodeParser::NodeParser;

  NodeItem compute() override
  {
    const Material *material = graph_.material;
    NodeItem surface = create_node(
        "open_pbr_surface",
        NodeItem::Type::SurfaceShader,
        {{"base_weight", val(1.0f)},
         {"base_color", val(MaterialX::Color3(material->r, material->g, material->b))},
         {"base_diffuse_roughness", val(material->roughness)},
         {"specular_weight", val(material->spec)},
         {"base_metalness", val(material->metallic)}});

    NodeItem res = create_node(
        "surfacematerial", NodeItem::Type::Material, {{"surfaceshader", surface}});
    return res;
  }

  NodeItem compute_error()
  {
    NodeItem surface = create_node("open_pbr_surface",
                                   NodeItem::Type::SurfaceShader,
                                   {{"base_color", val(MaterialX::Color3(1.0f, 0.0f, 1.0f))}});
    NodeItem res = create_node(
        "surfacematerial", NodeItem::Type::Material, {{"surfaceshader", surface}});
    return res;
  }
};

MaterialX::DocumentPtr export_to_materialx(Depsgraph *depsgraph,
                                           Material *material,
                                           const ExportParams &export_params)
{
  CLOG_DEBUG(LOG_IO_MATERIALX, "Material: %s", material->id.name);

  MaterialX::DocumentPtr doc = MaterialX::createDocument();
  NodeItem output_item;

  NodeGraph graph(depsgraph, material, export_params, doc);

  bNodeTree *local_tree = bke::node_tree_add_tree(
      nullptr, "Inlined Tree", material->nodetree->idname);
  BLI_SCOPED_DEFER([&]() { BKE_id_free(nullptr, &local_tree->id); });

  /* Pick an engine target that the source tree actually has a Material Output for.
   * The inliner filters Material Output nodes by `params.target_engine_` (only ALL +
   * matching-target outputs are processed) AND `ntreeShaderOutputNode(_, target)` only
   * matches outputs whose `custom1` is SHD_OUTPUT_ALL or the requested target. Production
   * .blends often have only SHD_OUTPUT_CYCLES (and/or SHD_OUTPUT_EEVEE) Material Outputs —
   * the artist's authoritative offline-rendering network. With the default
   * target=SHD_OUTPUT_ALL the inliner would skip those output sockets entirely and
   * `ntreeShaderOutputNode` would return null, dropping into compute_error() and emitting
   * a magenta open_pbr_surface stub in place of the real MaterialX network. Prefer ALL,
   * then CYCLES (the closest renderer convention for MaterialX consumers like Karma),
   * then EEVEE. */
  NodeShaderOutputTarget mtlx_target = SHD_OUTPUT_ALL;
  if (material->nodetree) {
    bool has_all = false, has_cycles = false, has_eevee = false;
    for (const bNode *src_node : material->nodetree->all_nodes()) {
      if (src_node->type_legacy != SH_NODE_OUTPUT_MATERIAL) {
        continue;
      }
      switch (src_node->custom1) {
        case SHD_OUTPUT_ALL:
          has_all = true;
          break;
        case SHD_OUTPUT_CYCLES:
          has_cycles = true;
          break;
        case SHD_OUTPUT_EEVEE:
          has_eevee = true;
          break;
        default:
          break;
      }
    }
    if (!has_all) {
      if (has_cycles) {
        mtlx_target = SHD_OUTPUT_CYCLES;
      }
      else if (has_eevee) {
        mtlx_target = SHD_OUTPUT_EEVEE;
      }
    }
  }

  InlineShaderNodeTreeParams params;
  params.target_engine_ = mtlx_target;
  inline_shader_node_tree(*material->nodetree, *local_tree, params);

  local_tree->ensure_topology_cache();
  bNode *output_node = ntreeShaderOutputNode(local_tree, mtlx_target);
  if (output_node && output_node->typeinfo->materialx_fn) {
    NodeParserData data = {graph, NodeItem::Type::Material, nullptr, graph.empty_node()};
    output_node->typeinfo->materialx_fn(&data, output_node, nullptr);
    output_item = data.result;
  }
  else {
    output_item = DefaultMaterialNodeParser(
                      graph, nullptr, nullptr, NodeItem::Type::Material, nullptr)
                      .compute_error();
  }

  /* This node is expected to have a specific name to link up to USD. */
  graph.set_output_node_name(output_item);

  CLOG_DEBUG(LOG_IO_MATERIALX,
             "Material: %s\n%s",
             material->id.name,
             MaterialX::writeToXmlString(doc).c_str());
  return doc;
}

}  // namespace blender::nodes::materialx
