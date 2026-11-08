"""Запускается ВНУТРИ Blender. Шаг 2–3 мастера «Свой персонаж»:
    blender --background --python blender_char_rig.py -- job.json

По точкам (как в Mixamo) строит скелет ValveBiped, привязывает к нему меш
автоматическими весами, рендерит тестовую позу и пишет исходники для studiomdl:
  body.smd  — меш с костями и весами
  phys.smd  — рэгдолл: по выпуклой форме на каждую часть тела
  rig.json  — суставы для превью, атрибуты, материалы, статистика
"""
import json
import math
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector
from mathutils.bvhtree import BVHTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from blender_to_source import describe_material, fmt_vertex, log, safe  # noqa: E402,F401

INCH = 0.0254
VB = "ValveBiped."
# кость -> её «продолжение»: направление кости = на этот сустав
LEN_CHILD = {
    "Bip01_Spine": "Bip01_Spine1", "Bip01_Spine1": "Bip01_Spine2", "Bip01_Spine2": "Bip01_Spine4",
    "Bip01_Spine4": "Bip01_Neck1", "Bip01_Neck1": "Bip01_Head1",
    "Bip01_R_Clavicle": "Bip01_R_UpperArm", "Bip01_R_UpperArm": "Bip01_R_Forearm", "Bip01_R_Forearm": "Bip01_R_Hand",
    "Bip01_L_Clavicle": "Bip01_L_UpperArm", "Bip01_L_UpperArm": "Bip01_L_Forearm", "Bip01_L_Forearm": "Bip01_L_Hand",
    "Bip01_R_Thigh": "Bip01_R_Calf", "Bip01_R_Calf": "Bip01_R_Foot", "Bip01_R_Foot": "Bip01_R_Toe0",
    "Bip01_L_Thigh": "Bip01_L_Calf", "Bip01_L_Calf": "Bip01_L_Foot", "Bip01_L_Foot": "Bip01_L_Toe0",
}
HELPERS = {"forward", "Anim_Attachment_RH", "Anim_Attachment_LH"}  # не деформируют меш
# Позвоночник, шея и голова получают ориентацию эталона как есть, без доворота к модели:
# иначе при переводе в эталонную позу голова наклоняется (у HL2 шея наклонена вперёд)
# и персонаж в игре смотрит в пол. Доворачиваются только конечности и ключицы.
TORSO = {"Bip01_Pelvis", "Bip01_Spine", "Bip01_Spine1", "Bip01_Spine2", "Bip01_Spine4",
         "Bip01_Neck1", "Bip01_Head1"}
# кость без своей формы в рэгдолле -> к какой форме относятся её вершины
REGION = {"Bip01_Spine": "Bip01_Pelvis", "Bip01_Spine1": "Bip01_Spine2", "Bip01_Spine4": "Bip01_Spine2",
          "Bip01_Neck1": "Bip01_Head1", "Bip01_R_Clavicle": "Bip01_Spine2", "Bip01_L_Clavicle": "Bip01_Spine2",
          "Bip01_R_Toe0": "Bip01_R_Foot", "Bip01_L_Toe0": "Bip01_L_Foot"}


def short(name):
    return name[len(VB):] if name.startswith(VB) else name


def std_world(bones):
    """Мировые матрицы эталонного скелета (дюймы)."""
    world = []
    for b in bones:
        x, y, z, w = b["quat"]
        local = Matrix.Translation(Vector(b["pos"])) @ Quaternion((w, x, y, z)).to_matrix().to_4x4()
        world.append(world[b["parent"]] @ local if b["parent"] >= 0 else local)
    return world


def make_depth(body):
    """y-координата середины тела в точке (x, z): луч спереди и сзади, как у Mixamo."""
    dg = bpy.context.evaluated_depsgraph_get()
    bvh = BVHTree.FromObject(body, dg)
    big = 100.0

    def depth(x, z):
        front = bvh.ray_cast(Vector((x, -big, z)), Vector((0, 1, 0)))[0]
        back = bvh.ray_cast(Vector((x, big, z)), Vector((0, -1, 0)))[0]
        if front and back:
            return (front.y + back.y) / 2
        near = bvh.find_nearest(Vector((x, 0, z)))[0]
        return near.y if near else 0.0

    return depth, bvh


STD_HAND_LENGTH = 7.8 * INCH  # от сустава кисти до кончика среднего пальца у эталона


def is_finger(n):
    return "_Finger" in n


