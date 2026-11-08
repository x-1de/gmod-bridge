"""Flex-правила Source -> драйверы Blender.

В MDL каждое правило — это формула в обратной польской записи:
    biter = c35 * (1 - c49) + c49
где cN — значение flex-контроллера (слайдера Face Poser).

Компилируем её в «простое выражение» драйвера Blender (арифметика,
min/max, if-else). Такие драйверы Blender считает сам, без Python,
поэтому они работают и при выключенном Auto Run Python Scripts.

Выражение драйвера ограничено 255 символами, поэтому длинные
подвыражения выносятся во вспомогательные свойства арматуры (_fx.N)
со своими драйверами.
"""
from __future__ import annotations

from dataclasses import dataclass, field

CONST, FETCH1, FETCH2, ADD, SUB, MUL, DIV, NEG = 1, 2, 3, 4, 5, 6, 7, 8
MAX, MIN, TWOWAY_0, TWOWAY_1, NWAY, COMBO, DOMINATE = 13, 14, 15, 16, 17, 18, 19
DME_LOWER_EYELID, DME_UPPER_EYELID = 20, 21
NOOPS = {9, 10, 11, 12}  # EXP/OPEN/CLOSE/COMMA — только для парсера studiomdl

MAX_EXPR = 200  # запас до лимита Blender в 255 символов

CONTROLLER_PREFIX = "fc."
HELPER_PREFIX = "_fx."


@dataclass
class Expr:
    text: str
    const: float | None = None
    vars: set[str] = field(default_factory=set)  # имена переменных драйвера (cN, hN)


def controller_prop(name: str) -> str:
    return CONTROLLER_PREFIX + name


def _num(v: float) -> str:
    s = f"{v:.6g}"
    return f"({s})" if v < 0 else s


