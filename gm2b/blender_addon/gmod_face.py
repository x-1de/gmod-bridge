"""GMod Tools — панель для моделей, импортированных программой GMod → Blender.

Вкладка «GMod» в боковой панели 3D-вида (N):
  • Скин и bodygroups — как в контекстном меню GMod
  • Face Poser — слайдеры flex-контроллеров с поиском и группами
  • Сброс, ключи анимации, пресеты выражений

Слайдеры — это свойства арматуры «fc.<имя>», shape keys двигаются драйверами,
поэтому лицо работает и без этого плагина; плагин просто даёт удобный интерфейс.
"""

bl_info = {
    "name": "GMod Tools (Face Poser, скины, bodygroups)",
    "author": "GMod → Blender",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "3D View → N-панель → GMod",
    "description": "Слайдеры Face Poser, скины и bodygroups для моделей из Garry's Mod",
    "category": "Animation",
}

import json

import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty

# ------------------------------------------------------------------ данные модели


def find_rig(context):
    """Арматура модели GMod: активный объект, его родитель или цель модификатора Armature."""
    obj = context.active_object
    candidates = []
    if obj:
        candidates += [obj, obj.parent]
        candidates += [m.object for m in getattr(obj, "modifiers", []) if m.type == "ARMATURE"]
    for c in candidates:
        if c and c.type == "ARMATURE" and ("gm2b_flex_ui" in c or "skin" in c or "gm2b_bodygroups" in c):
            return c
    return None


