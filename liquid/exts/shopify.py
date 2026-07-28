"""Extension for Shopify mode."""

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