class Compiler:
    def __init__(self, mdl):
        self.mdl = mdl
        self.helpers: list[dict] = []
        self.flex_expr: dict[int, Expr] = {}

    # --- построение выражений

    def _ctrl(self, i: int) -> Expr:
        return Expr(f"c{i}", vars={f"c{i}"})

    def _join(self, fmt: str, *parts: Expr) -> Expr:
        """fmt с {0}, {1}… подставляет тексты частей. Если результат длиннее лимита,
        самые длинные части по очереди выносятся во вспомогательные свойства."""
        parts = list(parts)
        while True:
            text = fmt.format(*(p.text for p in parts))
            if len(text) <= MAX_EXPR:
                return Expr(text, vars=set().union(*(p.vars for p in parts)) if parts else set())
            i = max(range(len(parts)), key=lambda j: len(parts[j].text))
            if len(parts[i].text) <= 3:
                raise ValueError("flex-выражение не помещается в драйвер")
            parts[i] = self._helper(parts[i])

    def _helper(self, e: Expr) -> Expr:
        """Выносит выражение в свойство _fx.N с отдельным драйвером."""
        k = len(self.helpers)
        self.helpers.append({"prop": f"{HELPER_PREFIX}{k}", "expr": e.text, "vars": self._var_map(e.vars)})
        return Expr(f"h{k}", vars={f"h{k}"})

    def _var_map(self, names: set[str]) -> dict[str, str]:
        out = {}
        for n in sorted(names):
            if n[0] == "c":
                out[n] = controller_prop(self.mdl.flex_controllers[int(n[1:])].name)
            else:
                out[n] = f"{HELPER_PREFIX}{n[1:]}"
        return out

    def _remap01(self, ci: int) -> Expr:
        """Значение контроллера, переведённое из [min, max] в [0, 1]."""
        c = self.mdl.flex_controllers[ci]
        span = (c.max - c.min) or 1.0
        return self._join(f"min(max(({{0}}-{_num(c.min)})/{_num(span)},0),1)", self._ctrl(ci))

    # --- RPN -> выражение

    def compile_rule(self, rule) -> Expr:
        stack: list[Expr] = []
        for op, iv, fv in rule.ops:
            if op == CONST:
                stack.append(Expr(_num(fv), const=fv))
            elif op == FETCH1:
                stack.append(self._ctrl(iv))
            elif op == FETCH2:
                stack.append(self._fetch_flex(iv))
            elif op in (ADD, SUB, MUL):
                b, a = stack.pop(), stack.pop()
                sym = {ADD: "+", SUB: "-", MUL: "*"}[op]
                stack.append(self._join(f"({{0}}{sym}{{1}})", a, b))
            elif op == DIV:  # движок: деление только при делителе > 0.0001, иначе 0
                b, a = stack.pop(), stack.pop()
                if len(b.text) > 12:
                    b = self._helper(b)
                stack.append(self._join("({0}/{1} if {1}>0.0001 else 0)", a, b))
            elif op == NEG:
                stack.append(self._join("(-{0})", stack.pop()))
            elif op in (MAX, MIN):
                b, a = stack.pop(), stack.pop()
                fn = "max" if op == MAX else "min"
                stack.append(self._join(fn + "({0},{1})", a, b))
            elif op == TWOWAY_0:  # -1..0 -> 1..0
                stack.append(self._join("min(max(-{0},0),1)", self._ctrl(iv)))
            elif op == TWOWAY_1:  # 0..1 -> 0..1
                stack.append(self._join("min(max({0},0),1)", self._ctrl(iv)))
            elif op == NWAY:
                stack.append(self._nway(stack, iv))
            elif op == COMBO:
                parts = [stack.pop() for _ in range(iv)][::-1]
                fmt = "(" + "*".join(f"{{{j}}}" for j in range(len(parts))) + ")"
                stack.append(self._join(fmt, *parts))
            elif op == DOMINATE:
                dom = [stack.pop() for _ in range(iv)][::-1]
                base = stack.pop()
                prod = "*".join(f"{{{j + 1}}}" for j in range(len(dom)))
                stack.append(self._join(f"({{0}}*(1-{prod}))", base, *dom))
            elif op in (DME_LOWER_EYELID, DME_UPPER_EYELID):
                stack.append(self._eyelid(stack, iv, upper=op == DME_UPPER_EYELID))
            elif op in NOOPS:
                continue
            else:
                raise ValueError(f"неизвестная операция flex-правила {op}")
        return stack[-1] if stack else Expr("0", 0.0)

    def _fetch_flex(self, flex: int) -> Expr:
        """Ссылка на уже посчитанный flex. Длинные выражения выносятся один раз и переиспользуются."""
        prev = self.flex_expr.get(flex)
        if prev is None:
            return Expr("0", 0.0)
        if len(prev.text) > 24:
            prev = self.flex_expr[flex] = self._helper(prev)
        return Expr(f"({prev.text})" if not prev.text.startswith("h") else prev.text, prev.const, set(prev.vars))

    def _nway(self, stack, multiplier: int) -> Expr:
        value_ci = int(stack.pop().const or 0)
        z, y, x, w = (self._small(e) for e in (stack.pop(), stack.pop(), stack.pop(), stack.pop()))
        v = self._ctrl(value_ci)
        # {0}=значение, {1..4}=W X Y Z
        ramp = self._join(
            "min(1-(min(1,-min(0,{2}-{0}))*(1-min(max(({0}-{1})/({2}-{1}),0),1))"
            "+min(1,-min(0,{0}-{3}))*(1-min(max(({0}-{4})/({3}-{4}),0),1))),1)",
            v, w, x, y, z)
        return self._join("({0}*{1})", self._ctrl(multiplier), ramp)

    def _small(self, e: Expr) -> Expr:
        return e if len(e.text) <= 12 else self._helper(e)

    def _eyelid(self, stack, close_lid_v: int, upper: bool) -> Expr:
        """Веки моделей из SFM/DMX. Приближение логики studiorender:
        закрытие века с учётом моргания и взгляда вверх/вниз."""
        close_lid = int(stack.pop().const or 0)
        blink = int(stack.pop().const or -1)
        updown = int(stack.pop().const or -1)
        lid_v = self._remap01(close_lid_v)
        lid = self._remap01(close_lid)
        if blink >= 0:
            lid = self._join("max({0},{1})", lid, self._remap01(blink))
        if updown >= 0:
            c = self.mdl.flex_controllers[updown]
            span = (c.max - c.min) or 1.0
            ud = self._helper(self._join(
                f"min(max(({{0}}-{_num(c.min)})/{_num(span)}*2-1,-1),1)", self._ctrl(updown)))
            lid_v, lid = self._small(lid_v), self._small(lid)
            if upper:
                fmt = "((1+{0})*(1-{1})*{2} if {0}<0 else (1-{1})*{2})"
            else:
                fmt = "((1-{0})*(1-{1})*{2} if {0}>0 else (1-{1})*{2})"
            return self._join(fmt, ud, lid_v, lid)
        return self._join("((1-{0})*{1})", lid_v, lid)

    def compile(self) -> dict:
        drivers = {}
        for rule in self.mdl.flex_rules:
            if rule.flex >= len(self.mdl.flex_names):
                continue
            e = self.compile_rule(rule)
            self.flex_expr[rule.flex] = e
            drivers[self.mdl.flex_names[rule.flex]] = {"expr": e.text, "vars": self._var_map(e.vars)}
        return {
            "controllers": [
                {"name": c.name, "prop": controller_prop(c.name), "type": c.type, "min": c.min, "max": c.max}
                for c in self.mdl.flex_controllers
            ],
            "helpers": self.helpers,
            "drivers": drivers,
        }


def compile_flex(mdl) -> dict | None:
    if not mdl.flex_controllers or not mdl.flex_rules:
        return None
    return Compiler(mdl).compile()


def evaluate(mdl, values: dict[str, float]) -> dict[str, float]:
    """Прямое вычисление правил (как в движке) — для проверки скомпилированных драйверов."""
    src = [values.get(c.name, 0.0) for c in mdl.flex_controllers]
    dest = [0.0] * len(mdl.flex_names)
    for rule in mdl.flex_rules:
        st: list[float] = []
        for op, iv, fv in rule.ops:
            if op == CONST:
                st.append(fv)
            elif op == FETCH1:
                st.append(src[iv])
            elif op == FETCH2:
                st.append(dest[iv])
            elif op in (ADD, SUB, MUL, DIV, MAX, MIN):
                b, a = st.pop(), st.pop()
                st.append({ADD: a + b, SUB: a - b, MUL: a * b, DIV: a / b if b > 0.0001 else 0.0,
                           MAX: max(a, b), MIN: min(a, b)}[op])
            elif op == NEG:
                st[-1] = -st[-1]
            elif op == TWOWAY_0:
                st.append(min(max(-src[iv], 0.0), 1.0))
            elif op == TWOWAY_1:
                st.append(min(max(src[iv], 0.0), 1.0))
            elif op == COMBO:
                p = 1.0
                for _ in range(iv):
                    p *= st.pop()
                st.append(p)
            elif op == DOMINATE:
                p = 1.0
                for _ in range(iv):
                    p *= st.pop()
                st[-1] *= 1.0 - p
        if rule.flex < len(dest) and st:
            dest[rule.flex] = st[-1]
    return {mdl.flex_names[i]: v for i, v in enumerate(dest)}
