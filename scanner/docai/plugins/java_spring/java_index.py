"""
Structural index of Java sources built from tree-sitter trees.

Every type (class, interface, enum, record, annotation) is recorded with its
annotations, fields, constructors and methods. Method bodies are reduced to
the facts the analyzer needs: call chains, object creations, thrown
exceptions and local variable types.

Expressions that matter for analysis (annotation arguments, call arguments,
field initializers) are kept as small tuples ("Expr"):

    ("str", "text")              string / char literal
    ("lambda", "source text")    lambda expression
    ("concat", [Expr, ...])      string concatenation with +
    ("ref", "NAME" | "A.B")      identifier or dotted constant reference
    ("class", "TypeName")        Foo.class
    ("lit", value)               number / boolean / null
    ("list", [Expr, ...])        {a, b} array initializer
    ("ann", Annotation)          nested annotation
    ("call", name, [Expr], recv) method call (recv is text or None)
    ("new", "TypeName", [Expr])  object creation
    ("expr", "source text")      anything else
"""
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from docai.core.treesitter import get_text

PRIMITIVES = {
    "byte", "short", "int", "long", "float", "double", "boolean", "char", "void"
}

TYPE_DECLARATIONS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "annotation",
}

COMMENT_TYPES = {"line_comment", "block_comment"}


# ============================
# DATA MODEL
# ============================

@dataclass
class TypeRef:
    name: str                      # simple base name: "List", "UserDto", "int"
    args: List["TypeRef"] = field(default_factory=list)
    array: int = 0
    raw: str = ""

    def __str__(self):
        return self.raw or self.name


@dataclass
class Annotation:
    name: str                      # simple name, e.g. "GetMapping"
    args: Dict[str, tuple] = field(default_factory=dict)
    raw: str = ""

    def arg(self, *keys):
        """First present argument among keys (positional value is 'value')."""
        for key in keys:
            if key in self.args:
                return self.args[key]
        return None


@dataclass
class FieldInfo:
    name: str
    type: TypeRef
    annotations: List[Annotation] = field(default_factory=list)
    modifiers: List[str] = field(default_factory=list)
    init: Optional[tuple] = None
    line: int = 0

    @property
    def is_static(self):
        return "static" in self.modifiers


@dataclass
class ParamInfo:
    name: str
    type: TypeRef
    annotations: List[Annotation] = field(default_factory=list)


@dataclass
class Call:
    name: str
    args: Optional[List[tuple]]    # None for method references (repo::save)
    line: int


@dataclass
class Chain:
    """a.b(x).c(y) -> root=("name", "a"), calls=[b(x), c(y)]"""
    root: tuple
    calls: List[Call]
    is_reference: bool = False


@dataclass
class Creation:
    type: TypeRef
    args: List[tuple]
    line: int


@dataclass
class MethodInfo:
    name: str
    owner: str                     # qualified name of the declaring type
    params: List[ParamInfo] = field(default_factory=list)
    return_type: Optional[TypeRef] = None
    annotations: List[Annotation] = field(default_factory=list)
    modifiers: List[str] = field(default_factory=list)
    throws: List[TypeRef] = field(default_factory=list)
    is_constructor: bool = False
    line_start: int = 0
    line_end: int = 0
    javadoc: str = ""
    chains: List[Chain] = field(default_factory=list)
    creations: List[Creation] = field(default_factory=list)
    thrown: List[TypeRef] = field(default_factory=list)
    locals: Dict[str, Tuple[TypeRef, Optional[tuple]]] = field(default_factory=dict)
    returns: List[tuple] = field(default_factory=list)

    @property
    def id(self):
        # params never change after parsing, so the id is computed once
        cached = self.__dict__.get("_id")
        if cached is None:
            cached = self.__dict__["_id"] = f"{self.owner}#{self.name}({len(self.params)})"
        return cached