def build_joints(markers, bones, W_std, H, depth, verts):
    """Положения суставов (метры) по точкам пользователя.

    Точка = [x, z] с вида спереди и, если задана, y — глубина с вида сбоку.
    Без глубины сустав ставится посередине тела (луч спереди и сзади, как у Mixamo)."""
    names = [short(b["name"]) for b in bones]
    P_std = {n: W_std[i].to_translation() * INCH for i, n in enumerate(names)}

    def mk(key, dz=0.0):
        v = markers[key]
        x, z = v[0], v[1] + dz
        y = v[2] if len(v) > 2 and v[2] is not None else depth(x, z)
        return Vector((x, y, z))

    def at(x, z):
        return Vector((x, depth(x, z), z))

    P = {}
    P["Bip01_Pelvis"] = mk("groin", 0.055 * H)
    P["Bip01_Head1"] = mk("chin", 0.035 * H)
    pelvis, head = P["Bip01_Pelvis"], P["Bip01_Head1"]
    zp, zh = P_std["Bip01_Pelvis"].z, P_std["Bip01_Head1"].z
    s_torso = (head.z - pelvis.z) / (zh - zp)
    if "neck" in markers:
        neck = P["Bip01_Neck1"] = mk("neck")
        zn = P_std["Bip01_Neck1"].z
        # голова всегда выше шеи: у мультяшных персонажей челюсть бывает ниже плеч,
        # и сустав головы по подбородку оказался бы под шеей
        if head.z < neck.z + 0.03 * H:
            head.z = neck.z + 0.03 * H
            s_torso = (head.z - pelvis.z) / (zh - zp)
        chain = ("Bip01_Spine", "Bip01_Spine1", "Bip01_Spine2", "Bip01_Spine4")
    else:
        neck, zn = head, zh
        chain = ("Bip01_Spine", "Bip01_Spine1", "Bip01_Spine2", "Bip01_Spine4", "Bip01_Neck1")
    for n in chain:  # позвоночник — между тазом и шеей, в пропорциях эталона
        t = (P_std[n].z - zp) / (zn - zp)
        P[n] = at(pelvis.x + (neck.x - pelvis.x) * t, pelvis.z + t * (neck.z - pelvis.z))
    extra = {}
    s_hand = {}
    for side, s in (("R", "r"), ("L", "l")):
        shoulder = mk(f"shoulder_{s}")
        spine4 = P["Bip01_Spine4"]
        # ключица — от груди к плечу, чуть выше плечевого сустава (как у эталона)
        P[f"Bip01_{side}_Clavicle"] = Vector((spine4.x + (shoulder.x - spine4.x) * 0.16,
                                              (spine4.y + shoulder.y) / 2, shoulder.z + 0.012 * H))
        P[f"Bip01_{side}_UpperArm"] = shoulder
        P[f"Bip01_{side}_Forearm"] = mk(f"elbow_{s}")
        wrist = P[f"Bip01_{side}_Hand"] = mk(f"wrist_{s}")
        knee = P[f"Bip01_{side}_Calf"] = mk(f"knee_{s}")
        ankle = P[f"Bip01_{side}_Foot"] = mk(f"ankle_{s}")
        if f"hip_{s}" in markers:
            P[f"Bip01_{side}_Thigh"] = mk(f"hip_{s}")
        else:
            off_x = (P_std[f"Bip01_{side}_Thigh"] - P_std["Bip01_Pelvis"]).x * s_torso
            P[f"Bip01_{side}_Thigh"] = Vector((0.65 * knee.x + 0.35 * (pelvis.x + off_x), pelvis.y, pelvis.z))
        P[f"Bip01_{side}_Toe0"] = mk(f"toe_{s}") if f"toe_{s}" in markers else toe_joint(verts, ankle, H)
        if f"fingertip_{s}" in markers:
            extra[f"fingertip_{side}"] = mk(f"fingertip_{s}")
            length = (extra[f"fingertip_{side}"] - wrist).length
        else:  # длина кисти по самой модели: самая дальняя вершина вдоль предплечья
            d = (wrist - P[f"Bip01_{side}_Forearm"]).normalized()
            reach = [(v - wrist).dot(d) for v in verts if (v - wrist).length < 0.15 * H
                     and ((v - wrist) - d * (v - wrist).dot(d)).length < 0.05 * H]
            length = max([r for r in reach if r > 0], default=STD_HAND_LENGTH)
        if f"thumb_{s}" in markers:
            extra[f"thumb_{side}"] = mk(f"thumb_{s}")
        s_hand[side] = min(max(length / STD_HAND_LENGTH, 0.5), 2.0)
    return P, P_std, s_torso, s_hand, extra


def toe_joint(verts, ankle, H):
    """Носок: самая передняя (−Y) вершина стопы, сустав — на 70% пути от щиколотки к ней."""
    foot = [v for v in verts if v.z < ankle.z + 0.01 * H and abs(v.x - ankle.x) < 0.06 * H]
    if not foot:
        return ankle + Vector((0, -0.07 * H, -ankle.z * 0.8))
    tip = min(foot, key=lambda v: v.y)
    return Vector((ankle.x + (tip.x - ankle.x) * 0.7, ankle.y + (tip.y - ankle.y) * 0.7, 0.012 * H))


