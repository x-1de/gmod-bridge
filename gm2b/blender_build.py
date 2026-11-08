"""Запускается ВНУТРИ Blender:
    blender --background --python blender_build.py -- job.json

Строит арматуру, меши с весами, материалы и сохраняет .blend.
Не импортирует ничего из пакета gm2b — у Blender свой Python.
"""
import json
import math
import sys
from collections import defaultdict

import bpy
from mathutils import Matrix, Quaternion, Vector


def log(msg):
    print(f"GM2B: {msg}", flush=True)


# Кости Source смотрят вдоль локальной X, кости Blender — вдоль Y.
# Поворачиваем только для отображения; на скиннинг это не влияет.
BONE_FIX = Matrix.Rotation(-math.pi / 2, 4, "Z")


def build_armature(job, coll, scale):
    bones = job["bones"]
    arm = bpy.data.armatures.new(job["name"] + "_skeleton")
    obj = bpy.data.objects.new(job["name"], arm)
    coll.objects.link(obj)
    obj.show_in_front = True
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")

    world = []
    children = defaultdict(list)
    for i, b in enumerate(bones):
        x, y, z, w = b["quat"]
        local = Matrix.Translation(Vector(b["pos"]) * scale) @ Quaternion((w, x, y, z)).to_matrix().to_4x4()
        world.append(world[b["parent"]] @ local if b["parent"] >= 0 else local)
        if b["parent"] >= 0:
            children[b["parent"]].append(i)

    min_len = 1.0 * scale
    lengths, names = [], []
    for i, b in enumerate(bones):
        head = world[i].to_translation()
        kids = children[i]
        if kids:
            length = sum((world[k].to_translation() - head).length for k in kids) / len(kids)
        else:
            length = lengths[b["parent"]] * 0.5 if b["parent"] >= 0 else min_len * 4
        length = max(length, min_len)
        lengths.append(length)

        eb = arm.edit_bones.new(b["name"])
        eb.head = (0, 0, 0)
        eb.tail = (0, length, 0)
        eb.matrix = world[i] @ BONE_FIX
        names.append(eb.name)

    for i, b in enumerate(bones):
        if b["parent"] >= 0:
            arm.edit_bones[names[i]].parent = arm.edit_bones[names[b["parent"]]]

    bpy.ops.object.mode_set(mode="OBJECT")
    return obj, names


def _load_image(path, non_color=False):
    img = bpy.data.images.load(path, check_existing=True)
    if non_color:
        img.colorspace_settings.name = "Non-Color"
    return img


def _socket(sockets, identifier):
    return next(s for s in sockets if s.identifier == identifier)


def _skin_value(nodes, arm_obj):
    """Нода Value, которой драйвер передаёт arm_obj["skin"].
    Драйвер типа AVERAGE не исполняет Python, поэтому работает
    даже с выключенным Auto Run Python Scripts."""
    val = nodes.new("ShaderNodeValue")
    val.label = "skin"
    val.location = (-1500, 0)
    drv = val.outputs[0].driver_add("default_value").driver
    drv.type = "AVERAGE"
    var = drv.variables.new()
    var.type = "SINGLE_PROP"
    var.targets[0].id_type = "OBJECT"
    var.targets[0].id = arm_obj
    var.targets[0].data_path = '["skin"]'
    return val.outputs[0]


def _texture_switch(nodes, links, paths, skin, y, non_color=False, fallback=(1, 1, 1, 1)):
    """Выход (цвет, альфа): одна текстура, либо выбор из нескольких по номеру скина."""
    cache, outs = {}, []
    for i, path in enumerate(paths):
        if path is None:
            outs.append(None)
        elif path in cache:
            outs.append(cache[path])
        else:
            tex = nodes.new("ShaderNodeTexImage")
            tex.image = _load_image(path, non_color)
            tex.location = (-1300, y - 280 * len(cache))
            cache[path] = (tex.outputs["Color"], tex.outputs["Alpha"])
            outs.append(cache[path])
    if len(set(paths)) == 1:
        return outs[0]

    color = alpha = None
    for i, out in enumerate(outs):
        c, a = out if out else (fallback, 1.0)
        if color is None:
            color, alpha = c, a
            continue
        eq = nodes.new("ShaderNodeMath")
        eq.operation = "COMPARE"
        links.new(skin, eq.inputs[0])
        eq.inputs[1].default_value = i
        eq.inputs[2].default_value = 0.5
        eq.location = (-950, y - 120 * i)
        mixes = []
        for kind, prev, new in (("RGBA", color, c), ("FLOAT", alpha, a)):
            mix = nodes.new("ShaderNodeMix")
            mix.data_type = kind
            mix.location = (-750 if kind == "RGBA" else -600, y - 120 * i)
            suffix = "Color" if kind == "RGBA" else "Float"
            links.new(eq.outputs[0], _socket(mix.inputs, "Factor_Float"))
            for ident, value in ((f"A_{suffix}", prev), (f"B_{suffix}", new)):
                sock = _socket(mix.inputs, ident)
                if isinstance(value, (tuple, float)):
                    sock.default_value = value
                else:
                    links.new(value, sock)
            mixes.append(_socket(mix.outputs, f"Result_{suffix}"))
        color, alpha = mixes
    return color, alpha


