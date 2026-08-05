"""Extension for Shopify mode."""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from jinja2 import nodes
from jinja2.exceptions import TemplateSyntaxError
from jinja2.runtime import Context
from markupsafe import Markup

from ..tags.shopify import shopify_tags
from .standard import LiquidStandardExtension


def _identity(value):
    return value


def _syntax_error(message, source, offset, name, filename):
    lineno = source.count("\n", 0, offset) + 1
    raise TemplateSyntaxError(message, lineno, name=name, filename=filename)


def _liquid_sentinel(left_trim, right_trim, label):
    left = "-" if left_trim else ""
    right = "-" if right_trim else ""
    return f"{{#{left} {label} {right}#}}"


def _expand_liquid_body(body, source, body_offset, name, filename):
    newline_matches = list(re.finditer(r"\r\n|\n|\r", body))
    if not newline_matches:
        _syntax_error(
            "The liquid tag requires each statement on its own line",
            source,
            body_offset,
            name,
            filename,
        )

    first_newline = newline_matches[0]
    last_newline = newline_matches[-1]
    opening_tail = body[: first_newline.start()]
    closing_indent = body[last_newline.end() :]
    if opening_tail.strip():
        _syntax_error(
            "The liquid tag requires each statement on its own line",
            source,
            body_offset,
            name,
            filename,
        )
    if closing_indent.strip():
        _syntax_error(
            "The liquid tag closing delimiter must be on its own line",
            source,
            body_offset + last_newline.end(),
            name,
            filename,
        )

    if first_newline is last_newline:
        inner = ""
    else:
        inner = body[first_newline.end() : last_newline.start()]

    expanded_lines = []
    line_offset = body_offset + first_newline.end()
    for line in inner.splitlines(keepends=True):
        statement = line.rstrip("\r\n").strip()
        newline = line[len(line.rstrip("\r\n")) :]
        if not statement or statement.startswith("#"):
            expanded_lines.append(f"{{#- -#}}{newline}")
        else:
            if statement == "liquid" or statement.startswith("liquid "):
                _syntax_error(
                    "Nested liquid tags are not supported",
                    source,
                    line_offset,
                    name,
                    filename,
                )
            expanded_lines.append(f"{{%- {statement} -%}}{newline}")
        line_offset += len(line)

    return (
        first_newline.group()
        + "".join(expanded_lines)
        + (last_newline.group() if first_newline is not last_newline else "")
    )


def _next_delimiter(source, cursor, delimiters):
    matches = (
        (offset, kind, start, end)
        for kind, start, end in delimiters
        if (offset := source.find(start, cursor)) >= 0
    )
    return min(matches, default=None)


def _expand_liquid_tags(
    source,
    name,
    filename,
    block_start,
    block_end,
    variable_start,
    variable_end,
    comment_start,
    comment_end,
):
    tag_pattern = re.compile(
        re.escape(block_start) + r"(?P<left>-?)\s*(?P<tag>comment|raw|liquid)\b"
    )
    delimiters = (
        ("block", block_start, block_end),
        ("variable", variable_start, variable_end),
        ("comment", comment_start, comment_end),
    )
    output = []
    cursor = 0

    while delimiter := _next_delimiter(source, cursor, delimiters):
        offset, kind, start, end = delimiter
        output.append(source[cursor:offset])
        delimiter_end = source.find(end, offset + len(start))
        if delimiter_end < 0:
            output.append(source[offset:])
            cursor = len(source)
            break

        if kind != "block":
            cursor = delimiter_end + len(end)
            output.append(source[offset:cursor])
            continue

        match = tag_pattern.match(source, offset)
        if match is None:
            cursor = delimiter_end + len(end)
            output.append(source[offset:cursor])
            continue

        tag = match.group("tag")
        opening_end = delimiter_end

        if tag in {"comment", "raw"}:
            closing_pattern = re.compile(
                re.escape(block_start)
                + r"-?\s*end"
                + tag
                + r"\s*-?"
                + re.escape(block_end)
            )
            closing_match = closing_pattern.search(
                source,
                opening_end + len(block_end),
            )
            if closing_match is None:
                output.append(source[match.start() :])
                cursor = len(source)
                break
            output.append(source[offset : closing_match.end()])
            cursor = closing_match.end()
            continue

        right_trim = opening_end > match.end() and source[opening_end - 1] == "-"
        body_end = opening_end - 1 if right_trim else opening_end
        body = source[match.end() : body_end]
        expanded_body = _expand_liquid_body(
            body,
            source,
            match.end(),
            name,
            filename,
        )
        output.append(_liquid_sentinel(bool(match.group("left")), True, "liquid"))
        output.append(expanded_body)
        output.append(_liquid_sentinel(True, right_trim, "endliquid"))
        cursor = opening_end + len(block_end)

    output.append(source[cursor:])
    return "".join(output)


