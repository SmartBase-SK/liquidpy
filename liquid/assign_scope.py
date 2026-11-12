"""Give Liquid ``assign`` its Liquid scope on top of Jinja's block-scoped ``set``.

Jinja keeps a ``set`` made inside ``for`` or ``capture`` local to that block, so
a Liquid ``assign`` there is lost after the loop and even between iterations.
Names assigned inside such a block are mirrored into a store on the render's
``Context``, read back at the start of every iteration and after the block.
Scopes built by extensions (``nodes.Scope``, ``With``, ``CallBlock`` ...) stay
local.

The store is reached through context filters, which Jinja calls directly,
because a call expression goes through ``context.call`` and, in a sandbox, its
checks. The parser marks templates without such assigns, so compiling them
skips the AST walk.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, FrozenSet, Iterator, List, Set

from jinja2 import nodes, pass_context
from jinja2.environment import Environment
from jinja2.exceptions import TemplateSyntaxError
from jinja2.parser import Parser
from jinja2.runtime import Context

RESERVED_FILTER_PREFIX = "_liquid_"
_RESTORE = "_liquid_restore_assigned"
_STORE = "_liquid_store_assigned"
_SHARED_SCOPES = (nodes.For, nodes.AssignBlock)
_SHARED_SCOPE_END_TOKENS = frozenset(
    {"name:endfor", "name:endcapture", "name:endtablerow"}
)
_BLOCK_ASSIGNS = "liquid_block_assigns"
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


ASSIGN_SCOPE_FILTERS = {
    _RESTORE: _restore_assigned,
    _STORE: _store_assigned,
}


def note_assignment(parser: Parser) -> None:
    """Record that ``parser`` met an assignment inside a ``for`` or ``capture``."""
    if any(
        token in _SHARED_SCOPE_END_TOKENS
        for end_tokens in parser._end_token_stack
        for token in end_tokens
    ):
        setattr(parser, _BLOCK_ASSIGNS, True)


def mark_parsed_template(template: nodes.Template, parser: Parser) -> None:
    setattr(template, _BLOCK_ASSIGNS, getattr(parser, _BLOCK_ASSIGNS, False))


def reject_reserved_filters(template: nodes.Template) -> None:
    for node in template.find_all(nodes.Filter):
        if node.name in ASSIGN_SCOPE_FILTERS:
            raise TemplateSyntaxError(f"Filter {node.name!r} is reserved", node.lineno)


def share_block_assignments(
    environment: Environment, template: nodes.Template
) -> nodes.Template:
    """Return ``template`` rewritten to keep block assignments, or unchanged."""
    marked = getattr(template, _BLOCK_ASSIGNS, None)
    if marked is False:
        return template
    if marked is None:
        reject_reserved_filters(template)
    assigned = _AssignedNames()
    shared = assigned.shared(template)
    if not shared:
        return template
    for name, function in ASSIGN_SCOPE_FILTERS.items():
        environment.filters.setdefault(name, function)
    rewriter = _Rewriter(shared, assigned, environment)
    rewritten = copy.copy(template)
    rewritten.body = rewriter.statements(template.body)
    return rewritten


def _filter(name: str, value: nodes.Expr, *args: nodes.Expr) -> nodes.Filter:
    return nodes.Filter(value, name, list(args), [], None, None)


class _AssignedNames:
    """Names assigned inside a node, computed once per node."""

    def __init__(self) -> None:
        self._inside: Dict[int, FrozenSet[str]] = {}

    def inside(self, node: nodes.Node) -> FrozenSet[str]:
        names = self._inside.get(id(node))
        if names is None:
            names = frozenset(
                name
                for body in _statement_lists(node)
                for statement in body
                for name in self._by(statement)
            )
            self._inside[id(node)] = names
        return names

    def shared(self, node: nodes.Node) -> FrozenSet[str]:
        """Names assigned inside any ``for`` or ``capture`` below ``node``."""
        names: Set[str] = set()
        for body in _statement_lists(node):
            for statement in body:
                if isinstance(statement, _SHARED_SCOPES):
                    names |= self.inside(statement)
                names |= self.shared(statement)
        return frozenset(names)

    def _by(self, statement: nodes.Node) -> Iterator[str]:
        yield from _target_names(statement)
        if not isinstance(statement, _LOCAL_SCOPES):
            yield from self.inside(statement)


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
    def __init__(
        self,
        shared: FrozenSet[str],
        assigned: _AssignedNames,
        environment: Environment,
    ) -> None:
        self.shared = shared
        self.assigned = assigned
        self.environment = environment

    def new(self, node: nodes.Node, lineno: int) -> nodes.Node:
        return node.set_lineno(lineno).set_environment(self.environment)

    def statements(self, body: List[nodes.Node]) -> List[nodes.Node]:
        result: List[nodes.Node] = []
        for statement in body:
            result.append(self.node(statement))
            if isinstance(statement, _SHARED_SCOPES):
                inner = self.assigned.inside(statement) & self.shared
                result.extend(
                    self.restore(name, statement.lineno) for name in sorted(inner)
                )
            for name in sorted(_target_names(statement) & self.shared):
                result.append(self.store(name, statement.lineno))
        return result

    def node(self, node: nodes.Node) -> nodes.Node:
        lists = _statement_lists(node)
        if not lists or not self.assigned.inside(node) & self.shared:
            return node
        rewritten = copy.copy(node)
        for field in node.fields:
            value = getattr(node, field)
            if any(value is body for body in lists):
                setattr(rewritten, field, self.statements(value))
        if isinstance(node, nodes.For) and isinstance(rewritten, nodes.For):
            loop_names = (
                {node.target.name}
                if isinstance(node.target, nodes.Name)
                else {name.name for name in node.target.find_all(nodes.Name)}
            )
            refreshed = (self.assigned.inside(node) & self.shared) - loop_names
            rewritten.body = [
                *(self.restore(name, node.lineno) for name in sorted(refreshed)),
                *rewritten.body,
            ]
        return rewritten

    def restore(self, name: str, lineno: int) -> nodes.Node:
        return self.new(
            nodes.Assign(
                nodes.Name(name, "store"),
                _filter(_RESTORE, nodes.Const(name), nodes.Name(name, "load")),
            ),
            lineno,
        )

    def store(self, name: str, lineno: int) -> nodes.Node:
        return self.new(
            nodes.ExprStmt(
                _filter(_STORE, nodes.Const(name), nodes.Name(name, "load"))
            ),
            lineno,
        )
