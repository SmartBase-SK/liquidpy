from jinja2.environment import Environment
from jinja2.loaders import DictLoader, FileSystemLoader
from jinja2.parser import Parser
from jinja2.runtime import LoopContext
from liquid import LiquidEnvironment, configure_environment
from liquid.liquid import Liquid
import pytest  # noqa: F401


def test_env_args(set_default_standard):
    loader = FileSystemLoader("~")
    tpl = Liquid(
        "{$ a $}",
        variable_start_string="{$",
        variable_end_string="$}",
        x=1,
        loader=loader,
    )
    assert tpl.render(a=1) == "1"
    assert tpl.env.x == 1


def test_from_env(set_default_standard):
    loader = FileSystemLoader("~")
    env = Environment(
        loader=loader,
        variable_start_string="{$",
        variable_end_string="$}",
    )
    tpl = Liquid.from_env("{$ a $}", env)
    assert tpl.render(a=1) == "1"

    tpl = Liquid(
        "{@ a @}",
        env=env,
        # override the env's settings
        variable_start_string="{@",
        variable_end_string="@}",
    )
    assert tpl.render(a=1) == "1"


def test_async_render(set_default_standard):
    import asyncio

    tpl = Liquid("{{ a }}", enable_async=True)

    try:
        run = asyncio.run
    except AttributeError:
        loop = asyncio.get_event_loop()
        run = loop.run_until_complete

    assert run(tpl.render_async(a=1)) == "1"


def test_default_environment_resolves_mapping_keys_before_attributes(
    set_default_standard,
):
    tpl = Liquid("{{ value.items }}", from_file=False)

    assert tpl.render(value={"items": "mapping value"}) == "mapping value"


def test_configure_existing_environment(set_default_shopify):
    env = LiquidEnvironment(
        loader=DictLoader(
            {
                "main": "{% render 'card', title: title %}",
                "card": "{{ title }}",
            }
        )
    )

    assert configure_environment(env, mode="shopify") is env
    assert env.get_template("main").render(title="Configured") == "Configured"


def test_configure_plain_environment_installs_liquid_lookup(
    set_default_standard,
):
    env = Environment()
    configure_environment(env, mode="standard")

    assert (
        env.from_string("{{ value.items }}").render(value={"items": "mapping value"})
        == "mapping value"
    )


def test_import_does_not_patch_jinja_process_globals():
    assert Parser.parse_for.__module__ == "jinja2.parser"
    assert not hasattr(LoopContext, "liquid_cycle")