@dataclass
class TypeInfo:
    name: str
    qualified_name: str
    package: str
    kind: str                      # class / interface / enum / record / annotation
    file: str                      # path relative to the repository
    module: str
    line_start: int = 0
    line_end: int = 0
    annotations: List[Annotation] = field(default_factory=list)
    modifiers: List[str] = field(default_factory=list)
    extends: List[TypeRef] = field(default_factory=list)
    implements: List[TypeRef] = field(default_factory=list)
    type_params: List[str] = field(default_factory=list)
    fields: List[FieldInfo] = field(default_factory=list)
    methods: List[MethodInfo] = field(default_factory=list)
    constructors: List[MethodInfo] = field(default_factory=list)
    enum_constants: List[str] = field(default_factory=list)
    enum_args: Dict[str, List[tuple]] = field(default_factory=dict)
    outer: Optional[str] = None    # qualified name of the enclosing type
    javadoc: str = ""
    imports: Dict[str, str] = field(default_factory=dict)
    wildcard_imports: List[str] = field(default_factory=list)

    def annotation(self, *names):
        return find_annotation(self.annotations, *names)

    def has_annotation(self, *names):
        return self.annotation(*names) is not None

    def field_named(self, name):
        return next((f for f in self.fields if f.name == name), None)


def find_annotation(annotations, *names):
    return next((a for a in annotations if a.name in names), None)


# ============================
# PARSING
# ============================

