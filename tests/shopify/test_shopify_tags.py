import asyncio

from jinja2 import DictLoader
from jinja2.exceptions import TemplateSyntaxError
import pytest

from liquid import Liquid, LiquidEnvironment, configure_environment
from liquid.exts.shopify import LiquidShopifyExtension


def test_liquid_tag_supports_assignments_and_control_flow(set_default_shopify):
    source = """{%- liquid
  assign og_title = page_title | default: shop.name
  assign og_url = canonical_url | default: request.origin
  assign og_type = 'website'
  assign og_description = page_description | default: shop.description | default: shop.name

  if request.page_type == 'product'
    assign og_type = 'product'
  elsif request.page_type == 'article'
    assign og_type = 'article'
  elsif request.page_type == 'password'
    assign og_url = request.origin
  endif
%}{{ og_title }}|{{ og_url }}|{{ og_type }}|{{ og_description }}"""
    template = Liquid(source)
    context = {
        "page_title": "Example",
        "canonical_url": "https://shop.test/page",
        "page_description": "Description",
        "shop": {"name": "Shop", "description": "Shop description"},
    }

    assert (
        template.render(
            **context,
            request={"origin": "https://shop.test", "page_type": "product"},
        )
        == "Example|https://shop.test/page|product|Description"
    )
    assert (
        template.render(
            **context,
            request={"origin": "https://shop.test", "page_type": "article"},
        )
        == "Example|https://shop.test/page|article|Description"
    )
    assert (
        template.render(
            **context,
            request={"origin": "https://shop.test", "page_type": "password"},
        )
        == "Example|https://shop.test|website|Description"
    )


def test_liquid_tag_supports_echo_case_and_line_comments(set_default_shopify):
    template = Liquid("""{% liquid
  # Choose a message from the product type.
  assign product_type = product.type | downcase
  case product_type
    when 'health'
      assign message = 'Health potion'
    when 'love'
      assign message = 'Love potion'
    else
      assign message = 'Potion'
  endcase
  echo message | upcase
%}""")

    assert template.render(product={"type": "Health"}) == "HEALTH POTION"


def test_liquid_tag_supports_capture_and_unless(set_default_shopify):
    template = Liquid("""{% liquid
  capture message
    echo product.title | upcase
  endcapture
  unless hidden
    echo message
  endunless
%}""")

    assert template.render(product={"title": "Health potion"}, hidden=False) == (
        "HEALTH POTION"
    )
    assert template.render(product={"title": "Health potion"}, hidden=True) == ""


def test_echo_supports_standalone_expressions(set_default_shopify):
    template = Liquid("{% echo product.title | upcase %}")

    assert template.render(product={"title": "Health potion"}) == "HEALTH POTION"


@pytest.mark.parametrize(
    ("opening", "closing", "expected"),
    [
        ("{% liquid", "%}", "A X B"),
        ("{%- liquid", "%}", "AX B"),
        ("{% liquid", "-%}", "A XB"),
        ("{%- liquid", "-%}", "AXB"),
    ],
)
def test_liquid_tag_preserves_outer_whitespace_control(
    set_default_shopify,
    opening,
    closing,
    expected,
):
    template = Liquid(f"A {opening}\n  echo 'X'\n{closing} B")

    assert template.render() == expected


def test_liquid_tag_supports_empty_and_comment_only_blocks(set_default_shopify):
    template = Liquid("A{% liquid\n  # Nothing to render.\n\n%}B")

    assert template.render() == "AB"


def test_liquid_tag_preserves_render_dependency_lines(set_default_shopify):
    template = Liquid(
        "{% liquid\n  render 'card', title: title\n%}",
        loader=DictLoader({"card": "{{ title }}"}),
    )
    extension = template.env.extensions[LiquidShopifyExtension.identifier]

    assert template.render(title="Visible") == "Visible"
    assert [
        (call.template_name, call.line) for call in extension.render_calls(None)
    ] == [("card", 2)]


