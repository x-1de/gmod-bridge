"""Запускается ВНУТРИ Blender:
    blender --background --python blender_to_source.py -- job.json

Открывает модель (FBX/OBJ/glTF/BLEND/STL/PLY) и пишет исходники для studiomdl:
  ref.smd       — геометрия (один корневой кость, статичный проп)
  phys.smd      — выпуклая оболочка для физики
  materials.json — для каждого материала: PNG базового цвета, нормалей, прозрачность
"""
import json
import os
import re
import sys

import bmesh
import bpy
from mathutils import Matrix


def log(msg):
    print(f"GM2B: {msg}", flush=True)


def import_any(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".blend":
        bpy.ops.wm.open_mainfile(filepath=path)
        return
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    elif ext == ".stl":
        bpy.ops.wm.stl_import(filepath=path)
    elif ext == ".ply":
        bpy.ops.wm.ply_import(filepath=path)
    else:
        raise RuntimeError(f"формат {ext} не поддерживается")


def safe(name, used):
    base = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "material"
    out, i = base, 1
    while out in used:
        i += 1
        out = f"{base}_{i}"
    used.add(out)
    return out


def upstream_image(socket, depth=0):
    """Первая картинка выше по нодам от входа шейдера (через Mix, Hue/Sat и т.п.)."""
    if not socket.is_linked or depth > 8:
        return None
    node = socket.links[0].from_node
    if node.type == "TEX_IMAGE" and node.image:
        return node.image
    for inp in node.inputs:
        img = upstream_image(inp, depth + 1)
        if img:
            return img
    return None


def save_png(img, path):
    img = img.copy()  # не трогаем оригинал (путь, формат)
    img.file_format = "PNG"
    img.save(filepath=path)
    bpy.data.images.remove(img)


def describe_material(mat, name, out_dir):
    info = {"name": name, "base": None, "normal": None, "alpha": "OPAQUE", "color": [0.8, 0.8, 0.8]}
    if mat is None:
        return info
    bsdf = None
    if mat.node_tree:
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        info["color"] = list(mat.diffuse_color[:3])
        return info
    base_in = bsdf.inputs["Base Color"]
    info["color"] = list(base_in.default_value[:3])
    img = upstream_image(base_in)
    if img:
        info["base"] = os.path.join(out_dir, f"{name}.png")
        save_png(img, info["base"])
    nrm = bsdf.inputs["Normal"]
    if nrm.is_linked and nrm.links[0].from_node.type == "NORMAL_MAP":
        nimg = upstream_image(nrm.links[0].from_node.inputs["Color"])
        if nimg:
            info["normal"] = os.path.join(out_dir, f"{name}_normal.png")
            save_png(nimg, info["normal"])
    method = getattr(mat, "surface_render_method", None) or getattr(mat, "blend_method", "OPAQUE")
    if bsdf.inputs["Alpha"].is_linked:
        info["alpha"] = "BLEND" if method in ("BLENDED", "BLEND") else "CLIP"
    return info


def fmt_vertex(p, n, uv):
    return (f"0 {p.x:.6f} {p.y:.6f} {p.z:.6f} {n.x:.6f} {n.y:.6f} {n.z:.6f} "
            f"{uv[0]:.6f} {uv[1]:.6f} 1 0 1.000000")


SMD_HEADER = ["version 1", "nodes", '0 "root" -1', "end", "skeleton", "time 0",
              "0 0.000000 0.000000 0.000000 0.000000 0.000000 0.000000", "end", "triangles"]


def main():
    job = json.load(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8"))
    out_dir = job["out_dir"]
    import_any(job["input"])

    root = Matrix.Scale(job["scale"], 4)
    if job.get("rotate"):
        root = Matrix.Rotation(job["rotate"], 4, "Z") @ root

    dg = bpy.context.evaluated_depsgraph_get()
    objs = [o for o in bpy.context.scene.objects if o.type in ("MESH", "CURVE", "SURFACE", "META", "FONT")
            and o.visible_get()]
    if not objs:
        raise RuntimeError("в файле нет видимых мешей")

    used, mat_names, materials = set(), {}, []
    lines = list(SMD_HEADER)
    hull_points = []
    tris = 0
    for o in objs:
        eo = o.evaluated_get(dg)
        me = eo.to_mesh()
        me.calc_loop_triangles()
        mw = root @ o.matrix_world
        nm = mw.to_3x3().inverted_safe().transposed()
        uv = me.uv_layers.active.data if me.uv_layers.active else None
        corner = me.corner_normals if hasattr(me, "corner_normals") else None
        if corner is None:
            me.calc_normals_split()
        slot_names = []
        for slot in (o.material_slots or [None]):
            mat = slot.material if slot else None
            key = mat.name if mat else "__none__"
            if key not in mat_names:
                mat_names[key] = safe(mat.name if mat else "default", used)
                materials.append(describe_material(mat, mat_names[key], out_dir))
            slot_names.append(mat_names[key])
        for v in me.vertices:
            hull_points.append(mw @ v.co)
        for t in me.loop_triangles:
            lines.append(slot_names[min(t.material_index, len(slot_names) - 1)])
            for li in t.loops:
                p = mw @ me.vertices[me.loops[li].vertex_index].co
                n = (nm @ (corner[li].vector if corner is not None else me.loops[li].normal)).normalized()
                lines.append(fmt_vertex(p, n, uv[li].uv if uv else (0.0, 0.0)))
            tris += 1
        eo.to_mesh_clear()
    lines.append("end")
    with open(os.path.join(out_dir, "ref.smd"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # Физика: выпуклая оболочка всех вершин (движку нужны выпуклые куски)
    bm = bmesh.new()
    for p in hull_points:
        bm.verts.new(p)
    res = bmesh.ops.convex_hull(bm, input=bm.verts)
    leftovers = {*res["geom_interior"], *res["geom_unused"]}  # списки пересекаются
    bmesh.ops.delete(bm, geom=[g for g in leftovers if g.is_valid], context="VERTS")
    bmesh.ops.triangulate(bm, faces=bm.faces)
    bm.normal_update()
    phys = list(SMD_HEADER)
    for f in bm.faces:
        phys.append("phys")
        for v in f.verts:
            phys.append(fmt_vertex(v.co, f.normal, (0.0, 0.0)))
    phys.append("end")
    with open(os.path.join(out_dir, "phys.smd"), "w", encoding="utf-8") as f:
        f.write("\n".join(phys) + "\n")

    xs = [p.x for p in hull_points]
    ys = [p.y for p in hull_points]
    zs = [p.z for p in hull_points]
    info = {
        "materials": materials, "triangles": tris, "hull_faces": len(bm.faces),
        "size": [max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)],
    }
    bm.free()
    with open(os.path.join(out_dir, "source.json"), "w", encoding="utf-8") as f:
        json.dump(info, f)
    log(f"треугольников: {tris}, материалов: {len(materials)}, физика: {info['hull_faces']} граней")


if __name__ == "__main__":
    main()