@dataclass(frozen=True)
class RenderCall:
    """A render dependency found while parsing a template."""

    template_name: Optional[str]
    line: int


class LiquidShopifyExtension(LiquidStandardExtension):
    """Shopify tags and their runtime behavior."""

    tag_manager = shopify_tags

    def __init__(self, environment):
        super().__init__(environment)
        self._render_calls: Dict[Optional[str], List[RenderCall]] = {}
        environment.extend(
            liquid_render_context_names=(),
            liquid_render_template_resolver=_identity,
        )

    def preprocess(self, source, name, filename):
        source = _expand_liquid_tags(
            source,
            name,
            filename,
            self.environment.block_start_string,
            self.environment.block_end_string,
            self.environment.variable_start_string,
            self.environment.variable_end_string,
            self.environment.comment_start_string,
            self.environment.comment_end_string,
        )
        return super().preprocess(source, name, filename)

    def parse(self, parser):
        token = next(parser.stream)
        if token.value == "render":
            return self._parse_render(parser, token)
        if token.value == "style":
            return self._parse_style(parser, token)
        return self.__class__.tag_manager.parse(
            self.environment,
            token,
            parser,
        )

    def render_calls(self, template_name: Optional[str]) -> Tuple[RenderCall, ...]:
        """Return render dependencies collected for a parsed template."""
        return tuple(self._render_calls.get(template_name, ()))

    def _parse_render(self, parser, token):
        template = parser.parse_expression()
        arguments = {}
        while parser.stream.skip_if("comma"):
            name = parser.stream.expect("name")
            parser.stream.expect("colon")
            if name.value in arguments:
                raise TemplateSyntaxError(
                    f"Duplicate argument {name.value!r}",
                    name.lineno,
                )
            arguments[name.value] = parser.parse_expression()
        if not parser.stream.current.test("block_end"):
            raise TemplateSyntaxError(
                "Expected a comma-separated named argument",
                token.lineno,
            )

        template_name = None
        if isinstance(template, nodes.Const) and isinstance(template.value, str):
            template_name = self.environment.liquid_render_template_resolver(
                template.value
            )
        self._render_calls.setdefault(parser.name, []).append(
            RenderCall(template_name, token.lineno)
        )
        argument_dict = nodes.Dict(
            [nodes.Pair(nodes.Const(name), value) for name, value in arguments.items()]
        )
        return nodes.Output(
            [
                self.call_method(
                    "_render_async" if self.environment.is_async else "_render",
                    [nodes.ContextReference(), template, argument_dict],
                    lineno=token.lineno,
                )
            ],
            lineno=token.lineno,
        )

    def _parse_style(self, parser, token):
        if not parser.stream.current.test("block_end"):
            raise TemplateSyntaxError(
                "The style tag does not accept arguments",
                token.lineno,
            )
        body = parser.parse_statements(("name:endstyle",), drop_needle=True)
        call = self.call_method(
            "_render_style_async" if self.environment.is_async else "_render_style",
            lineno=token.lineno,
        )
        return nodes.CallBlock(call, [], [], body, lineno=token.lineno)

    def _render(
        self,
        context: Context,
        template_name: str,
        arguments: Dict[str, Any],
    ) -> Markup:
        names = self.environment.liquid_render_context_names
        snippet_context = dict(arguments)
        snippet_context.update({name: context.resolve(name) for name in names})
        resolved_name = self.environment.liquid_render_template_resolver(template_name)
        template = self.environment.get_template(resolved_name)
        return Markup(template.render(snippet_context))

    async def _render_async(
        self,
        context: Context,
        template_name: str,
        arguments: Dict[str, Any],
    ) -> Markup:
        names = self.environment.liquid_render_context_names
        snippet_context = dict(arguments)
        snippet_context.update({name: context.resolve(name) for name in names})
        resolved_name = self.environment.liquid_render_template_resolver(template_name)
        template = self.environment.get_template(resolved_name)
        return Markup(await template.render_async(snippet_context))

    @staticmethod
    def _render_style(caller) -> Markup:
        return Markup(f"<style>{caller()}</style>")

    @staticmethod
    async def _render_style_async(caller) -> Markup:
        return Markup(f"<style>{await caller()}</style>")
