"""Give Liquid ``assign`` its Liquid scope on top of Jinja's block-scoped ``set``.

Jinja keeps a ``set`` made inside ``for`` or ``capture`` local to that block, so
a Liquid ``assign`` there is lost after the loop and even between iterations.
Each such block stores the current value of the names assigned inside it before
it starts, stores them again after every assignment, and reads them back at the
start of every iteration and after the block. The store lives on the render's
``Context``. Scopes built by extensions (``nodes.Scope``, ``With``,
``CallBlock`` ...) stay local.

The store is reached through context filters, which Jinja calls directly,
because a call expression goes through ``context.call`` and, in a sandbox, its
checks. Their names are not valid filter names in template source.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Set, Tuple

from jinja2 import nodes, pass_context
from jinja2.environment import Environment
from jinja2.runtime import Context

_RESTORE = "liquid-restore-assigned"
_STORE = "liquid-store-assigned"
_BLOCKS = (nodes.For, nodes.AssignBlock)
_ASSIGNMENTS = (nodes.Assign, nodes.AssignBlock)
_LOCAL_SCOPES = (
    nodes.CallBlock,
    nodes.FilterBlock,
    nodes.With,
    nodes.Scope,
    nodes.OverlayScope,
)


def _scope(context: Context) -> Dict[str, Any]:
    try:
        return context.liquid_assign_scope
    except AttributeError:
        scope: Dict[str, Any] = {}
        context.liquid_assign_scope = scope
        return scope


@pass_context
def _restore_assigned(context: Context, name: str, current: Any) -> Any:
    return _scope(context).get(name, current)


@pass_context
def _store_assigned(context: Context, name: str, value: Any) -> str:
    _scope(context)[name] = value
    return ""


def install_assign_scope_filters(environment: Environment) -> None:
    environment.filters[_RESTORE] = _restore_assigned
    environment.filters[_STORE] = _store_assigned


def share_block_assignments(
    environment: Environment, template: nodes.Template
) -> nodes.Template:
    """Return ``template`` rewritten to keep block assignments, or unchanged."""
    rewriter = _Rewriter(environment)
    body = rewriter.statements(template.body, in_block=False)
    if body is template.body:
        return template
    rewritten = copy.copy(template)
    rewritten.body = body
    return rewritten


class _Rewriter:
    def __init__(self, environment: Environment) -> None:
        self.environment = environment
        self._inside: Dict[int, FrozenSet[str]] = {}

    def statements(self, body: List[nodes.Node], in_block: bool) -> List[nodes.Node]:
        result: Optional[List[nodes.Node]] = None
        for index, statement in enumerate(body):
            rewritten = self.node(statement, in_block)
            names = self.inside(statement) if type(statement) in _BLOCKS else ()
            stored = _target_names(statement) if in_block else ()
            if result is None:
                if rewritten is statement and not names and not stored:
                    continue
                result = body[:index]
            line = statement.lineno
            result.extend(self.store(name, line) for name in sorted(names))
            result.append(rewritten)
            result.extend(self.restore(name, line) for name in sorted(names))
            result.extend(self.store(name, line) for name in sorted(stored))
        return body if result is None else result

    def node(self, node: nodes.Node, in_block: bool) -> nodes.Node:
        lists = _statement_lists(node)
        if not lists:
            return node
        if isinstance(node, _LOCAL_SCOPES):
            in_block = False
        elif isinstance(node, _BLOCKS):
            in_block = True
        changes = {}
        for field, body in lists:
            new_body = self.statements(body, in_block)
            if new_body is not body:
                changes[field] = new_body
        refreshed: List[str] = []
        if type(node) is nodes.For:
            refreshed = sorted(self.inside(node) - _loop_names(node))
        if not changes and not refreshed:
            return node
        rewritten = copy.copy(node)
        for field, new_body in changes.items():
            setattr(rewritten, field, new_body)
        if refreshed:
            rewritten.body = [
                *(self.restore(name, node.lineno) for name in refreshed),
                *rewritten.body,
            ]
        return rewritten

    def inside(self, node: nodes.Node) -> FrozenSet[str]:
        """Names assigned below ``node``, outside the local scopes it contains."""
        names = self._inside.get(id(node))
        if names is None:
            names = frozenset(
                name
                for _, body in _statement_lists(node)
                for statement in body
                for name in self._assigned_by(statement)
            )
            self._inside[id(node)] = names
        return names

    def _assigned_by(self, statement: nodes.Node) -> Iterator[str]:
        yield from _target_names(statement)
        if not isinstance(statement, _LOCAL_SCOPES):
            yield from self.inside(statement)

    def restore(self, name: str, lineno: int) -> nodes.Node:
        return self._new(
            nodes.Assign(
                nodes.Name(name, "store"),
                _filter(_RESTORE, name),
            ),
            lineno,
        )

    def store(self, name: str, lineno: int) -> nodes.Node:
        return self._new(nodes.ExprStmt(_filter(_STORE, name)), lineno)

    def _new(self, node: nodes.Node, lineno: int) -> nodes.Node:
        return node.set_lineno(lineno).set_environment(self.environment)


def _filter(filter_name: str, name: str) -> nodes.Filter:
    return nodes.Filter(
        nodes.Const(name), filter_name, [nodes.Name(name, "load")], [], None, None
    )


def _target_names(node: nodes.Node) -> Set[str]:
    if type(node) not in _ASSIGNMENTS:
        return set()
    target = node.target
    if isinstance(target, nodes.Name):
        return {target.name}
    return {name.name for name in target.find_all(nodes.Name) if name.ctx == "store"}


def _loop_names(node: nodes.For) -> Set[str]:
    if isinstance(node.target, nodes.Name):
        return {node.target.name}
    return {name.name for name in node.target.find_all(nodes.Name)}


_STATEMENT_FIELDS: Dict[type, Tuple[str, ...]] = {
    nodes.Template: ("body",),
    nodes.For: ("body", "else_"),
    nodes.If: ("body", "elif_", "else_"),
    nodes.Macro: ("body",),
    nodes.CallBlock: ("body",),
    nodes.FilterBlock: ("body",),
    nodes.With: ("body",),
    nodes.Block: ("body",),
    nodes.AssignBlock: ("body",),
    nodes.Scope: ("body",),
    nodes.OverlayScope: ("body",),
    nodes.ScopedEvalContextModifier: ("body",),
    **dict.fromkeys(
        (
            nodes.Output,
            nodes.Extends,
            nodes.Include,
            nodes.Import,
            nodes.FromImport,
            nodes.ExprStmt,
            nodes.Assign,
            nodes.Continue,
            nodes.Break,
            nodes.EvalContextModifier,
        ),
        (),
    ),
}


def _statement_lists(node: nodes.Node) -> List[Tuple[str, List[nodes.Node]]]:
    fields = _STATEMENT_FIELDS.get(type(node))
    if fields is None:
        return [
            (field, value)
            for field in node.fields
            if isinstance(value := getattr(node, field), list)
            and value
            and all(isinstance(item, nodes.Stmt) for item in value)
        ]
    return [(field, value) for field in fields if (value := getattr(node, field))]
