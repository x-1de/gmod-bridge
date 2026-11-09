"""Run: blender -b --factory-startup --python-exit-code 1 --python scripts/blender_character_checks.py.

Original synthetic geometry only. Checks fingertip fitting and the no-heat fallback in Blender.
"""
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Vector, Quaternion
import math

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gm2b"))
from blender_char_rig import build_joints, fit_fingers, fix_head_attachments, hand_swing, weight_mesh, VB


def close(a, b):
    assert (a - b).length < 1e-5, (tuple(a), tuple(b))


def finger_fit():
    for side, sign in (("R", -1), ("L", 1)):
        bones, world = [], []
        for digit in range(5):
            for j, suffix in enumerate(("", "1", "2")):
                bones.append({"name": f"{VB}Bip01_{side}_Finger{digit}{suffix}"})
                world.append(Matrix.Translation((sign * (.1 + j * .025), digit * .02, 1)))
        originals = [w.copy() for w in world]
        tips = {f"finger_{side}_{d}": Vector((sign * .19, d * .02 + .03, 1.03)) for d in range(5)}
        fit_fingers(bones, world, tips)
        for d in range(5):
            first, mid, last = [world[d * 3 + j].to_translation() for j in range(3)]
            close(first, originals[d * 3].to_translation())
            close(last + (last - mid) * .8, tips[f"finger_{side}_{d}"])
            for j in range(3):
                assert abs(world[d * 3 + j].to_3x3().determinant() - 1) < 1e-5


def spine_depth():
    names = ["Pelvis", "Spine", "Spine1", "Spine2", "Spine4", "Neck1", "Head1"]
    positions = [(0, 0, z) for z in (30, 34, 38, 43, 48, 55, 60)]
    for s, sign in (("R", -1), ("L", 1)):
        names.extend([f"{s}_Thigh"])
        positions.append((sign * 4, 0, 30))
    bones = [{"name": VB + "Bip01_" + n} for n in names]
    world = [Matrix.Translation(p) for p in positions]
    markers = {"groin": [0, .8, .1], "chin": [0, 1.6, -.1], "neck": [0, 1.5, -.15]}
    for s, sign in (("r", -1), ("l", 1)):
        for name, x, z in (("shoulder", .2, 1.4), ("elbow", .4, 1.3), ("wrist", .6, 1.2),
                            ("knee", .1, .5), ("ankle", .1, .1)):
            markers[f"{name}_{s}"] = [sign * x, z, 0]
    P, *_ = build_joints(markers, bones, world, 1.8, lambda x, z: 99, [])
    for n in ("Spine", "Spine1", "Spine2", "Spine4"):
        p = P["Bip01_" + n]
        t = (p.z - P["Bip01_Pelvis"].z) / (1.5 - P["Bip01_Pelvis"].z)
        assert abs(p.y - (.1 + t * (-.15 - .1))) < 1e-5


def palm_roll():
    hand = "Bip01_R_Hand"
    thumb_direction = Quaternion((1, 0, 0), math.radians(120)) @ Vector((0, 1, 0))
    rotation = hand_swing(hand, Matrix.Identity(3), {hand: Vector()},
                          {hand: Vector(), "Bip01_R_Finger0": Vector((0, 1, 0))},
                          {"fingertip_R": Vector((1, 0, 0)), "thumb_R": thumb_direction})
    close(rotation @ Vector((0, 1, 0)), thumb_direction)


def fallback_weights():
    # Vertex-only meshes leave an empty proxy after cleaning; KDTree must not be queried.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    me = bpy.data.meshes.new("LooseMesh")
    me.from_pydata([(0.1, 0, 0), (.4, .02, 0), (.9, 0, 0)], [], [])
    body = bpy.data.objects.new("Body", me)
    bpy.context.scene.collection.objects.link(body)
    data = bpy.data.armatures.new("TestArmature")
    arm = bpy.data.objects.new("Skeleton", data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    root = data.edit_bones.new("root")
    root.head, root.tail = (0, 0, 0), (.5, 0, 0)
    child = data.edit_bones.new("child")
    child.head, child.tail, child.parent = (.5, 0, 0), (1, 0, 0), root
    unrelated = data.edit_bones.new("unrelated")
    unrelated.head, unrelated.tail = (.11, .0001, 0), (.2, .0001, 0)
    bpy.ops.object.mode_set(mode="OBJECT")
    auto, fallback = weight_mesh(body, arm, 1)
    assert auto == 0 and fallback == 3
    for v in me.vertices:
        weights = [g.weight for g in v.groups]
        assert 1 <= len(weights) <= 3 and abs(sum(weights) - 1) < 1e-5
    assert body.vertex_groups["unrelated"].index not in [g.group for g in me.vertices[0].groups]
    assert not bpy.data.objects.get("WeightProxy")


def seam_classification():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(size=1)
    cube = bpy.context.object
    verts, faces = [], []
    for face in cube.data.polygons:
        start = len(verts)
        verts.extend([tuple(cube.data.vertices[i].co) for i in face.vertices])
        faces.append(tuple(range(start, len(verts))))
    mesh = bpy.data.meshes.new("UVSeams")
    mesh.from_pydata(verts, [], faces)
    cube.data = mesh
    keys = ("Pelvis", "Spine1", "Spine2", "Spine4", "R_UpperArm", "L_UpperArm", "R_Forearm",
            "L_Forearm", "R_Hand", "L_Hand", "Neck1", "Head1")
    joints = {"Bip01_" + key: Vector((0, 0, 0)) for key in keys}
    assert fix_head_attachments(cube, joints, 2) == 0, "UV-split body was mistaken for head accessories"
    assert len(mesh.vertices) == 24, "Original mesh must not be welded"


finger_fit()
spine_depth()
palm_roll()
fallback_weights()
seam_classification()
print("Character Blender checks: finger tips, palm roll, torso depth, fallback weights, UV seam classification OK")
