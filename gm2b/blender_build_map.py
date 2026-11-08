"""Запускается ВНУТРИ Blender:
    blender --background --python blender_build_map.py -- job.json

Строит карту: мир, брашевые объекты, библиотеку пропов + экземпляры, свет.
Большие массивы лежат в job.bin (numpy), метаданные — в job.json.
"""
import json
import math
import os
import sys

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from blender_build import build_material, export_formats, log  # noqa: E402


class Blobs:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = f.read()

    def get(self, ref):
        if ref is None:
            return None
        return np.frombuffer(self.data, dtype=np.dtype(ref["dtype"]), count=ref["n"], offset=ref["off"])


def make_mesh(name, pos, loops, starts, sizes, uvs_loop, mats, materials, smooth=None,
              normals=None, blend=None):
    me = bpy.data.meshes.new(name)
    me.vertices.add(len(pos) // 3)
    me.vertices.foreach_set("co", pos)
    me.loops.add(len(loops))
    me.loops.foreach_set("vertex_index", loops)
    me.polygons.add(len(starts))
    me.polygons.foreach_set("loop_start", starts)
    try:  # Blender < 4.0: число углов полигона задаётся явно
        me.polygons.foreach_set("loop_total", sizes)
    except (AttributeError, TypeError, RuntimeError):
        pass

    uniq, inv = np.unique(mats, return_inverse=True)
    for u in uniq:
        me.materials.append(materials[int(u)])
    me.polygons.foreach_set("material_index", inv.astype(np.int32))

    uv = me.uv_layers.new(name="UVMap")
    uv.data.foreach_set("uv", uvs_loop.astype(np.float32).ravel())

    if smooth is not None:
        me.polygons.foreach_set("use_smooth", smooth.astype(bool))
    if blend is not None:
        attr = me.attributes.new("blend", "FLOAT", "POINT")
        attr.data.foreach_set("value", blend)
    me.update(calc_edges=True)
    me.validate(clean_customdata=False)
    if normals is not None:
        if hasattr(me, "use_auto_smooth"):
            me.use_auto_smooth = True
        me.normals_split_custom_set_from_vertices(normals.reshape(-1, 3).tolist())
    return me


def mesh_from_bsp(d, blobs, materials, scale):
    pos = blobs.get(d["pos"]) * scale
    return make_mesh(
        d["name"], pos.astype(np.float32), blobs.get(d["loops"]), blobs.get(d["starts"]),
        blobs.get(d["sizes"]), blobs.get(d["uvs"]), blobs.get(d["mats"]), materials,
        smooth=blobs.get(d["smooth"]), blend=blobs.get(d["blend"]))


def euler(angles):
    """QAngle Source (pitch, yaw, roll) -> Euler XYZ Blender."""
    p, y, r = (math.radians(a) for a in angles)
    return (r, p, y)


def new_collection(name, parent):
    c = bpy.data.collections.new(name)
    parent.children.link(c)
    return c


def setup_viewports(scale):
    """Настройки 3D-видов, которые сохраняются в .blend: карта видна целиком,
    без мерцания поверхностей, а в Material Preview вместо HDRI — небо карты."""
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            space = area.spaces.active
            space.clip_start = 4 * scale    # ~10 см
            space.clip_end = 40000 * scale  # больше любой карты Source
            space.shading.use_scene_world = True
            space.shading.use_scene_lights = False  # тысячи ламп — только для Rendered


def main():
    job_path = sys.argv[sys.argv.index("--") + 1]
    with open(job_path, encoding="utf-8") as fh:
        job = json.load(fh)
    blobs = Blobs(job["bin"])
    scale = job["scale"]

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    root = new_collection(job["name"], scene.collection)

    log(f"материалы: {len(job['materials'])}")
    materials = [build_material(m["name"], [m], None) for m in job["materials"]]

    # --- мир
    world_coll = new_collection("world", root)
    me = mesh_from_bsp(job["world"], blobs, materials, scale)
    world_coll.objects.link(bpy.data.objects.new("world", me))
    log(f"мир: {len(me.polygons)} полигонов")

    # 3D-скайбокс: уменьшенная копия окружения, по умолчанию выключен
    sky_coll = new_collection("skybox_3d", root)
    if job.get("skybox"):
        sky_coll.objects.link(bpy.data.objects.new("skybox_3d", mesh_from_bsp(job["skybox"], blobs, materials, scale)))

    # --- брашевые объекты (двери, кнопки, func_brush…)
    brush_coll = new_collection("brush_entities", root)
    for b in job["brushes"]:
        obj = bpy.data.objects.new(b["name"], mesh_from_bsp(b, blobs, materials, scale))
        obj.location = [c * scale for c in b["origin"]]
        obj.rotation_euler = euler(b["angles"])
        brush_coll.objects.link(obj)
    log(f"брашевых объектов: {len(job['brushes'])}")

    # --- пропы: библиотека (скрыта из сцены) + экземпляры
    lib_root = new_collection("_prop_library", root)
    lib = []
    for p in job["prop_lib"]:
        coll = bpy.data.collections.new(p["name"])
        lib_root.children.link(coll)
        loops = blobs.get(p["loops"])
        uv_vertex = blobs.get(p["uvs_vertex"]).reshape(-1, 2)
        me = make_mesh(
            p["name"], (blobs.get(p["positions"]) * scale).astype(np.float32), loops,
            blobs.get(p["starts"]), blobs.get(p["sizes"]), uv_vertex[loops], blobs.get(p["mats"]),
            materials, smooth=np.ones(len(blobs.get(p["starts"])), bool), normals=blobs.get(p["normals"]))
        coll.objects.link(bpy.data.objects.new(p["name"], me))
        lib.append(coll)
    layer = bpy.context.view_layer.layer_collection.children[root.name].children[lib_root.name]
    layer.exclude = True

    props_coll = new_collection("props", root)
    for idx, x, y, z, pitch, yaw, roll, s, in_sky in job["props"]:
        coll = lib[int(idx)]
        obj = bpy.data.objects.new(coll.name, None)
        obj.instance_type = "COLLECTION"
        obj.instance_collection = coll
        obj.empty_display_size = 8 * scale
        obj.location = (x * scale, y * scale, z * scale)
        obj.rotation_euler = euler((pitch, yaw, roll))
        obj.scale = (s, s, s)
        (sky_coll if in_sky else props_coll).objects.link(obj)
    log(f"пропов: {len(job['props'])} (уникальных {len(lib)})")
    layers = bpy.context.view_layer.layer_collection.children[root.name].children
    layers[sky_coll.name].exclude = True

    # --- свет
    if job["lights"]:
        light_coll = new_collection("lights", root)
        for i, l in enumerate(job["lights"]):
            kind = {"point": "POINT", "spot": "SPOT", "sun": "SUN"}[l["kind"]]
            data = bpy.data.lights.new(f"{l['kind']}_{i}", kind)
            data.color = l["color"]
            if kind == "SUN":
                data.energy = 3.0
            else:
                # яркость Source -> ватты: подобрано на глаз, правится в свойствах света
                data.energy = l["brightness"] * 4.0 * (scale / 0.0254) ** 2
                data.shadow_soft_size = 4 * scale
                # Тени от тысячи ламп — главный тормоз Rendered-режима; в игре они тоже без теней
                data.use_shadow = False
            if kind == "SPOT":
                data.spot_size = math.radians(min(max(l["cone"] * 2, 1), 180))
                data.spot_blend = max(0.0, min(1.0, 1 - l["inner_cone"] / max(l["cone"], 1)))
            obj = bpy.data.objects.new(data.name, data)
            obj.location = [c * scale for c in l["origin"]]
            if kind in ("SPOT", "SUN"):
                from mathutils import Vector
                obj.rotation_euler = Vector(l["direction"]).to_track_quat("-Z", "Y").to_euler()
            light_coll.objects.link(obj)
        log(f"источников света: {len(job['lights'])}")

    # Фон = цвет неба карты: сквозь окна и проёмы видно «небо», а не серую пустоту
    world = bpy.data.worlds.new("sky")
    sky = job.get("sky_color", (0.45, 0.6, 0.8))
    world.color = sky
    try:
        world.use_nodes = True
        bg = world.node_tree.nodes["Background"]
        bg.inputs["Color"].default_value = (*sky, 1.0)
        bg.inputs["Strength"].default_value = 1.0
    except (AttributeError, KeyError):
        pass
    scene.world = world
    setup_viewports(scale)

    if "blend" in job.get("formats", ["blend"]):
        bpy.ops.wm.save_as_mainfile(filepath=job["output"])
        bpy.ops.file.make_paths_relative()
        bpy.ops.wm.save_mainfile()
        log("сохранено: .blend")

    def realize_props():
        # FBX/glTF/OBJ не везде понимают экземпляры коллекций — делаем их настоящими объектами
        inst = [o for o in props_coll.objects if o.instance_type == "COLLECTION"]
        if not inst:
            return
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        for o in inst:
            o.select_set(True)
        bpy.context.view_layer.objects.active = inst[0]
        bpy.ops.object.duplicates_make_real()
        log(f"пропы развёрнуты в объекты: {len(inst)}")

    export_formats(job, prepare=realize_props)


if __name__ == "__main__":
    main()