def hand_swing(n, R_std, P, P_std, extra):
    """Поворот кисти: направление — на кончики пальцев, поворот вокруг оси — по большому пальцу.

    Именно поворот вокруг оси решает, куда смотрит ладонь, а значит — как лягут пальцы на оружие."""
    side = n.split("_")[1]
    tip = extra.get(f"fingertip_{side}")
    if tip is None:
        return None
    axis = (tip - P[n]).normalized()
    q = R_std.col[0].normalized().rotation_difference(axis)
    thumb, std_thumb = extra.get(f"thumb_{side}"), P_std.get(f"Bip01_{side}_Finger0")
    if thumb is not None and std_thumb is not None:
        u_std = q @ (std_thumb - P_std[n])
        u_std -= axis * u_std.dot(axis)
        u = thumb - P[n]
        u -= axis * u.dot(axis)
        hand_len = (tip - P[n]).length
        if u.length < 0.15 * hand_len or u_std.length < 1e-5:
            # большой палец почти на оси кисти — по нему не понять, куда смотрит ладонь
            ROLL_LOG[side] = "пропущен: большой палец слишком близко к оси кисти"
            return q
        angle = math.atan2(axis.dot(u_std.cross(u)), u_std.dot(u))
        if abs(math.degrees(angle)) > 60:
            # у ладони в T/A-позе поворот небольшой; больше 60° — почти наверняка точка
            # большого пальца стоит не там (например, на предмете в руке)
            ROLL_LOG[side] = f"пропущен: {math.degrees(angle):+.0f}° — проверьте точку большого пальца"
            return q
        q = Quaternion(axis, angle) @ q
        ROLL_LOG[side] = f"{math.degrees(angle):+.0f}°"
    return q


ROLL_LOG = {}


def build_skeleton(bones, W_std, P, P_std, s_torso, s_hand, extra=None):
    """Мировые матрицы наших костей (метры): ориентация эталона, довёрнутая к реальной конечности."""
    extra = extra or {}
    names = [short(b["name"]) for b in bones]
    W, swing = [], {}
    for i, b in enumerate(bones):
        n = names[i]
        R_std = W_std[i].to_3x3()
        if n in HELPERS or is_finger(n):
            # вспомогательные кости и пальцы — локальная позиция эталона, масштаб по телу / кисти
            parent = W[b["parent"]]
            x, y, z, w = b["quat"]
            scale = s_hand["R" if "_R_" in n else "L"] if is_finger(n) else s_torso
            local = Matrix.Translation(Vector(b["pos"]) * INCH * scale) @ \
                Quaternion((w, x, y, z)).to_matrix().to_4x4()
            W.append(parent @ local)
            continue
        if n in TORSO:
            q = Quaternion()  # таз, позвоночник, шея, голова — ориентация эталона
        elif n.endswith("_Hand") and hand_swing(n, R_std, P, P_std, extra) is not None:
            q = hand_swing(n, R_std, P, P_std, extra)
        elif n in LEN_CHILD:
            c = LEN_CHILD[n]
            q = (P_std[c] - P_std[n]).normalized().rotation_difference((P[c] - P[n]).normalized())
        else:
            q = swing[names[b["parent"]]]  # концевые кости (кисть, носок) — как у родителя
        swing[n] = q
        W.append(Matrix.Translation(P[n]) @ (q.to_matrix() @ R_std).to_4x4())
    return W


