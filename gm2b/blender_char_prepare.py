"""Запускается ВНУТРИ Blender. Шаг 1 мастера «Свой персонаж»:
    blender --background --python blender_char_prepare.py -- job.json

  • импортирует модель, собирает все видимые меши в один объект «Body»
  • поворачивает лицом к −Y (как эталонный скелет ValveBiped), ставит ногами на пол,
    масштабирует под заданный рост
  • рендерит виды спереди и сбоку для расстановки точек
  • угадывает начальные положения точек (подбородок, запястья, локти, колени, пах)
"""
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from blender_to_source import import_any, log  # noqa: E402


def join_meshes():
    """Все видимые меши (с модификаторами, в позе покоя) -> один объект Body."""
    for arm in [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]:
        arm.data.pose_position = "REST"  # если у модели уже есть риг — берём позу покоя
    dg = bpy.context.evaluated_depsgraph_get()
    parts = []
    for o in list(bpy.context.scene.objects):
        if o.type not in ("MESH", "CURVE", "SURFACE", "META", "FONT") or not o.visible_get():
            continue
        me = bpy.data.meshes.new_from_object(o.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
        me.transform(o.matrix_world)
        part = bpy.data.objects.new(o.name, me)
        parts.append(part)
    if not parts:
        raise RuntimeError("в файле нет видимых мешей")
    # чистая сцена только с новыми объектами
    for o in list(bpy.context.scene.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for p in parts:
        bpy.context.scene.collection.objects.link(p)
    bpy.ops.object.select_all(action="DESELECT")
    for p in parts:
        p.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    if len(parts) > 1:
        bpy.ops.object.join()
    body = bpy.context.view_layer.objects.active
    body.name = body.data.name = "Body"
    return body


def normalize(body, rotate_deg, height_m):
    me = body.data
    me.transform(Matrix.Rotation(math.radians(rotate_deg), 4, "Z"))
    xs = [v.co.x for v in me.vertices]
    ys = [v.co.y for v in me.vertices]
    zs = [v.co.z for v in me.vertices]
    h = max(zs) - min(zs)
    if h <= 0:
        raise RuntimeError("у модели нулевая высота")
    k = height_m / h
    center = Vector(((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2, min(zs)))
    me.transform(Matrix.Scale(k, 4) @ Matrix.Translation(-center))
    me.update()


def render_view(body, path, side=False, res=(760, 1000)):
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.display.shading.light = "STUDIO"
    sc.display.shading.color_type = "TEXTURE"
    sc.display.shading.show_cavity = True
    sc.render.film_transparent = True
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    bb = [body.matrix_world @ Vector(c) for c in body.bound_box]
    lo = Vector([min(p[i] for p in bb) for i in range(3)])
    hi = Vector([max(p[i] for p in bb) for i in range(3)])
    center = (lo + hi) / 2
    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = "ORTHO"
    # ortho_scale покрывает большую сторону кадра; берём с запасом по высоте и ширине
    width = (hi.y - lo.y) if side else (hi.x - lo.x)
    upp_needed = max((hi.z - lo.z) / res[1], width / res[0]) * 1.08
    cam_data.ortho_scale = upp_needed * max(res)
    cam = bpy.data.objects.new("cam", cam_data)
    sc.collection.objects.link(cam)
    sc.camera = cam
    dist = 20 * (hi - lo).length
    if side:  # вид слева: камера на −X смотрит в +X
        cam.location = (center.x - dist, center.y, center.z)
        cam.rotation_euler = (math.radians(90), 0, math.radians(-90))
    else:  # вид спереди: камера на −Y смотрит в +Y
        cam.location = (center.x, center.y - dist, center.z)
        cam.rotation_euler = (math.radians(90), 0, 0)
    cam_data.clip_end = dist * 3
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    upp = cam_data.ortho_scale / max(res)
    mapping = {"cx": center.y if side else center.x, "cz": center.z, "upp": upp, "w": res[0], "h": res[1]}
    bpy.data.objects.remove(cam, do_unlink=True)
    return mapping


DEPTH_STRIDE = 8  # шаг сетки глубины в пикселях вида спереди
FRONT_RES = (1520, 2000)  # виды в высоком разрешении — чтобы при приближении точки ставились точно
SIDE_RES = (1000, 2000)


def depth_grid(body, front):
    """Для каждой точки вида спереди: середина тела по глубине (y) или NaN, если там пусто.

    Нужна программе, чтобы ставить точки на виде сбоку по умолчанию и искать силуэт."""
    import numpy as np
    from mathutils.bvhtree import BVHTree

    bvh = BVHTree.FromObject(body, bpy.context.evaluated_depsgraph_get())
    w, h = front["w"] // DEPTH_STRIDE, front["h"] // DEPTH_STRIDE
    grid = np.full((h, w), np.nan, dtype=np.float32)
    far = 100.0
    for gy in range(h):
        z = front["cz"] - ((gy + 0.5) * DEPTH_STRIDE - front["h"] / 2) * front["upp"]
        for gx in range(w):
            x = front["cx"] + ((gx + 0.5) * DEPTH_STRIDE - front["w"] / 2) * front["upp"]
            a = bvh.ray_cast(Vector((x, -far, z)), Vector((0, 1, 0)))[0]
            if a is None:
                continue
            b = bvh.ray_cast(Vector((x, far, z)), Vector((0, -1, 0)))[0]
            grid[gy, gx] = (a.y + b.y) / 2 if b is not None else a.y
    return grid


def guess_markers(body, H, front, grid):
    """Начальные точки по силуэту — пользователь их потом поправит."""
    import numpy as np

    vs = [v.co for v in body.data.vertices]
    mask = ~np.isnan(grid)
    gh, gw = mask.shape
    xs = front["cx"] + ((np.arange(gw) + 0.5) * DEPTH_STRIDE - front["w"] / 2) * front["upp"]

    def row(z):
        gy = int((front["h"] / 2 - (z - front["cz"]) / front["upp"]) / DEPTH_STRIDE)
        return mask[min(max(gy, 0), gh - 1)]

    def centroid(z, sign, limit):
        r = row(z)
        sel = r & (xs * sign > 0.005 * H) & (np.abs(xs) < limit)
        return float(xs[sel].mean()) if sel.any() else sign * 0.06 * H

    # развилка ног: снизу вверх — где центральная полоса впервые сплошная по всей ширине
    # (одиночная прядь волос между ногами не считается)
    groin_z = 0.47 * H
    band = np.abs(xs) < 0.035 * H
    for z in np.arange(0.12 * H, 0.62 * H, 0.004 * H):
        if row(z)[band].all():
            groin_z = float(z) if 0.3 * H < z < 0.6 * H else groin_z
            break
    ankle_z = 0.055 * H
    knee_z = ankle_z + 0.55 * (groin_z - ankle_z)
    chin_z = 0.845 * H
    shoulder_z = chin_z - 0.075 * H

    out = {"chin": [0.0, chin_z], "neck": [0.0, chin_z - 0.04 * H], "groin": [0.0, groin_z]}
    for sign, s in ((-1, "r"), (1, "l")):  # правая сторона персонажа — на −X
        arm = [v for v in vs if v.x * sign > 0.14 * H and 0.3 * H < v.z < 0.95 * H]
        tip = max(arm, key=lambda v: v.x * sign) if arm else Vector((sign * 0.35 * H, 0, 0.55 * H))
        shoulder = Vector((sign * 0.09 * H, 0, shoulder_z))
        wrist = tip + (shoulder - tip).normalized() * 0.085 * H
        elbow = shoulder + (wrist - shoulder) * 0.5
        out[f"shoulder_{s}"] = [shoulder.x, shoulder.z]
        out[f"elbow_{s}"] = [elbow.x, elbow.z]
        out[f"wrist_{s}"] = [wrist.x, wrist.z]
        out[f"fingertip_{s}"] = [tip.x, tip.z]
        out[f"thumb_{s}"] = thumb_tip(vs, wrist, tip)
        knee_x = centroid(knee_z, sign, 0.2 * H)
        ankle_x = centroid(ankle_z, sign, 0.2 * H)
        out[f"hip_{s}"] = [sign * max(abs(knee_x), 0.05 * H), groin_z + 0.035 * H]
        out[f"knee_{s}"] = [knee_x, knee_z]
        out[f"ankle_{s}"] = [ankle_x, ankle_z]
        out[f"toe_{s}"] = toe_tip(vs, ankle_x, ankle_z, H)
    return out


def thumb_tip(vs, wrist, tip):
    """Кончик большого пальца: вершина кисти, дальше всех отстоящая от оси «запястье → кончики»."""
    axis = tip - wrist
    length = axis.length or 1e-6
    axis = axis / length
    best, best_d = None, -1.0
    for v in vs:
        rel = v - wrist
        along = rel.dot(axis)
        if 0.1 * length < along < 0.75 * length and rel.length < 1.1 * length:
            d = (rel - axis * along).length
            if d > best_d:
                best, best_d = v, d
    p = best or (wrist + (tip - wrist) * 0.4)
    return [p.x, p.z, p.y]  # глубина известна по самой вершине


def toe_tip(vs, ankle_x, ankle_z, H):
    """Носок: на 70% пути от щиколотки к самой передней (−Y) вершине стопы."""
    foot = [v for v in vs if v.z < ankle_z + 0.01 * H and abs(v.x - ankle_x) < 0.06 * H]
    if not foot:
        return [ankle_x, 0.012 * H, -0.05 * H]
    tip = min(foot, key=lambda v: v.y)
    heel_y = sum(v.y for v in foot) / len(foot)
    return [ankle_x + (tip.x - ankle_x) * 0.7, 0.012 * H, heel_y + (tip.y - heel_y) * 0.7]


def main():
    job = json.load(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8"))
    out = job["out_dir"]
    import_any(job["input"])
    body = join_meshes()
    normalize(body, job.get("rotate", 0), job["height_m"])
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, "char.blend"))

    import numpy as np
    from blender_to_source import describe_material, safe

    front = render_view(body, os.path.join(out, "front.png"), res=FRONT_RES)
    side_map = render_view(body, os.path.join(out, "side.png"), side=True, res=SIDE_RES)
    grid = depth_grid(body, front)
    np.save(os.path.join(out, "depth.npy"), grid)
    me = body.data
    # материалы с текстурами, которые нашёл импорт (остальным можно назначить вручную)
    used, materials = set(), []
    mats_dir = os.path.join(out, "materials")
    os.makedirs(mats_dir, exist_ok=True)
    for mat in (me.materials or [None]):
        materials.append(describe_material(mat, safe(mat.name if mat else "body", used), mats_dir))
    info = {
        "front": front, "side": side_map, "height_m": job["height_m"], "depth_stride": DEPTH_STRIDE,
        "vertices": len(me.vertices), "triangles": sum(len(p.vertices) - 2 for p in me.polygons),
        "materials": materials, "markers": guess_markers(body, job["height_m"], front, grid),
    }
    with open(os.path.join(out, "prepare.json"), "w", encoding="utf-8") as f:
        json.dump(info, f)
    with_tex = sum(1 for m in materials if m["base"])
    log(f"модель готова: {info['vertices']} вершин, {info['triangles']} треугольников, "
        f"текстуры найдены у {with_tex} из {len(materials)} материалов")


if __name__ == "__main__":
    main()