class JavaFileParser:
    """Turns one parsed Java file into TypeInfo objects."""

    def __init__(self, code: bytes, rel_path: str, module: str):
        self.code = code
        self.rel_path = rel_path
        self.module = module
        self.package = ""
        self.imports: Dict[str, str] = {}
        self.wildcard_imports: List[str] = []

    def parse(self, root) -> List[TypeInfo]:
        types: List[TypeInfo] = []

        for node in root.children:
            if node.type == "package_declaration":
                name = next((c for c in node.named_children if "identifier" in c.type), None)
                if name:
                    self.package = self._text(name)

            elif node.type == "import_declaration":
                self._parse_import(node)

        for node in root.children:
            if node.type in TYPE_DECLARATIONS:
                self._parse_type(node, None, types)

        return types

    # ---------- helpers ----------

    def _text(self, node):
        return get_text(node, self.code)

    def _parse_import(self, node):
        text = self._text(node)
        is_static = " static " in f" {text} "
        name_node = next((c for c in node.named_children if "identifier" in c.type), None)

        if not name_node or is_static:
            return

        name = self._text(name_node)

        if any(c.type == "asterisk" for c in node.children):
            self.wildcard_imports.append(name)
        else:
            self.imports[name.rsplit(".", 1)[-1]] = name

    def _javadoc(self, node):
        prev = node.prev_sibling

        while prev is not None and prev.type == "line_comment":
            prev = prev.prev_sibling

        if prev is None or prev.type != "block_comment":
            return ""

        text = self._text(prev)

        if not text.startswith("/**"):
            return ""

        lines = []
        for line in text[3:-2].splitlines():
            line = line.strip().lstrip("*").strip()
            if line.startswith("@"):
                break
            if line:
                lines.append(line)

        return " ".join(lines)[:1000]

    def _modifiers(self, node):
        annotations, modifiers = [], []
        mods = next((c for c in node.children if c.type == "modifiers"), None)

        if mods is not None:
            for c in mods.children:
                if c.type in ("annotation", "marker_annotation"):
                    annotations.append(self.annotation(c))
                elif not c.is_named:
                    modifiers.append(c.type)

        return annotations, modifiers

    # ---------- types ----------

    def _parse_type(self, node, outer: Optional[TypeInfo], out: List[TypeInfo]):
        name = self._text(node.child_by_field_name("name"))
        kind = TYPE_DECLARATIONS[node.type]

        if outer:
            qualified = f"{outer.qualified_name}.{name}"
        else:
            qualified = f"{self.package}.{name}" if self.package else name

        annotations, modifiers = self._modifiers(node)

        info = TypeInfo(
            name=name,
            qualified_name=qualified,
            package=self.package,
            kind=kind,
            file=self.rel_path,
            module=self.module,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            annotations=annotations,
            modifiers=modifiers,
            outer=outer.qualified_name if outer else None,
            javadoc=self._javadoc(node),
            imports=self.imports,
            wildcard_imports=self.wildcard_imports,
        )

        superclass = node.child_by_field_name("superclass")
        if superclass is not None:
            info.extends = [self.type_ref(c) for c in superclass.named_children]

        for c in node.children:
            if c.type == "super_interfaces":
                info.implements = self._type_list(c)
            elif c.type == "extends_interfaces":
                # interface Foo extends A, B
                info.extends = self._type_list(c)
            elif c.type == "type_parameters":
                info.type_params = [
                    self._text(p.named_children[0]) for p in c.named_children if p.named_children
                ]

        if kind == "record":
            params = node.child_by_field_name("parameters")
            if params is not None:
                for p in self._params(params):
                    info.fields.append(FieldInfo(p.name, p.type, p.annotations, ["private", "final"]))

        out.append(info)

        body = node.child_by_field_name("body")
        if body is not None:
            self._parse_body(body, info, out)

    def _type_list(self, node):
        type_list = next((c for c in node.named_children if c.type == "type_list"), node)
        return [self.type_ref(c) for c in type_list.named_children]

    def _parse_body(self, body, info: TypeInfo, out: List[TypeInfo]):
        for member in body.named_children:
            t = member.type

            if t == "field_declaration" or t == "constant_declaration":
                self._parse_field(member, info)

            elif t == "method_declaration":
                info.methods.append(self._parse_method(member, info))

            elif t in ("constructor_declaration", "compact_constructor_declaration"):
                info.constructors.append(self._parse_method(member, info, constructor=True))

            elif t in TYPE_DECLARATIONS:
                self._parse_type(member, info, out)

            elif t == "enum_constant":
                name = self._text(member.child_by_field_name("name"))
                info.enum_constants.append(name)
                info.enum_args[name] = self._args(member.child_by_field_name("arguments"))

            elif t == "enum_body_declarations":
                self._parse_body(member, info, out)

    def _parse_field(self, node, info: TypeInfo):
        annotations, modifiers = self._modifiers(node)
        type_ref = self.type_ref(node.child_by_field_name("type"))

        if info.kind == "interface":
            modifiers = list(set(modifiers) | {"static", "final"})

        for c in node.children:
            if c.type != "variable_declarator":
                continue

            value = c.child_by_field_name("value")

            info.fields.append(FieldInfo(
                name=self._text(c.child_by_field_name("name")),
                type=type_ref,
                annotations=annotations,
                modifiers=modifiers,
                init=self.expr(value) if value is not None else None,
                line=node.start_point[0] + 1,
            ))

    def _params(self, params_node):
        params = []

        for p in params_node.named_children:
            if p.type not in ("formal_parameter", "spread_parameter"):
                continue

            annotations, _ = self._modifiers(p)
            type_node = p.child_by_field_name("type")
            name_node = p.child_by_field_name("name")

            if p.type == "spread_parameter":
                type_node = next((c for c in p.named_children if c.type not in ("modifiers", "variable_declarator")), None)
                declarator = next((c for c in p.named_children if c.type == "variable_declarator"), None)
                name_node = declarator.child_by_field_name("name") if declarator else None

            if type_node is None or name_node is None:
                continue

            type_ref = self.type_ref(type_node)
            if p.type == "spread_parameter":
                type_ref.array += 1

            params.append(ParamInfo(self._text(name_node), type_ref, annotations))

        return params

    def _parse_method(self, node, info: TypeInfo, constructor=False):
        annotations, modifiers = self._modifiers(node)

        if info.kind == "interface" and "default" not in modifiers and "static" not in modifiers:
            modifiers = modifiers + ["abstract"]

        name_node = node.child_by_field_name("name")
        params_node = node.child_by_field_name("parameters")
        type_node = node.child_by_field_name("type")

        method = MethodInfo(
            name=self._text(name_node) if name_node is not None else info.name,
            owner=info.qualified_name,
            params=self._params(params_node) if params_node is not None else [],
            return_type=self.type_ref(type_node) if type_node is not None else None,
            annotations=annotations,
            modifiers=modifiers,
            is_constructor=constructor,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            javadoc=self._javadoc(node),
        )

        for c in node.children:
            if c.type == "throws":
                method.throws = [self.type_ref(t) for t in c.named_children]

        body = node.child_by_field_name("body")
        if body is not None:
            self._scan_body(body, method)

        return method

    # ---------- method bodies ----------

    def _scan_body(self, body, method: MethodInfo):
        stack = [body]

        while stack:
            n = stack.pop()
            t = n.type

            if t == "method_invocation":
                parent = n.parent
                is_inner = (
                    parent is not None
                    and parent.type == "method_invocation"
                    and parent.child_by_field_name("object") == n
                )
                if not is_inner:
                    method.chains.append(self._chain(n))

            elif t == "method_reference":
                chain = self._method_reference(n)
                if chain:
                    method.chains.append(chain)

            elif t == "object_creation_expression":
                type_node = n.child_by_field_name("type")
                if type_node is not None:
                    method.creations.append(Creation(
                        self.type_ref(type_node),
                        self._args(n.child_by_field_name("arguments")),
                        n.start_point[0] + 1,
                    ))

            elif t == "throw_statement":
                thrown = next((c for c in n.named_children if c.type not in COMMENT_TYPES), None)
                if thrown is not None and thrown.type == "object_creation_expression":
                    method.thrown.append(self.type_ref(thrown.child_by_field_name("type")))

            elif t == "return_statement":
                value = next((c for c in n.named_children if c.type not in COMMENT_TYPES), None)
                if value is not None:
                    method.returns.append(self.expr(value))

            elif t == "local_variable_declaration":
                type_ref = self.type_ref(n.child_by_field_name("type"))
                for c in n.children:
                    if c.type == "variable_declarator":
                        value = c.child_by_field_name("value")
                        init = self.expr(value) if value is not None else None
                        local_type = type_ref
                        if type_ref.name == "var" and init and init[0] == "new":
                            local_type = TypeRef(init[1], raw=init[1])
                        method.locals[self._text(c.child_by_field_name("name"))] = (local_type, init)

            elif t == "enhanced_for_statement":
                type_node, name_node = n.child_by_field_name("type"), n.child_by_field_name("name")
                if type_node is not None and name_node is not None:
                    method.locals[self._text(name_node)] = (self.type_ref(type_node), None)

            elif t == "catch_formal_parameter":
                catch_type = next((c for c in n.named_children if c.type == "catch_type"), None)
                name_node = n.child_by_field_name("name")
                if catch_type is not None and catch_type.named_children and name_node is not None:
                    method.locals[self._text(name_node)] = (self.type_ref(catch_type.named_children[0]), None)

            elif t in TYPE_DECLARATIONS:
                # local classes are not followed
                continue

            stack.extend(n.children)

    def _args(self, args_node):
        if args_node is None:
            return []
        return [self.expr(a) for a in args_node.named_children if a.type not in COMMENT_TYPES]

    def _chain(self, top) -> Chain:
        calls = []
        n = top
        root: tuple = ("none",)

        while True:
            calls.append(Call(
                self._text(n.child_by_field_name("name")),
                self._args(n.child_by_field_name("arguments")),
                n.start_point[0] + 1,
            ))

            obj = n.child_by_field_name("object")

            if obj is None:
                break

            if obj.type == "method_invocation":
                n = obj
                continue

            root = self.receiver(obj)
            break

        calls.reverse()
        return Chain(root, calls)

    def _method_reference(self, node) -> Optional[Chain]:
        named = [c for c in node.children if c.type not in COMMENT_TYPES]
        if len(named) < 3:
            return None

        target, name = named[0], named[-1]
        method_name = "new" if name.type == "new" else self._text(name)

        return Chain(self.receiver(target), [Call(method_name, None, node.start_point[0] + 1)], is_reference=True)

    def receiver(self, node) -> tuple:
        t = node.type

        if t == "this":
            return ("this",)
        if t == "super":
            return ("super",)
        if t == "identifier":
            return ("name", self._text(node))
        if t in ("type_identifier", "scoped_type_identifier", "generic_type"):
            ref = self.type_ref(node)
            return ("type", ref.name, ref.raw)
        if t == "field_access":
            obj = node.child_by_field_name("object")
            fld = self._text(node.child_by_field_name("field"))
            if obj is not None and obj.type == "this":
                return ("field", fld)
            text = re.sub(r"\s+", "", self._text(node))
            if re.fullmatch(r"[\w.]+", text):
                return ("ref", text)
            return ("expr", text[:200])
        if t == "object_creation_expression":
            ref = self.type_ref(node.child_by_field_name("type"))
            return ("new", ref.name, ref.raw)
        if t == "cast_expression":
            ref = self.type_ref(node.child_by_field_name("type"))
            return ("cast", ref.name, ref.raw)
        if t == "parenthesized_expression" and node.named_children:
            return self.receiver(node.named_children[0])
        if t == "string_literal":
            return ("type", "String", "String")

        return ("expr", self._text(node)[:200])

    # ---------- types & expressions ----------

    def type_ref(self, node) -> TypeRef:
        if node is None:
            return TypeRef("Object", raw="Object")

        t = node.type
        raw = re.sub(r"\s+", "", self._text(node))

        if t == "generic_type":
            base = node.named_children[0]
            args_node = next((c for c in node.named_children if c.type == "type_arguments"), None)
            args = [self.type_ref(a) for a in args_node.named_children] if args_node else []
            return TypeRef(self.type_ref(base).name, args, 0, raw)

        if t == "array_type":
            inner = self.type_ref(node.child_by_field_name("element"))
            dims = node.child_by_field_name("dimensions")
            count = self._text(dims).count("[") if dims is not None else 1
            return TypeRef(inner.name, inner.args, inner.array + count, raw)

        if t == "scoped_type_identifier":
            return TypeRef(raw.rsplit(".", 1)[-1], raw=raw)

        if t == "wildcard":
            bound = next((c for c in node.named_children if c.type != "annotation"), None)
            return self.type_ref(bound) if bound is not None else TypeRef("Object", raw="?")

        if t == "annotated_type":
            inner = next((c for c in node.named_children if c.type not in ("annotation", "marker_annotation")), None)
            return self.type_ref(inner)

        return TypeRef(raw, raw=raw)

    def annotation(self, node) -> Annotation:
        name = self._text(node.child_by_field_name("name")).rsplit(".", 1)[-1]
        ann = Annotation(name=name, raw=self._text(node)[:300])
        args = node.child_by_field_name("arguments")

        if args is None:
            return ann

        for c in args.named_children:
            if c.type in COMMENT_TYPES:
                continue
            if c.type == "element_value_pair":
                key = self._text(c.child_by_field_name("key"))
                ann.args[key] = self.expr(c.child_by_field_name("value"))
            else:
                ann.args["value"] = self.expr(c)

        return ann

    def string_value(self, node):
        text = self._text(node)

        if text.startswith('"""'):
            body = text[3:-3]
            lines = body.splitlines()
            return "\n".join(line.strip() for line in lines).strip()

        if len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]:
            text = text[1:-1]

        return text.replace('\\"', '"').replace("\\\\", "\\")

    def expr(self, node) -> tuple:
        if node is None:
            return ("expr", "")

        t = node.type

        if t in ("string_literal", "character_literal", "text_block"):
            return ("str", self.string_value(node))

        if t == "binary_expression":
            op = node.child_by_field_name("operator")
            if op is not None and self._text(op) == "+":
                parts = []
                for side in (node.child_by_field_name("left"), node.child_by_field_name("right")):
                    e = self.expr(side)
                    parts.extend(e[1] if e[0] == "concat" else [e])
                return ("concat", parts)
            return ("expr", self._text(node)[:200])

        if t == "identifier":
            return ("ref", self._text(node))

        if t == "field_access":
            text = re.sub(r"\s+", "", self._text(node))
            if re.fullmatch(r"[\w.]+", text):
                return ("ref", text.replace("this.", "", 1) if text.startswith("this.") else text)
            return ("expr", text[:200])

        if t == "class_literal":
            return ("class", self.type_ref(node.named_children[0]).name if node.named_children else "Object")

        if t in ("true", "false"):
            return ("lit", t == "true")

        if t == "null_literal":
            return ("lit", None)

        if t.endswith("integer_literal") or t.endswith("floating_point_literal"):
            text = self._text(node).rstrip("lLfFdD").replace("_", "")
            try:
                return ("lit", float(text) if "." in text else int(text, 0))
            except ValueError:
                return ("lit", text)

        if t in ("element_value_array_initializer", "array_initializer"):
            return ("list", [self.expr(c) for c in node.named_children if c.type not in COMMENT_TYPES])

        if t == "array_creation_expression":
            init = next((c for c in node.named_children if c.type == "array_initializer"), None)
            if init is not None:
                return self.expr(init)

        if t in ("annotation", "marker_annotation"):
            return ("ann", self.annotation(node))

        if t in ("parenthesized_expression", "cast_expression"):
            inner = node.child_by_field_name("value") if t == "cast_expression" else None
            inner = inner or next((c for c in node.named_children if c.type not in COMMENT_TYPES and "type" not in c.type), None)
            if inner is not None:
                return self.expr(inner)

        if t == "method_invocation":
            obj = node.child_by_field_name("object")
            return (
                "call",
                self._text(node.child_by_field_name("name")),
                self._args(node.child_by_field_name("arguments")),
                re.sub(r"\s+", "", self._text(obj)) if obj is not None else None,
            )

        if t == "lambda_expression":
            return ("lambda", self._text(node)[:500])

        if t == "object_creation_expression":
            return (
                "new",
                self.type_ref(node.child_by_field_name("type")).name,
                self._args(node.child_by_field_name("arguments")),
            )

        return ("expr", self._text(node)[:200])


