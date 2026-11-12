"""Give Liquid ``assign`` its Liquid scope on top of Jinja's block-scoped ``set``.

Jinja keeps a ``set`` made inside ``for`` or ``capture`` local to that block, so
a Liquid ``assign`` there is lost after the loop and even between iterations.
Names assigned inside such a block are mirrored into one per-render store, read
back at the start of every iteration and after the block. Scopes built by
extensions (``nodes.Scope``, ``With``, ``CallBlock`` ...) stay local.

The store is reached through filters, which Jinja calls directly, because a
call expression goes through ``context.call`` and, in a sandbox, its checks.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, FrozenSet, Iterator, List, MutableMapping, Set

from jinja2 import nodes
from jinja2.environment import Environment
from jinja2.exceptions import TemplateSyntaxError

SCOPE_NAME = "_liquid_assign_scope"
_NEW_SCOPE = "_liquid_new_assign_scope"
_RESTORE = "_liquid_restore_assigned"
_STORE = "_liquid_store_assigned"
_SHARED_SCOPES = (nodes.For, nodes.AssignBlock)
_LOCAL_SCOPES = (
    nodes.CallBlock,
    nodes.FilterBlock,
    nodes.With,
    nodes.Scope,
    nodes.OverlayScope,
)


def _new_assign_scope(_: Any = None) -> Dict[str, Any]:
    return {}


def _restore_assigned(scope: MutableMapping[str, Any], name: str, current: Any) -> Any:
    return scope.get(name, current)


def _store_assigned(scope: MutableMapping[str, Any], name: str, value: Any) -> str:
    scope[name] = value
    return ""


ASSIGN_SCOPE_FILTERS = {
    _NEW_SCOPE: _new_assign_scope,
    _RESTORE: _restore_assigned,
    _STORE: _store_assigned,
}


def share_block_assignments(
    environment: Environment, template: nodes.Template
) -> nodes.Template:
    """Return ``template`` rewritten to keep block assignments, or unchanged."""
    for node in template.find_all(nodes.Filter):
        if node.name in ASSIGN_SCOPE_FILTERS:
            raise TemplateSyntaxError(f"Filter {node.name!r} is reserved", node.lineno)
    shared = frozenset(
        name
        for scoped in template.find_all(_SHARED_SCOPES)
        for name in _names_assigned_inside(scoped)
    )
    if not shared:
        return template
    for name, function in ASSIGN_SCOPE_FILTERS.items():
        environment.filters.setdefault(name, function)
    rewriter = _Rewriter(shared)
    rewritten = copy.copy(template)
    rewritten.body = [
        nodes.Assign(
            nodes.Name(SCOPE_NAME, "store"),
            _filter(_NEW_SCOPE, nodes.Const(None)),
        ).set_lineno(1),
        *rewriter.statements(template.body),
    ]
    return rewritten


def _filter(name: str, value: nodes.Expr, *args: nodes.Expr) -> nodes.Filter:
    return nodes.Filter(value, name, list(args), [], None, None)


def _names_assigned_inside(scoped: nodes.Node) -> Set[str]:
    return {
        name
        for body in _statement_lists(scoped)
        for statement in body
        for name in _names_assigned_by(statement)
    }


def _names_assigned_by(statement: nodes.Node) -> Iterator[str]:
    yield from _target_names(statement)
    if not isinstance(statement, _LOCAL_SCOPES):
        yield from _names_assigned_inside(statement)


def _target_names(node: nodes.Node) -> Set[str]:
    if not isinstance(node, (nodes.Assign, nodes.AssignBlock)):
        return set()
    target = node.target
    if isinstance(target, nodes.Name):
        return {target.name}
    return {name.name for name in target.find_all(nodes.Name) if name.ctx == "store"}


def _statement_lists(node: nodes.Node) -> List[List[nodes.Node]]:
    return [
        value
        for field in node.fields
        if isinstance(value := getattr(node, field), list)
        and value
        and all(isinstance(item, nodes.Stmt) for item in value)
    ]


class _Rewriter:
    def __init__(self, shared: FrozenSet[str]) -> None:
        self.shared = shared

    def statements(self, body: List[nodes.Node]) -> List[nodes.Node]:
        result: List[nodes.Node] = []
        for statement in body:
            result.append(self.node(statement))
            if isinstance(statement, _SHARED_SCOPES):
                inner = _names_assigned_inside(statement) & self.shared
                result.extend(
                    self.restore(name, statement.lineno) for name in sorted(inner)
                )
            for name in sorted(_target_names(statement) & self.shared):
                result.append(self.store(name, statement.lineno))
        return result

    def node(self, node: nodes.Node) -> nodes.Node:
        lists = _statement_lists(node)
        if not lists:
            return node
        rewritten = copy.copy(node)
        for field in node.fields:
            value = getattr(node, field)
            if any(value is body for body in lists):
                setattr(rewritten, field, self.statements(value))
        if isinstance(node, nodes.For) and isinstance(rewritten, nodes.For):
            loop_names = {name.name for name in node.target.find_all(nodes.Name)}
            if isinstance(node.target, nodes.Name):
                loop_names.add(node.target.name)
            refreshed = (_names_assigned_inside(node) & self.shared) - loop_names
            rewritten.body = [
                *(self.restore(name, node.lineno) for name in sorted(refreshed)),
                *rewritten.body,
            ]
        return rewritten

    def restore(self, name: str, lineno: int) -> nodes.Node:
        return nodes.Assign(
            nodes.Name(name, "store"),
            _filter(
                _RESTORE,
                nodes.Name(SCOPE_NAME, "load"),
                nodes.Const(name),
                nodes.Name(name, "load"),
            ),
        ).set_lineno(lineno)

    def store(self, name: str, lineno: int) -> nodes.Node:
        return nodes.ExprStmt(
            _filter(
                _STORE,
                nodes.Name(SCOPE_NAME, "load"),
                nodes.Const(name),
                nodes.Name(name, "load"),
            )
        ).set_lineno(lineno)
