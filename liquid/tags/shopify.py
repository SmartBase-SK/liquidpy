"""Provide shopify tags
see: https://shopify.dev/api/liquid/tags
"""

from jinja2 import nodes
from jinja2.exceptions import TemplateSyntaxError

from .manager import TagManager
from .standard import (
    comment,
    capture,
    assign,
    unless,
    case,
    tablerow,
    increment,
    decrement,
    cycle,
)

shopify_tags = TagManager()

shopify_tags.register(comment, raw=True)
shopify_tags.register(capture)
shopify_tags.register(assign)
shopify_tags.register(unless)
shopify_tags.register(case)
shopify_tags.register(tablerow)
shopify_tags.register(increment)
shopify_tags.register(decrement)
shopify_tags.register(cycle)


@shopify_tags.register
def echo(token, parser):
    """Output an expression from either a standalone or ``liquid`` tag."""
    return nodes.Output(
        [parser.parse_expression()],
        lineno=token.lineno,
    )


@shopify_tags.register
def render(token, parser):
    """Parsed by ``LiquidShopifyExtension`` to preserve isolated scope."""
    raise AssertionError("render must be parsed by LiquidShopifyExtension")


@shopify_tags.register
def style(token, parser):
    """Render the tag body as an inline style block."""
    if not parser.stream.current.test("block_end"):
        raise TemplateSyntaxError(
            "The style tag does not accept arguments",
            token.lineno,
        )
    body = parser.parse_statements(("name:endstyle",), drop_needle=True)
    return [
        nodes.Output(
            [nodes.TemplateData("<style>")],
            lineno=token.lineno,
        ),
        *body,
        nodes.Output(
            [nodes.TemplateData("</style>")],
            lineno=token.lineno,
        ),
    ]


# https://shopify.dev/api/liquid/tags/theme-tags
# TODO: form, layout, paginate, section