@pytest.mark.parametrize(
    ("source", "line", "message"),
    [
        (
            "{% liquid\n  assign value = 1\n  missing value\n%}",
            3,
            "unknown tag 'missing'",
        ),
        (
            "{% liquid\n  liquid\n%}",
            2,
            "Nested liquid tags are not supported",
        ),
        (
            "{% liquid assign value = 1 %}",
            1,
            "requires each statement on its own line",
        ),
    ],
)
def test_liquid_tag_rejects_invalid_syntax(
    set_default_shopify,
    source,
    line,
    message,
):
    with pytest.raises(TemplateSyntaxError, match=message) as exc_info:
        Liquid(source)

    assert exc_info.value.lineno == line


def test_liquid_syntax_inside_raw_is_not_preprocessed(set_default_shopify):
    source = "{% raw %}{% liquid\n  echo value\n%}{% endraw %}"

    assert Liquid(source).render(value="hidden") == "{% liquid\n  echo value\n%}"


def test_liquid_syntax_inside_literals_and_comments_is_not_preprocessed(
    set_default_shopify,
):
    source = """{{ '{% liquid' }}{% comment %}{% liquid
  echo hidden
%}{% endcomment %}"""

    assert Liquid(source).render(hidden="hidden") == "{% liquid"


def test_render_uses_named_arguments_with_isolated_scope(set_default_shopify):
    template = Liquid(
        "main",
        from_file=True,
        loader=DictLoader(
            {
                "main": (
                    "{% assign hidden = 'outer' %}{% render 'card', title: title %}"
                ),
                "card": "{{ title }}:{{ hidden | default: 'missing' }}",
            }
        ),
    )

    assert template.render(title="Visible") == "Visible:missing"


def test_render_can_propagate_configured_context_names(set_default_shopify):
    template = Liquid(
        "main",
        from_file=True,
        loader=DictLoader(
            {
                "main": "{% render 'card' %}",
                "card": "{{ settings.title }}",
            }
        ),
    )
    template.env.liquid_render_context_names = ("settings",)

    assert template.render(settings={"title": "Shared"}) == "Shared"


def test_render_calls_are_available_for_dependency_validation(
    set_default_shopify,
):
    template = Liquid(
        "{% render 'card' %}{% render dynamic %}",
        from_file=False,
    )
    extension = template.env.extensions[LiquidShopifyExtension.identifier]

    assert [
        (call.template_name, call.line) for call in extension.render_calls(None)
    ] == [("card", 1), (None, 1)]


def test_style_renders_inline_css(set_default_shopify):
    template = Liquid(
        "{% style %}.theme { color: {{ color }}; }{% endstyle %}",
        from_file=False,
    )

    assert template.render(color="red") == ("<style>.theme { color: red; }</style>")


def test_render_uses_canonical_name_for_metadata_and_runtime(
    set_default_shopify,
):
    env = LiquidEnvironment(
        loader=DictLoader(
            {
                "main": "{% render 'card' %}",
                "snippets/card.liquid": "Canonical",
            }
        )
    )
    configure_environment(env, mode="shopify")
    env.liquid_render_template_resolver = lambda name: f"snippets/{name}.liquid"

    template = env.get_template("main")
    extension = env.extensions[LiquidShopifyExtension.identifier]

    assert template.render() == "Canonical"
    assert extension.render_calls("main")[0].template_name == ("snippets/card.liquid")


def test_render_arguments_cannot_override_propagated_context(
    set_default_shopify,
):
    env = LiquidEnvironment(
        loader=DictLoader(
            {
                "main": "{% render 'card', protected: 'argument' %}",
                "card": "{{ protected }}",
            }
        )
    )
    configure_environment(env, mode="shopify")
    env.liquid_render_context_names = ("protected",)

    assert env.get_template("main").render(protected="host") == "host"


def test_render_and_style_support_async_environments(set_default_shopify):
    template = Liquid(
        "main",
        from_file=True,
        enable_async=True,
        loader=DictLoader(
            {
                "main": (
                    "{% render 'card', title: title %}"
                    "{% style %}.x { color: {{ color }}; }{% endstyle %}"
                ),
                "card": "<h2>{{ title }}</h2>",
            }
        ),
    )

    assert (
        asyncio.run(template.render_async(title="Async", color="red"))
        == "<h2>Async</h2><style>.x { color: red; }</style>"
    )
