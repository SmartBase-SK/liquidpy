import asyncio

from jinja2 import DictLoader

from liquid import Liquid, LiquidEnvironment, configure_environment
from liquid.exts.shopify import LiquidShopifyExtension


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