def make_armature(bones, W, P, H):
    names = [short(b["name"]) for b in bones]
    arm = bpy.data.armatures.new("Skeleton")
    obj = bpy.data.objects.new("Skeleton", arm)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    tails = {
        "Bip01_Pelvis": P["Bip01_Pelvis"] + Vector((0, 0, 0.06 * H)),
        "Bip01_Head1": P["Bip01_Head1"] + Vector((0, 0, 0.12 * H)),
    }
    fingers = any(is_finger(n) for n in names)
    for side in ("R", "L"):
        h, f = P[f"Bip01_{side}_Hand"], P[f"Bip01_{side}_Forearm"]
        # с пальцами кость кисти — только ладонь, иначе — вся кисть целиком
        tails[f"Bip01_{side}_Hand"] = h + (h - f).normalized() * (0.035 if fingers else 0.09) * H
        t, ft = P[f"Bip01_{side}_Toe0"], P[f"Bip01_{side}_Foot"]
        tails[f"Bip01_{side}_Toe0"] = t + (t - ft).normalized() * 0.04 * H
    children = {}
    for i, b in enumerate(bones):
        if b["parent"] >= 0:
            children.setdefault(b["parent"], []).append(i)
    for i, b in enumerate(bones):
        n = names[i]
        eb = arm.edit_bones.new(b["name"])
        head = W[i].to_translation()
        if n in LEN_CHILD:
            tail = P[LEN_CHILD[n]]
        elif n in tails:
            tail = tails[n]
        elif is_finger(n):
            kids = [k for k in children.get(i, []) if is_finger(names[k])]
            if kids:
                tail = W[kids[0]].to_translation()
            else:  # кончик пальца: продолжение предыдущей фаланги
                prev = W[b["parent"]].to_translation()
                tail = head + (head - prev).normalized() * max((head - prev).length * 0.8, 0.004 * H)
        else:
            tail = head + W[i].to_3x3().col[0] * 0.02 * H
        eb.head, eb.tail = head, tail
        eb.use_deform = n not in HELPERS
        if b["parent"] >= 0:
            eb.parent = arm.edit_bones[bones[b["parent"]]["name"]]
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def weight_mesh(body, arm_obj, H):
    """Автоматические веса Blender (heat) на «сшитой» копии меша + перенос на оригинал.

    Игровые модели разрезаны по швам UV (сотни кусков, тысячи незамкнутых рёбер) — на таком
    меше heat-весование падает. На копии со сваренными вершинами оно работает, а веса
    переносятся обратно по совпадающим позициям, не трогая UV и текстуры."""
    from mathutils.kdtree import KDTree

    # Группы вершин от старого рига модели (если она уже была с костями) нам не нужны:
    # их веса иначе засчитываются как «есть вес», а к нашим костям вершина не привязана.
    body.vertex_groups.clear()
    proxy_me = body.data.copy()
    bm = bmesh.new()
    bm.from_mesh(proxy_me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0003 * H)
    # вырожденные грани и висящие рёбра ломают систему уравнений heat-весов для всего меша
    bmesh.ops.dissolve_degenerate(bm, dist=0.0001 * H, edges=bm.edges)
    loose = [v for v in bm.verts if not v.link_faces]
    bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bm.to_mesh(proxy_me)
    bm.free()
    proxy = bpy.data.objects.new("WeightProxy", proxy_me)
    bpy.context.scene.collection.objects.link(proxy)
    bpy.ops.object.select_all(action="DESELECT")
    proxy.select_set(True)
    arm_obj.select_set(True)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")

    bone_names = {b.name for b in arm_obj.data.bones if b.use_deform}
    names = {vg.index: vg.name for vg in proxy.vertex_groups}
    proxy_w = [[(names[g.group], g.weight) for g in v.groups if g.weight > 1e-4 and names[g.group] in bone_names]
               for v in proxy_me.vertices]
    kd = KDTree(len(proxy_me.vertices))
    for v in proxy_me.vertices:
        kd.insert(v.co, v.index)
    kd.balance()

    deform = [b for b in arm_obj.data.bones if b.use_deform]
    groups = {b.name: body.vertex_groups.new(name=b.name) for b in deform}
    auto = fallback = 0
    for v in body.data.vertices:
        _co, idx, _d = kd.find(v.co)
        weights = proxy_w[idx]
        if sum(w for _, w in weights) > 1e-4:
            auto += 1
        else:
            # мягкий запасной вариант: две ближайшие кости, вес обратно пропорционален расстоянию
            d = sorted((_dist_to_segment(v.co, b.head_local, b.tail_local), b.name) for b in deform)[:2]
            inv = [(n, 1.0 / max(dist, 1e-4) ** 4) for dist, n in d]
            total = sum(w for _, w in inv)
            weights = [(n, w / total) for n, w in inv]
            fallback += 1
        for n, w in weights:
            if n in groups:
                groups[n].add([v.index], w, "REPLACE")
    bpy.data.objects.remove(proxy, do_unlink=True)

    body.parent = arm_obj
    mod = body.modifiers.new("Armature", "ARMATURE")
    mod.object = arm_obj
    bpy.ops.object.select_all(action="DESELECT")
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    # мягче переходы на сгибах (локти, колени, плечи) и между «автоматическими» и запасными весами
    smooth_weights(body)
    # Source: не больше 3 костей на вершину, сумма весов = 1
    bpy.ops.object.vertex_group_limit_total(group_select_mode="ALL", limit=3)
    bpy.ops.object.vertex_group_normalize_all(group_select_mode="ALL", lock_active=False)
    return auto, fallback