def build_material(name, variants, arm_obj):
    """variants — описания материала для каждого скина (одно, если скин не меняет слот)."""
    m = variants[0]
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Roughness"].default_value = 0.7
    skin = _skin_value(nodes, arm_obj) if len(variants) > 1 else None

    bases = [v["base"] for v in variants]
    if any(bases):
        color, alpha = _texture_switch(nodes, links, bases, skin, 400)
        if m.get("base2"):
            # WorldVertexTransition: рельеф смешивает две текстуры по атрибуту вершин "blend"
            tex2 = nodes.new("ShaderNodeTexImage")
            tex2.image = _load_image(m["base2"])
            tex2.location = (-1300, 700)
            attr = nodes.new("ShaderNodeAttribute")
            attr.attribute_name = "blend"
            attr.location = (-1300, 900)
            mix = nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.location = (-700, 600)
            links.new(attr.outputs["Fac"], _socket(mix.inputs, "Factor_Float"))
            links.new(color, _socket(mix.inputs, "A_Color"))
            links.new(tex2.outputs["Color"], _socket(mix.inputs, "B_Color"))
            color = _socket(mix.outputs, "Result_Color")
        links.new(color, bsdf.inputs["Base Color"])
        if m["alpha"] != "OPAQUE":
            links.new(alpha, bsdf.inputs["Alpha"])
    elif m.get("color"):
        bsdf.inputs["Base Color"].default_value = (*m["color"], 1.0)
    elif m.get("water"):
        bsdf.inputs["Base Color"].default_value = (0.05, 0.18, 0.25, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.05
        bsdf.inputs["Alpha"].default_value = 0.6

    normals = [v["normal"] for v in variants]
    if any(normals):
        # PNG уже переведены в OpenGL-формат при конвертации, поэтому картинка идёт
        # в Normal Map напрямую — такую схему понимают экспортёры FBX/glTF/OBJ
        color, _ = _texture_switch(nodes, links, normals, skin, -400, non_color=True,
                                   fallback=(0.5, 0.5, 1.0, 1.0))
        nmap = nodes.new("ShaderNodeNormalMap")
        nmap.location = (-300, -400)
        links.new(color, nmap.inputs["Color"])
        links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])

    if m["alpha"] == "BLEND":
        _set_attr(mat, "surface_render_method", "BLENDED")  # Blender 4.2+
        _set_attr(mat, "blend_method", "BLEND")
    elif m["alpha"] == "CLIP":
        _set_attr(mat, "surface_render_method", "DITHERED")
        _set_attr(mat, "blend_method", "CLIP")
    mat.use_backface_culling = not m["nocull"]
    return mat


def _set_attr(obj, attr, value):
    try:
        setattr(obj, attr, value)
    except (AttributeError, TypeError):
        pass


