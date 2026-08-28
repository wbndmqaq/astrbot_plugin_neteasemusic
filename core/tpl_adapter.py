from __future__ import annotations

import re


def _convert_conditional(s: str) -> str:
    # 处理 {{ ... ? 'a' : 'b' }}  —— 直接转 (a if cond else b)
    def repl_ternary(m):
        inner = m.group(1).strip()
        # 仅当确有三元运算符（且不是 || 也不是 &&）才转换
        if "?" not in inner or ":" not in inner:
            return m.group(0)
        # 拆分顶层 ?: （不处理嵌套过深）
        # 找第一个顶层 '?'
        depth = 0
        qpos = -1
        for i, ch in enumerate(inner):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "?" and depth == 0:
                qpos = i
                break
        if qpos < 0:
            return m.group(0)
        cond = inner[:qpos].strip()
        rest = inner[qpos + 1 :]
        # 在 rest 里找顶层 ':'
        depth = 0
        cpos = -1
        in_str = None
        i = 0
        while i < len(rest):
            ch = rest[i]
            if in_str:
                if ch == "\\":
                    i += 2
                    continue
                if ch == in_str:
                    in_str = None
            else:
                if ch in ("'", '"'):
                    in_str = ch
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                elif ch == ":" and depth == 0:
                    cpos = i
                    break
            i += 1
        if cpos < 0:
            return m.group(0)
        a = rest[:cpos].strip()
        b = rest[cpos + 1 :].strip()
        a = _jsval_to_jinja(a)
        b = _jsval_to_jinja(b)
        cond_j = _jsval_to_jinja(cond, is_cond=True)
        return "{{ (" + a + " if " + cond_j + " else " + b + ") }}"

    # 匹配 {{ ... }}，非贪婪、不跨多个 {{ }}
    return re.sub(r"\{\{(?!each|if|else|\/)([^{}]*)\}\}", repl_ternary, s)


def _jsval_to_jinja(v: str, *, is_cond: bool = False) -> str:
    v = v.strip()
    if not v:
        return "''"
    # 字符串字面量：必须是「整体」合法的字面量（首尾引号且中间无裸引号）。
    # 不能用 startswith/endswith 判断——`'a' + x + 'b'` 这类拼接表达式首尾也是引号，
    # 会被误判为字符串导致内部引号被转义、变量变成字符串内容。
    if re.fullmatch(r"'[^']*'", v) or re.fullmatch(r'"[^"]*"', v):
        inner = v[1:-1]
        return "'" + inner.replace("'", "\\'") + "'"
    # 数字
    if re.fullmatch(r"-?\d+(\.\d+)?", v):
        return v
    # || 默认值：a || b -> a if a else b（Jinja 支持 truthiness）
    # 简单拆顶层 ||
    parts = _split_top(v, "||")
    if len(parts) > 1:
        expr = _jsval_to_jinja(parts[0].strip())
        for p in parts[1:]:
            default = _jsval_to_jinja(p.strip())
            expr = f"({default} if not {expr} else {expr})"
        return expr
    # + 字符串拼接：'a' + data.x
    parts = _split_top(v, "+")
    if len(parts) > 1:
        return "(" + " ~ ".join(_jsval_to_jinja(p.strip()) for p in parts) + ")"
    # 属性访问 data.x / item.y —— Jinja 用点；但与 dict 内置方法（items/keys/values/get 等）冲突时改用 [] 访问
    v = v.replace("&&", " and ").replace("||", " or ") if is_cond else v
    # 处理 obj.prop 形式：若 prop 是 dict 内置方法名，转 obj['prop']
    DICT_METHODS = {
        "items",
        "keys",
        "values",
        "get",
        "pop",
        "popitem",
        "setdefault",
        "update",
        "copy",
        "clear",
        "fromkeys",
    }
    m = re.fullmatch(r"([\w.]+)\.(\w+)", v)
    if m and m.group(2) in DICT_METHODS:
        return f"{m.group(1)}['{m.group(2)}']"
    return v


def _split_top(s: str, op: str) -> list[str]:
    parts = []
    depth = 0
    in_str = None
    cur = ""
    i = 0
    while i < len(s):
        ch = s[i]
        if in_str:
            cur += ch
            if ch == "\\":
                if i + 1 < len(s):
                    cur += s[i + 1]
                    i += 2
                    continue
            elif ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ("'", '"'):
            in_str = ch
            cur += ch
        elif ch == "(":
            depth += 1
            cur += ch
        elif ch == ")":
            depth -= 1
            cur += ch
        elif depth == 0 and s[i : i + len(op)] == op:
            parts.append(cur)
            cur = ""
            i += len(op)
            continue
        else:
            cur += ch
        i += 1
    parts.append(cur)
    return parts


def convert_template(src: str) -> str:
    out = src

    # 块标签转换
    # {{if cond}}            -> {% if cond %}
    # {{else}}               -> {% else %}
    # {{/if}}                -> {% endif %}
    # {{each arr item}}      -> {% for item in arr %}
    # {{each arr item idx}}  -> {% set item, idx = (arr | enumerate_items)[loop.index0] %}  —— 简化：仅支持 2 参
    # {{/each}}              -> {% endfor %}

    # {{each data.songs song}} / {{each data.songs song idx}}
    # index 参数在 Jinja 中用 loop.index0/loop.index 表达，忽略即可
    def repl_each(m):
        parts = m.group(1).strip().split()
        if len(parts) >= 2:
            return f"{{% for {parts[1]} in {_jsval_to_jinja(parts[0])} %}}"
        return m.group(0)

    out = re.sub(r"\{\{\s*each\s+([^}]+?)\s*\}\}", repl_each, out)
    out = re.sub(r"\{\{\s*/each\s*\}\}", "{% endfor %}", out)
    out = re.sub(
        r"\{\{\s*if\s+([^}]+?)\s*\}\}",
        lambda m: "{% if " + _jsval_to_jinja(m.group(1).strip(), is_cond=True) + " %}",
        out,
    )
    out = re.sub(r"\{\{\s*else\s*\}\}", "{% else %}", out)
    out = re.sub(r"\{\{\s*/if\s*\}\}", "{% endif %}", out)

    # 输出表达式（含三元、||、+）
    out = _convert_conditional(out)

    # 兜底：清理剩余普通 {{data.x}} 表达式中的 JS 残留（||、&&）
    def repl_plain(m):
        inner = m.group(1).strip()
        if inner.startswith("each") or inner.startswith("if") or inner.startswith("else") or inner.startswith("/"):
            return m.group(0)
        return "{{ " + _jsval_to_jinja(inner) + " }}"

    out = re.sub(r"\{\{\s*(?!each|if|else|/)([^{}]*)\}\}", repl_plain, out)

    return out


# 缓存已转换的模板
_cache: dict[str, str] = {}


def get_jinja_template(tpl_path: str) -> str:
    if tpl_path in _cache:
        return _cache[tpl_path]
    with open(tpl_path, "r", encoding="utf-8") as f:
        src = f.read()
    jinja = convert_template(src)
    _cache[tpl_path] = jinja
    return jinja