# ============================
# INDEX
# ============================

class JavaIndex:
    """All types of a repository with name resolution helpers."""

    def __init__(self):
        self.types: Dict[str, TypeInfo] = {}
        self.by_simple_name: Dict[str, List[TypeInfo]] = {}
        self.files: Dict[str, List[TypeInfo]] = {}
        self.subtypes: Dict[str, List[TypeInfo]] = {}

    def add_file(self, rel_path, types: List[TypeInfo]):
        self.files[rel_path] = types

        for t in types:
            self.types[t.qualified_name] = t
            self.by_simple_name.setdefault(t.name, []).append(t)

    def finalize(self):
        """Build the subtype map once every file is indexed."""
        self.subtypes = {}
        self._supertypes_cache = {}
        self._subtypes_cache = {}

        for t in self.types.values():
            for parent_ref in t.extends + t.implements:
                parent = self.resolve(parent_ref.name, t)
                if parent:
                    self.subtypes.setdefault(parent.qualified_name, []).append(t)

    # ---------- resolution ----------

    def resolve(self, simple_name: str, context: Optional[TypeInfo] = None) -> Optional[TypeInfo]:
        """Resolve a simple (or dotted) type name as seen from `context`."""
        if not simple_name or simple_name in PRIMITIVES:
            return None

        if "." in simple_name:
            if simple_name in self.types:
                return self.types[simple_name]
            simple_name = simple_name.rsplit(".", 1)[-1]

        candidates = self.by_simple_name.get(simple_name)

        if not candidates:
            return None

        if len(candidates) == 1 or context is None:
            return candidates[0]

        # nested in the context type or its outer types
        scope = context
        while scope is not None:
            nested = self.types.get(f"{scope.qualified_name}.{simple_name}")
            if nested:
                return nested
            scope = self.types.get(scope.outer) if scope.outer else None

        imported = context.imports.get(simple_name)
        if imported and imported in self.types:
            return self.types[imported]

        for c in candidates:
            if c.package == context.package and c.outer is None:
                return c

        for pkg in context.wildcard_imports:
            if f"{pkg}.{simple_name}" in self.types:
                return self.types[f"{pkg}.{simple_name}"]

        same_module = [c for c in candidates if c.module == context.module]
        return (same_module or candidates)[0]

    def supertypes(self, t: TypeInfo) -> List[TypeInfo]:
        """Indexed ancestors of t (classes and interfaces), nearest first."""
        cache = self.__dict__.setdefault("_supertypes_cache", {})
        if t.qualified_name not in cache:
            cache[t.qualified_name] = self._supertypes(t)
        return cache[t.qualified_name]

    def _supertypes(self, t: TypeInfo) -> List[TypeInfo]:
        seen, out, queue = {t.qualified_name}, [], [t]

        while queue:
            current = queue.pop(0)
            for ref in current.extends + current.implements:
                parent = self.resolve(ref.name, current)
                if parent and parent.qualified_name not in seen:
                    seen.add(parent.qualified_name)
                    out.append(parent)
                    queue.append(parent)

        return out

    def ancestor_refs(self, t: TypeInfo) -> List[TypeRef]:
        """Every extends/implements reference of t and its indexed ancestors."""
        refs = list(t.extends + t.implements)
        for parent in self.supertypes(t):
            refs.extend(parent.extends + parent.implements)
        return refs

    def all_subtypes(self, t: TypeInfo) -> List[TypeInfo]:
        cache = self.__dict__.setdefault("_subtypes_cache", {})
        if t.qualified_name not in cache:
            cache[t.qualified_name] = self._all_subtypes(t)
        return cache[t.qualified_name]

    def _all_subtypes(self, t: TypeInfo) -> List[TypeInfo]:
        seen, out, queue = {t.qualified_name}, [], [t]

        while queue:
            current = queue.pop(0)
            for sub in self.subtypes.get(current.qualified_name, []):
                if sub.qualified_name not in seen:
                    seen.add(sub.qualified_name)
                    out.append(sub)
                    queue.append(sub)

        return out

    def is_subtype_of(self, t: TypeInfo, ancestor_name: str) -> bool:
        if t.name == ancestor_name:
            return True
        return any(ref.name == ancestor_name for ref in self.ancestor_refs(t))

    def find_field(self, t: TypeInfo, name: str) -> Optional[Tuple[FieldInfo, TypeInfo]]:
        """Field declared in t, its ancestors or enclosing types."""
        scope = t
        while scope is not None:
            for owner in [scope] + self.supertypes(scope):
                f = owner.field_named(name)
                if f:
                    return f, owner
            scope = self.types.get(scope.outer) if scope.outer else None
        return None

    def find_methods(self, t: TypeInfo, name: str, argc: Optional[int]) -> List[MethodInfo]:
        """Methods called `name` visible on t (own, inherited, or implemented by subtypes)."""

        def matching(owner):
            ms = [m for m in owner.methods if m.name == name]
            if argc is not None:
                exact = [m for m in ms if len(m.params) == argc or (m.params and m.params[-1].type.array and argc >= len(m.params) - 1)]
                ms = exact or ms
            return ms

        own = []
        for owner in [t] + self.supertypes(t):
            own = matching(owner)
            if own:
                break

        # Calls through interfaces/abstract methods dispatch to implementations
        if not own or all("abstract" in m.modifiers for m in own):
            impls = []
            for sub in self.all_subtypes(t):
                impls.extend(m for m in matching(sub) if "abstract" not in m.modifiers)
            if impls:
                return own + impls

        return own


def module_for(rel_path: str, module_dirs: List[str]) -> str:
    """Longest module directory containing rel_path ('' is the root module)."""
    best = ""
    for d in module_dirs:
        if d and (rel_path == d or rel_path.startswith(d + "/")) and len(d) > len(best):
            best = d
    return best


def is_test_source(rel_path: str) -> bool:
    parts = rel_path.replace(os.sep, "/").split("/")
    return any(
        parts[i] == "src" and i + 1 < len(parts) and parts[i + 1] in ("test", "it", "integrationTest", "testFixtures")
        for i in range(len(parts))
    )