def build_mesh(model, coll, arm_obj, bone_names, materials, scale):
    p, n, uv, f = model["positions"], model["normals"], model["uvs"], model["faces"]
    verts = [(p[i] * scale, p[i + 1] * scale, p[i + 2] * scale) for i in range(0, len(p), 3)]
    faces = [f[i:i + 3] for i in range(0, len(f), 3)]

    me = bpy.data.meshes.new(model["name"])
    me.from_pydata(verts, [], faces)

    slots = {}
    for mi in sorted(set(model["face_materials"])):
        slots[mi] = len(me.materials)
        me.materials.append(materials[mi])
    me.polygons.foreach_set("material_index", [slots[mi] for mi in model["face_materials"]])

    uv_layer = me.uv_layers.new(name="UVMap")
    loop_verts = [0] * len(me.loops)
    me.loops.foreach_get("vertex_index", loop_verts)
    flat = []
    for v in loop_verts:
        flat += (uv[2 * v], 1.0 - uv[2 * v + 1])  # в Source V идёт сверху вниз
    uv_layer.data.foreach_set("uv", flat)

    me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    me.validate(clean_customdata=False)
    if hasattr(me, "use_auto_smooth"):  # Blender < 4.1
        me.use_auto_smooth = True
    me.normals_split_custom_set_from_vertices([(n[i], n[i + 1], n[i + 2]) for i in range(0, len(n), 3)])
    me.update()

    obj = bpy.data.objects.new(model["name"], me)
    coll.objects.link(obj)
    obj.parent = arm_obj
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm_obj

    groups = {}
    for vi, influences in enumerate(model["weights"]):
        for bone, w in influences:
            vg = groups.get(bone)
            if vg is None:
                vg = groups[bone] = obj.vertex_groups.new(name=bone_names[bone])
            vg.add([vi], w, "REPLACE")

    build_shape_keys(obj, model.get("shape_keys", []), scale)
    return obj


def build_shape_keys(obj, keys, scale):
    if not keys:
        return
    me = obj.data
    obj.shape_key_add(name="Basis", from_mix=False)
    base = [0.0] * (len(me.vertices) * 3)
    me.vertices.foreach_get("co", base)
    for k in keys:
        co = base[:]
        d = k["deltas"]
        for j, vi in enumerate(k["indices"]):
            co[3 * vi] += d[3 * j] * scale
            co[3 * vi + 1] += d[3 * j + 1] * scale
            co[3 * vi + 2] += d[3 * j + 2] * scale
        sk = obj.shape_key_add(name=k["name"], from_mix=False)
        sk.data.foreach_set("co", co)
        sk.value = 0.0  # Blender 5.x создаёт ключ со значением 1.0
    me.shape_keys.use_relative = True
    log(f"  shape keys: {len(keys)}")


def _add_driver(owner, path, expr, var_map, arm_obj):
    drv = owner.driver_add(path).driver
    drv.type = "SCRIPTED"
    for name, prop in var_map.items():
        var = drv.variables.new()
        var.name = name
        var.type = "SINGLE_PROP"
        var.targets[0].id_type = "OBJECT"
        var.targets[0].id = arm_obj
        var.targets[0].data_path = f'["{prop}"]'
    drv.expression = expr
    return drv


def build_flex_controllers(flex, arm_obj, mesh_objs):
    """Слайдеры Face Poser: свойства арматуры fc.<имя> + драйверы на shape keys."""
    ui = []
    for c in flex["controllers"]:
        lo, hi = c["min"], c["max"]
        arm_obj[c["prop"]] = min(max(0.0, lo), hi)
        arm_obj.id_properties_ui(c["prop"]).update(min=lo, max=hi, soft_min=lo, soft_max=hi,
                                                  description=f"Flex-контроллер ({c['type']})")
        ui.append([c["prop"], c["name"], c["type"], lo, hi])
    arm_obj["gm2b_flex_ui"] = json.dumps(ui)

    for h in flex["helpers"]:
        arm_obj[h["prop"]] = 0.0
        _add_driver(arm_obj, f'["{h["prop"]}"]', h["expr"], h["vars"], arm_obj)

    not_simple = driven = 0
    for obj in mesh_objs:
        keys = obj.data.shape_keys
        if not keys:
            continue
        for name, d in flex["drivers"].items():
            kb = keys.key_blocks.get(name)
            if kb is None:
                continue
            kb.slider_min = -1.0
            drv = _add_driver(kb, "value", d["expr"], d["vars"], arm_obj)
            driven += 1
            not_simple += not drv.is_simple_expression
    log(f"flex-контроллеры: {len(flex['controllers'])}, драйверов: {driven}, "
        f"вспомогательных: {len(flex['helpers'])}, требуют Python: {not_simple}")