def apply_textures(me, overrides):
    """Текстуры, назначенные пользователем вручную: подключаются в материалы до рендеров и экспорта.

    overrides: {имя материала: {"base": путь, "normal": путь}}; имена — как в prepare.json."""
    used, count = set(), 0
    for mat in me.materials:
        name = safe(mat.name if mat else "body", used)
        o = overrides.get(name)
        if not o or mat is None:
            continue
        if mat.node_tree is None:
            mat.use_nodes = True
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            bsdf = nodes.new("ShaderNodeBsdfPrincipled")
            out = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None) or \
                nodes.new("ShaderNodeOutputMaterial")
            links.new(bsdf.outputs[0], out.inputs["Surface"])
        if o.get("base"):
            node = nodes.new("ShaderNodeTexImage")
            node.image = bpy.data.images.load(o["base"], check_existing=True)
            links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
            nodes.active = node  # Workbench в рендерах показывает активную текстуру
            count += 1
        if o.get("normal"):
            node = nodes.new("ShaderNodeTexImage")
            node.image = bpy.data.images.load(o["normal"], check_existing=True)
            node.image.colorspace_settings.name = "Non-Color"
            nmap = nodes.new("ShaderNodeNormalMap")
            links.new(node.outputs["Color"], nmap.inputs["Color"])
            links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    return count


def fix_head_attachments(body, P, H):
    """Волосы, рожки, серьги и прочие отдельные куски, растущие выше шеи, — к голове.

    Автоматика привязывает пряди, свисающие рядом с руками, к костям рук, и в игре волосы
    летают вместе с руками. Такие куски (острова меша с верхом выше шеи, кроме основного тела)
    получают вес головы, а ниже плавно переходят на шею и верх спины."""
    me = body.data
    n = len(me.vertices)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for e in me.edges:
        a, b = find(e.vertices[0]), find(e.vertices[1])
        if a != b:
            parent[a] = b
    islands = {}
    for i in range(n):
        islands.setdefault(find(i), []).append(i)
    island_polys = {}
    for p in me.polygons:
        island_polys.setdefault(find(p.vertices[0]), []).append(p.vertices[:])

    neck = P["Bip01_Neck1"].z
    s4, s2 = P["Bip01_Spine4"].z, P["Bip01_Spine2"].z
    head, sp4, sp2 = (VB + "Bip01_Head1", VB + "Bip01_Spine4", VB + "Bip01_Spine2")
    # «растёт из головы» — верх куска выше подбородка (сустав головы ставится над подбородком);
    # воротники и банты начинаются ниже и остаются на теле
    chin = P["Bip01_Head1"].z - 0.035 * H
    # Кусок, внутри которого сидят суставы тела (позвоночник, плечи, локти, кисти, таз), — это
    # само тело или рука, а не волосы. Размер куска не годится: у мультяшных персонажей голова
    # бывает больше торса, а плечи — выше подбородка.
    body_joints = [P[k] for k in ("Bip01_Pelvis", "Bip01_Spine1", "Bip01_Spine2", "Bip01_Spine4",
                                  "Bip01_R_UpperArm", "Bip01_L_UpperArm", "Bip01_R_Forearm",
                                  "Bip01_L_Forearm", "Bip01_R_Hand", "Bip01_L_Hand")]
    # волосы, рожки, серьги — над плечами по ширине; предмет в вытянутой руке — далеко сбоку
    head_x = P["Bip01_Head1"].x
    reach = 1.3 * max(abs(P["Bip01_R_UpperArm"].x - head_x), abs(P["Bip01_L_UpperArm"].x - head_x))

    def over_shoulders(isl):
        return abs(sum(me.vertices[i].co.x for i in isl) / len(isl) - head_x) < reach

    picked = [isl for root, isl in islands.items()
              if max(me.vertices[i].co.z for i in isl) > chin and over_shoulders(isl)
              and not contains_joint(me, isl, island_polys.get(root, []), body_joints)]
    verts = [i for isl in picked for i in isl]
    if not verts:
        return 0
    for vg in body.vertex_groups:
        vg.remove(verts)
    groups = {name: body.vertex_groups.get(name) for name in (head, sp4, sp2)}
    for i in verts:
        z = me.vertices[i].co.z
        if z >= neck:
            w = {head: 1.0}
        elif z >= s4:
            t = (z - s4) / max(neck - s4, 1e-6)
            w = {head: t, sp4: 1 - t}
        elif z >= s2:
            t = (z - s2) / max(s4 - s2, 1e-6)
            w = {sp4: t, sp2: 1 - t}
        else:
            w = {sp2: 1.0}
        for name, wt in w.items():
            if wt > 1e-4:
                groups[name].add([i], wt, "REPLACE")
    return len(picked)