def controllers(rig):
    """[(prop, name, группа, min, max), ...]

    Обычно группа — это тип контроллера из модели (eyelid, phoneme…). Но в моделях,
    собранных из DMX, тип часто совпадает с именем, и тогда группируем по префиксу
    имени: ALL.Joy -> ALL, EYE.Close.L -> EYE, jaw_drop -> jaw."""
    try:
        raw = [tuple(c) for c in json.loads(rig.get("gm2b_flex_ui", "[]"))]
    except (TypeError, ValueError):
        return []
    if len({c[2] for c in raw}) <= max(3, len(raw) // 3):
        return raw

    def prefix(name):
        return name.split(".")[0] if "." in name else name.split("_")[0]

    sizes = {}
    for c in raw:
        sizes[prefix(c[1])] = sizes.get(prefix(c[1]), 0) + 1
    return [(p, n, prefix(n) if sizes[prefix(n)] > 1 else "Другое", lo, hi) for p, n, _t, lo, hi in raw]


def presets(rig):
    try:
        return json.loads(rig.get("gm2b_presets", "{}"))
    except (TypeError, ValueError):
        return {}


_group_items = []


def group_items(self, context):
    global _group_items
    rig = find_rig(context)
    groups = sorted({c[2] for c in controllers(rig)}) if rig else []
    _group_items = [("ALL", "Все группы", "")] + [(g, g, "") for g in groups]
    return _group_items


# ------------------------------------------------------------------ операторы


class GM2B_OT_flex_reset(bpy.types.Operator):
    bl_idname = "gm2b.flex_reset"
    bl_label = "Сбросить лицо"
    bl_description = "Вернуть все слайдеры в ноль"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        rig = find_rig(context)
        for prop, _name, _type, lo, hi in controllers(rig):
            rig[prop] = min(max(0.0, lo), hi)
        rig.update_tag()
        return {"FINISHED"}


class GM2B_OT_flex_keyframe(bpy.types.Operator):
    bl_idname = "gm2b.flex_keyframe"
    bl_label = "Ключ на все слайдеры"
    bl_description = "Поставить ключ анимации на все слайдеры в текущем кадре"
    bl_options = {"REGISTER", "UNDO"}

    only_changed: bpy.props.BoolProperty(name="Только ненулевые", default=False)

    def execute(self, context):
        rig = find_rig(context)
        n = 0
        for prop, _name, _type, lo, hi in controllers(rig):
            if self.only_changed and abs(rig[prop] - min(max(0.0, lo), hi)) < 1e-6:
                continue
            rig.keyframe_insert(data_path=f'["{prop}"]')
            n += 1
        self.report({"INFO"}, f"Ключей: {n}")
        return {"FINISHED"}


class GM2B_OT_preset_save(bpy.types.Operator):
    bl_idname = "gm2b.preset_save"
    bl_label = "Сохранить выражение"
    bl_description = "Запомнить текущие значения слайдеров под именем"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        rig = find_rig(context)
        name = context.window_manager.gm2b_preset_name.strip() or f"Выражение {len(presets(rig)) + 1}"
        data = presets(rig)
        data[name] = {prop: rig[prop] for prop, *_ in controllers(rig)}
        rig["gm2b_presets"] = json.dumps(data)
        context.window_manager.gm2b_preset_name = ""
        return {"FINISHED"}


class GM2B_OT_preset_apply(bpy.types.Operator):
    bl_idname = "gm2b.preset_apply"
    bl_label = "Применить выражение"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty()

    def execute(self, context):
        rig = find_rig(context)
        for prop, value in presets(rig).get(self.name, {}).items():
            if prop in rig:
                rig[prop] = value
        rig.update_tag()
        return {"FINISHED"}


class GM2B_OT_preset_delete(bpy.types.Operator):
    bl_idname = "gm2b.preset_delete"
    bl_label = "Удалить выражение"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty()

    def execute(self, context):
        rig = find_rig(context)
        data = presets(rig)
        data.pop(self.name, None)
        rig["gm2b_presets"] = json.dumps(data)
        return {"FINISHED"}


class GM2B_OT_bodygroup_set(bpy.types.Operator):
    bl_idname = "gm2b.bodygroup_set"
    bl_label = "Bodygroup"
    bl_description = "Показать этот вариант bodygroup"
    bl_options = {"REGISTER", "UNDO"}

    part: IntProperty()
    choice: IntProperty()

    def execute(self, context):
        rig = find_rig(context)
        groups = json.loads(rig.get("gm2b_bodygroups", "[]"))
        _name, objects = groups[self.part]
        for i, obj_name in enumerate(objects):
            obj = bpy.data.objects.get(obj_name)
            if obj:
                hidden = i != self.choice
                obj.hide_set(hidden)
                obj.hide_render = hidden
        return {"FINISHED"}


# ------------------------------------------------------------------ панели


class GM2B_PT_base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GMod"

    @classmethod
    def poll(cls, context):
        return find_rig(context) is not None


class GM2B_PT_model(GM2B_PT_base, bpy.types.Panel):
    bl_label = "Модель GMod"
    bl_idname = "GM2B_PT_model"

    @classmethod
    def poll(cls, context):
        return True

    def draw(self, context):
        layout = self.layout
        rig = find_rig(context)
        if rig is None:
            layout.label(text="Выберите модель из GMod", icon="INFO")
            return
        layout.label(text=rig.name, icon="ARMATURE_DATA")
        if "skin" in rig:
            layout.prop(rig, '["skin"]', text="Скин")

        groups = json.loads(rig.get("gm2b_bodygroups", "[]"))
        multi = [(i, g) for i, g in enumerate(groups) if len(g[1]) > 1]
        if multi:
            box = layout.box()
            box.label(text="Bodygroups", icon="OUTLINER_COLLECTION")
            for i, (name, objects) in multi:
                row = box.row(align=True)
                row.label(text=name)
                any_visible = False
                buttons = []
                for j, obj_name in enumerate(objects):
                    obj = bpy.data.objects.get(obj_name) if obj_name else None
                    visible = bool(obj) and not obj.hide_get()
                    any_visible |= visible
                    buttons.append((j, visible))
                for j, visible in buttons:
                    # пустой вариант «нажат», когда ни один меш не виден
                    depress = visible or (not objects[j] and not any_visible)
                    op = row.operator("gm2b.bodygroup_set", text=str(j), depress=depress)
                    op.part, op.choice = i, j


class GM2B_PT_face(GM2B_PT_base, bpy.types.Panel):
    bl_label = "Face Poser"
    bl_idname = "GM2B_PT_face"

    @classmethod
    def poll(cls, context):
        rig = find_rig(context)
        return rig is not None and "gm2b_flex_ui" in rig

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        rig = find_rig(context)

        row = layout.row(align=True)
        row.operator("gm2b.flex_reset", icon="LOOP_BACK", text="Сброс")
        row.operator("gm2b.flex_keyframe", icon="KEY_HLT", text="Ключ")

        layout.prop(wm, "gm2b_search", text="", icon="VIEWZOOM")
        layout.prop(wm, "gm2b_group", text="")

        search = wm.gm2b_search.lower()
        group = wm.gm2b_group
        by_group = {}
        for c in controllers(rig):
            if search and search not in c[1].lower():
                continue
            if group not in ("ALL", "") and c[2] != group:
                continue
            by_group.setdefault(c[2], []).append(c)

        if not by_group:
            layout.label(text="Ничего не найдено")
        for gname in sorted(by_group):
            box = layout.box()
            box.label(text=gname)
            col = box.column(align=True)
            for prop, name, *_ in by_group[gname]:
                col.prop(rig, f'["{prop}"]', text=name, slider=True)


class GM2B_PT_presets(GM2B_PT_base, bpy.types.Panel):
    bl_label = "Выражения"
    bl_idname = "GM2B_PT_presets"
    bl_parent_id = "GM2B_PT_face"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        rig = find_rig(context)
        row = layout.row(align=True)
        row.prop(context.window_manager, "gm2b_preset_name", text="")
        row.operator("gm2b.preset_save", text="", icon="ADD")
        for name in presets(rig):
            row = layout.row(align=True)
            row.operator("gm2b.preset_apply", text=name).name = name
            row.operator("gm2b.preset_delete", text="", icon="X").name = name


# ------------------------------------------------------------------ регистрация

classes = (
    GM2B_OT_flex_reset, GM2B_OT_flex_keyframe, GM2B_OT_preset_save, GM2B_OT_preset_apply,
    GM2B_OT_preset_delete, GM2B_OT_bodygroup_set,
    GM2B_PT_model, GM2B_PT_face, GM2B_PT_presets,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    wm = bpy.types.WindowManager
    wm.gm2b_search = StringProperty(name="Поиск", description="Фильтр слайдеров по имени",
                                    options={"TEXTEDIT_UPDATE"})
    wm.gm2b_group = EnumProperty(name="Группа", items=group_items)
    wm.gm2b_preset_name = StringProperty(name="Имя выражения")


def unregister():
    wm = bpy.types.WindowManager
    for p in ("gm2b_search", "gm2b_group", "gm2b_preset_name"):
        if hasattr(wm, p):
            delattr(wm, p)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