def main():
    job_path = sys.argv[sys.argv.index("--") + 1]
    with open(job_path, encoding="utf-8") as fh:
        job = json.load(fh)
    scale = job["scale"]

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene_coll = bpy.context.scene.collection
    coll = bpy.data.collections.new(job["name"])
    scene_coll.children.link(coll)

    log(f"кости: {len(job['bones'])}")
    arm_obj, bone_names = build_armature(job, coll, scale)

    families = len(job["slots"][0]) if job["slots"] else 1
    arm_obj["skin"] = job.get("skin", 0)
    arm_obj.id_properties_ui("skin").update(
        min=0, max=max(families - 1, 0), description="Номер скина модели (как skin в GMod)")

    # Один материал на слот; если скины меняют текстуру слота — материал с переключателем
    log(f"материалы: {len(job['slots'])} слотов, скинов: {families}")
    cache = {}
    materials = []
    switches = {}  # материал-переключатель -> варианты по скинам (для экспорта в FBX/glTF/OBJ)
    for slot in job["slots"]:
        key = tuple(slot) if len(set(slot)) > 1 else (slot[0],)
        if key not in cache:
            variants = [job["materials"][t] for t in key]
            name = variants[0]["name"] if len(key) == 1 else f"{variants[0]['name']}_skins"
            cache[key] = build_material(name, variants, arm_obj)
            if len(key) > 1:
                switches[cache[key]] = variants
        materials.append(cache[key])

    mesh_objs = []
    bodygroups = []  # для плагина: [имя, [объект варианта 0, варианта 1, …]]
    for bp in job["bodyparts"]:
        bp_coll = bpy.data.collections.new(bp["name"])
        coll.children.link(bp_coll)
        variants = []
        for i, model in enumerate(bp["models"]):
            if model.get("empty"):
                variants.append("")  # пустой вариант: «снять» часть модели
                continue
            log(f"меш: {bp['name']} / {model['name']}")
            obj = build_mesh(model, bp_coll, arm_obj, bone_names, materials, scale)
            mesh_objs.append(obj)
            variants.append(obj.name)
            if i > 0:  # в bodygroup видна только первая подмодель
                obj.hide_set(True)
                obj.hide_render = True
        bodygroups.append([bp["name"], variants])
    arm_obj["gm2b_bodygroups"] = json.dumps(bodygroups)

    if job.get("flex"):
        build_flex_controllers(job["flex"], arm_obj, mesh_objs)

    if "blend" in job.get("formats", ["blend"]):
        out = job["output"]
        if job.get("pack_textures"):
            bpy.ops.file.pack_all()
            bpy.ops.wm.save_as_mainfile(filepath=out)
        else:
            bpy.ops.wm.save_as_mainfile(filepath=out)
            bpy.ops.file.make_paths_relative()
            bpy.ops.wm.save_mainfile()
        log("сохранено: .blend")

    def flatten_skins():
        # Переключатель скинов на драйверах в FBX/glTF/OBJ не переносится —
        # подставляем обычный материал текущего скина
        skin = int(arm_obj["skin"])
        for mat, variants in switches.items():
            plain = build_material(mat.name[: -len("_skins")], [variants[min(skin, len(variants) - 1)]], None)
            for obj in mesh_objs:
                for slot in obj.material_slots:
                    if slot.material == mat:
                        slot.material = plain

    export_formats(job, prepare=flatten_skins)


def export_formats(job, prepare=None):
    """Дополнительные форматы рядом с .blend: FBX и glTF — с костями, весами и shape keys,
    OBJ — только геометрия и материалы. Экспортируется то, что видно (вариант bodygroup 0)."""
    import os

    formats = [f for f in job.get("formats", ["blend"]) if f != "blend"]
    if not formats:
        return
    if prepare:
        prepare()
    if any(i.packed_file for i in bpy.data.images):
        # OBJ копирует текстуры с диска; упакованные в .blend картинки возвращаем в файлы
        bpy.ops.file.unpack_all(method="USE_ORIGINAL")
    base = os.path.splitext(job["output"])[0]
    for fmt in formats:
        path = f"{base}.{fmt}"
        if fmt == "fbx":
            bpy.ops.export_scene.fbx(filepath=path, use_visible=True, object_types={"ARMATURE", "MESH"},
                                     add_leaf_bones=False, bake_anim=False,
                                     path_mode="COPY", embed_textures=True)
        elif fmt == "glb":
            bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_visible=True,
                                      export_animations=False)
        elif fmt == "obj":
            bpy.ops.wm.obj_export(filepath=path, export_materials=True, path_mode="COPY",
                                  apply_modifiers=True, export_selected_objects=False)
        log(f"сохранено: .{fmt}")


if __name__ == "__main__":
    main()