def contains_joint(me, island, island_polys, joints):
    """Сидит ли хоть один сустав внутри куска меша: лучи от сустава в четыре стороны
    по горизонтали должны упереться в этот кусок (оболочка вокруг сустава)."""
    from mathutils.bvhtree import BVHTree

    xs = [me.vertices[i].co.x for i in island]
    ys = [me.vertices[i].co.y for i in island]
    zs = [me.vertices[i].co.z for i in island]
    inside_box = [j for j in joints if min(xs) <= j.x <= max(xs) and min(ys) <= j.y <= max(ys)
                  and min(zs) <= j.z <= max(zs)]
    if not inside_box:
        return False
    remap = {v: k for k, v in enumerate(island)}
    polys = [[remap[v] for v in p] for p in island_polys]
    if not polys:
        return False
    bvh = BVHTree.FromPolygons([me.vertices[i].co for i in island], polys)
    dirs = (Vector((1, 0, 0)), Vector((-1, 0, 0)), Vector((0, 1, 0)), Vector((0, -1, 0)))
    for j in inside_box:
        if sum(1 for d in dirs if bvh.ray_cast(j, d)[0] is not None) >= 3:
            return True
    return False


def smooth_weights(body, factor=0.5, repeat=2):
    """Сглаживание весов по соседним вершинам (как Smooth в Weight Paint, но без UI-контекста)."""
    import numpy as np

    me = body.data
    groups = list(body.vertex_groups)
    V, G = len(me.vertices), len(groups)
    w = np.zeros((V, G), np.float32)
    for v in me.vertices:
        for g in v.groups:
            w[v.index, g.group] = g.weight
    edges = np.zeros(len(me.edges) * 2, np.int32)
    me.edges.foreach_get("vertices", edges)
    edges = edges.reshape(-1, 2)
    deg = np.bincount(edges.ravel(), minlength=V).astype(np.float32)
    has = deg > 0
    for _ in range(repeat):
        acc = np.zeros_like(w)
        np.add.at(acc, edges[:, 0], w[edges[:, 1]])
        np.add.at(acc, edges[:, 1], w[edges[:, 0]])
        w[has] = (1 - factor) * w[has] + factor * acc[has] / deg[has, None]
    everything = list(range(V))
    for gi, vg in enumerate(groups):
        vg.remove(everything)
        col = w[:, gi]
        for i in np.nonzero(col > 1e-3)[0]:
            vg.add([int(i)], float(col[i]), "REPLACE")


def _dist_to_segment(p, a, b):
    ab = b - a
    t = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_squared, 1e-12)))
    return (a + ab * t - p).length


def render_test_pose(body, arm_obj, out_dir):
    """Руки вверх-вперёд, колени согнуты, голова повёрнута — сразу видно, где кожа тянется не так."""
    from blender_char_prepare import render_view

    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="POSE")
    pose = {
        "ValveBiped.Bip01_R_UpperArm": Matrix.Rotation(math.radians(40), 4, "Y"),
        "ValveBiped.Bip01_L_UpperArm": Matrix.Rotation(math.radians(-40), 4, "Y"),
        "ValveBiped.Bip01_R_Forearm": Matrix.Rotation(math.radians(-50), 4, "X"),
        "ValveBiped.Bip01_L_Forearm": Matrix.Rotation(math.radians(-50), 4, "X"),
        "ValveBiped.Bip01_R_Thigh": Matrix.Rotation(math.radians(-40), 4, "X"),
        "ValveBiped.Bip01_L_Thigh": Matrix.Rotation(math.radians(-15), 4, "X"),
        "ValveBiped.Bip01_R_Calf": Matrix.Rotation(math.radians(70), 4, "X"),
        "ValveBiped.Bip01_Head1": Matrix.Rotation(math.radians(30), 4, "Z"),
    }
    for name, rot in pose.items():  # родители раньше детей — повороты складываются
        pb = arm_obj.pose.bones[name]
        head = pb.head.copy()
        pb.matrix = Matrix.Translation(head) @ rot @ Matrix.Translation(-head) @ pb.matrix
        bpy.context.view_layer.update()
    bpy.ops.object.mode_set(mode="OBJECT")
    front = render_view(body, os.path.join(out_dir, "test_front.png"))
    side = render_view(body, os.path.join(out_dir, "test_side.png"), side=True, res=(500, 1000))
    for pb in arm_obj.pose.bones:
        pb.matrix_basis = Matrix()
    bpy.context.view_layer.update()
    return front, side


def repose_to_standard(bones, W, me, vlinks):
    """Переводит меш в позу эталонного скелета (A-поза HL2) по уже посчитанным весам.

    Кости получают ровно эталонные локальные повороты, длины остаются нашими. Тогда
    «proportion trick» переносит в анимации только длины костей, а не разницу поз — иначе
    каждая анимация остаётся довёрнутой (ноги враскорячку, оружие за спиной)."""
    Wn = []
    for i, b in enumerate(bones):
        x, y, z, w = b["quat"]
        R = Quaternion((w, x, y, z)).to_matrix().to_4x4()
        if b["parent"] < 0:
            Wn.append(Matrix.Translation(W[i].to_translation()) @ R)
        else:
            t = (W[b["parent"]].inverted() @ W[i]).to_translation()  # длина и смещение — наши
            Wn.append(Wn[b["parent"]] @ Matrix.Translation(t) @ R)
    skin = [Wn[i] @ W[i].inverted() for i in range(len(bones))]
    rot = [m.to_3x3() for m in skin]
    pos = []
    for vi, v in enumerate(me.vertices):
        p = Vector()
        for b, wt in vlinks[vi]:
            p += (skin[b] @ v.co) * wt
        pos.append(p)

    def normal(vi, n):
        out = Vector()
        for b, wt in vlinks[vi]:
            out += (rot[b] @ n) * wt
        return out.normalized()

    return Wn, pos, normal


