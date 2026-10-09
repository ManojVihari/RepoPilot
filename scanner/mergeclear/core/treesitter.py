"""Small helpers shared by the tree-sitter based extractors."""


def normalize_captures(captures):
    """
    Return [(node, capture_name)] for both tree-sitter capture formats:
    a dict {name: [nodes]} or a list of (node, name) / (name, node) tuples.
    """
    normalized = []

    if isinstance(captures, dict):
        for name, nodes in captures.items():
            for node in nodes:
                normalized.append((node, name))
    else:
        for item in captures:
            if hasattr(item[0], "start_byte"):
                normalized.append((item[0], item[1]))
            else:
                normalized.append((item[1], item[0]))

    return normalized


def get_parent(node, target_type):
    while node and node.type != target_type:
        node = node.parent
    return node


def get_text(node, code):
    return code[node.start_byte:node.end_byte].decode()


def reachable(func, graph, include_start_on_cycle):
    """
    Every function reachable from `func` through `graph` (caller -> callees).

    `func` itself is only included when it is reachable through a cycle and
    `include_start_on_cycle` is True.
    """
    seen = set()
    stack = list(graph.get(func, []))

    while stack:
        call = stack.pop()

        if call in seen:
            continue

        seen.add(call)
        stack.extend(graph.get(call, []))

    if not include_start_on_cycle:
        seen.discard(func)

    return list(seen)