def render_reference_pose(body, me, pos, out_dir):
    """Как персонаж будет стоять в игре без анимации (поза эталона)."""
    from blender_char_prepare import render_view

    ref_me = me.copy()
    ref_me.vertices.foreach_set("co", [c for p in pos for c in p])
    ref_me.update()
    ref = bpy.data.objects.new("ReferencePose", ref_me)
    bpy.context.scene.collection.objects.link(ref)
    body.hide_render = True
    view = render_view(ref, os.path.join(out_dir, "ref_front.png"))
    body.hide_render = False
    bpy.data.objects.remove(ref, do_unlink=True)
    return view


def write_smd(path, bones, W, triangles):
    """triangles: [(material, [(pos, normal, uv, [(bone, w), ...]) * 3])], позиции в метрах."""
    lines = ["version 1", "nodes"]
    lines += [f'{i} "{b["name"]}" {b["parent"]}' for i, b in enumerate(bones)]
    lines += ["end", "skeleton", "time 0"]
    for i, b in enumerate(bones):
        local = W[i] if b["parent"] < 0 else W[b["parent"]].inverted() @ W[i]
        t = local.to_translation() / INCH
        r = local.to_euler("XYZ")
        lines.append(f"{i} {t.x:.6f} {t.y:.6f} {t.z:.6f} {r.x:.6f} {r.y:.6f} {r.z:.6f}")
    lines += ["end", "triangles"]
    for mat, verts in triangles:
        lines.append(mat)
        for p, n, uv, links in verts:
            p = p / INCH
            parent = max(links, key=lambda l: l[1])[0]
            lk = " ".join(f"{bi} {w:.6f}" for bi, w in links)
            lines.append(f"{parent} {p.x:.6f} {p.y:.6f} {p.z:.6f} {n.x:.6f} {n.y:.6f} {n.z:.6f} "
                         f"{uv[0]:.6f} {uv[1]:.6f} {len(links)} {lk}")
    lines.append("end")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    job = json.load(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8"))
    out = job["out_dir"]
    bpy.ops.wm.open_mainfile(filepath=os.path.join(out, "char.blend"))
    body = bpy.data.objects["Body"]
    H = job["height_m"]
    bones = job["bones"]
    names = [short(b["name"]) for b in bones]
    index = {b["name"]: i for i, b in enumerate(bones)}

    tex = apply_textures(body.data, job.get("textures", {}))
    if tex:
        log(f"назначено текстур вручную: {tex}")
    depth, bvh = make_depth(body)
    W_std = std_world(bones)
    verts = [v.co.copy() for v in body.data.vertices]
    P, P_std, s_torso, s_hand, extra = build_joints(job["markers"], bones, W_std, H, depth, verts)
    W = build_skeleton(bones, W_std, P, P_std, s_torso, s_hand, extra)
    arm_obj = make_armature(bones, W, P, H)
    if ROLL_LOG:
        log("поворот ладоней по большому пальцу: " +
            ", ".join(f"{'правая' if s == 'R' else 'левая'} {a}" for s, a in sorted(ROLL_LOG.items())))
    log(f"скелет: {len(bones)} костей, весование…")
    auto, fixed = weight_mesh(body, arm_obj, H)
    log(f"веса: автоматически {auto} вершин, по расстоянию до костей {fixed}")
    hair = fix_head_attachments(body, P, H)
    log(f"волосы и детали головы привязаны к голове: {hair} кусков")
    test_front, test_side = render_test_pose(body, arm_obj, out)

    # --- body.smd
    me = body.data
    me.calc_loop_triangles()
    uv = me.uv_layers.active.data if me.uv_layers.active else None
    corner = me.corner_normals if hasattr(me, "corner_normals") else None
    used, mat_names, materials = set(), [], []
    for i, mat in enumerate(me.materials or [None]):
        name = safe(mat.name if mat else "body", used)
        mat_names.append(name)
        materials.append(describe_material(mat, name, out))
    gidx = {vg.index: index[vg.name] for vg in body.vertex_groups if vg.name in index}
    vlinks = []
    for v in me.vertices:
        # Source: не больше 3 костей на вершину — оставляем три самых сильных и нормируем
        links = sorted(((gidx[g.group], g.weight) for g in v.groups if g.group in gidx and g.weight > 1e-4),
                       key=lambda l: -l[1])[:3]
        total = sum(w for _, w in links) or 1.0
        vlinks.append([(b, w / total) for b, w in links] or [(0, 1.0)])

    # В игру уходит меш в позе эталона: повороты костей = эталонные, длины — наши
    W_rest = W
    W, pos, skin_normal = repose_to_standard(bones, W_rest, me, vlinks)
    ref_front = render_reference_pose(body, me, pos, out)
    tris = []
    for t in me.loop_triangles:
        verts = []
        for li in t.loops:
            vi = me.loops[li].vertex_index
            n = (corner[li].vector if corner is not None else me.loops[li].normal).normalized()
            verts.append((pos[vi], skin_normal(vi, n), uv[li].uv if uv else (0.0, 0.0), vlinks[vi]))
        tris.append((mat_names[min(t.material_index, len(mat_names) - 1)], verts))
    write_smd(os.path.join(out, "body.smd"), bones, W, tris)

    # --- phys.smd: выпуклая форма на каждую часть тела рэгдолла
    solids = job["solids"]
    region_of = {}
    for n in names:
        target = n if VB + n in solids else REGION.get(n)
        if is_finger(n):  # пальцы — часть формы кисти
            target = "Bip01_R_Hand" if "_R_" in n else "Bip01_L_Hand"
        region_of[n] = VB + target if target and VB + target in solids else None
    pts = {s: [] for s in solids}
    for vi in range(len(me.vertices)):
        dom = max(vlinks[vi], key=lambda l: l[1])[0]
        region = region_of.get(names[dom])
        if region:
            pts[region].append(pos[vi])
    phys = []
    for s in solids:
        p = pts[s]
        if len(p) < 8:  # нет вершин — маленький кубик у сустава
            c = W[index[s]].to_translation()
            r = 0.03 * H
            p = [c + Vector((dx, dy, dz)) for dx in (-r, r) for dy in (-r, r) for dz in (-r, r)]
        centroid = sum(p, Vector()) / len(p)
        bm = bmesh.new()
        for q in p:
            bm.verts.new(centroid + (q - centroid) * 0.9)  # чуть меньше меша, чтобы части не застревали
        res = bmesh.ops.convex_hull(bm, input=bm.verts)
        leftovers = {*res["geom_interior"], *res["geom_unused"]}
        bmesh.ops.delete(bm, geom=[g for g in leftovers if g.is_valid], context="VERTS")
        bmesh.ops.triangulate(bm, faces=bm.faces)
        bm.normal_update()
        for f in bm.faces:
            phys.append(("phy", [(vv.co.copy(), f.normal.copy(), (0.0, 0.0), [(index[s], 1.0)]) for vv in f.verts]))
        bm.free()
    write_smd(os.path.join(out, "phys.smd"), bones, W, phys)

    # --- данные для QC и превью
    head = index[VB + "Bip01_Head1"]
    chin = job["markers"]["chin"]
    eye_z = chin[1] + 0.07 * H
    front_hit = bvh.ray_cast(Vector((chin[0], -100, eye_z)), Vector((0, 1, 0)))[0]
    eye = Vector((chin[0], (front_hit.y if front_hit else P["Bip01_Head1"].y) + 0.01 * H, eye_z))
    eye_local = W_rest[head].inverted() @ eye  # глаз жёстко привязан к голове — в обеих позах одинаково
    eye_ref = W[head] @ eye_local
    info = {
        # превью скелета рисуется поверх исходной позы модели
        "joints_front": {short(b["name"]).replace("Bip01_", ""):
                         [W_rest[i].to_translation().x, W_rest[i].to_translation().z]
                         for i, b in enumerate(bones) if short(b["name"]) not in HELPERS},
        "parents": {short(b["name"]).replace("Bip01_", ""): short(bones[b["parent"]]["name"]).replace("Bip01_", "")
                    for b in bones if b["parent"] >= 0 and short(b["name"]) not in HELPERS},
        "test_front": test_front, "test_side": test_side, "ref_front": ref_front,
        "eye_local": [c / INCH for c in eye_local], "eye_height": eye_ref.z / INCH,
        "height": H / INCH, "materials": materials, "triangles": len(tris), "fixed_vertices": fixed,
        "bone_count": len(bones),
    }
    with open(os.path.join(out, "rig.json"), "w", encoding="utf-8") as f:
        json.dump(info, f)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, "rigged.blend"))
    log(f"исходники готовы: {len(tris)} треугольников, рэгдолл из {len(solids)} частей")


if __name__ == "__main__":
    main()
