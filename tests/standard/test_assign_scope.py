import pytest
from jinja2 import DictLoader, Environment, nodes
from jinja2.exceptions import TemplateSyntaxError
from jinja2.ext import Extension
from jinja2.sandbox import SandboxedEnvironment

from liquid import Liquid, LiquidEnvironment, configure_environment

ITEMS = ["", "a", "b"]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "{% assign found = '' %}{% for item in items %}"
            "{% if item != '' %}{% assign found = item %}{% break %}{% endif %}"
            "{% endfor %}[{{ found }}]",
            "[a]",
            id="first-match-with-break",
        ),
        pytest.param(
            "{% for item in items %}{% assign last = item %}{% endfor %}[{{ last }}]",
            "[b]",
            id="first-assigned-in-loop",
        ),
        pytest.param(
            "{% assign count = 0 %}{% for item in items %}"
            "{% assign count = count | plus: 1 %}{{ count }}{% endfor %}|{{ count }}",
            "123|3",
            id="counter",
        ),
        pytest.param(
            "{% assign value = 1 %}{% for item in items %}"
            "{% if forloop.first %}{% assign value = 5 %}{% endif %}{{ value }}"
            "{% endfor %}",
            "555",
            id="conditional-assign-read-later",
        ),
        pytest.param(
            "{% for item in items %}{% assign label = item | append: '!' %}"
            "{{ label }}{% endfor %}[{{ label }}]",
            "!a!b![b!]",
            id="temporary-assigned-first",
        ),
        pytest.param(
            "{% assign count = 0 %}{% for row in items %}{% for col in items %}"
            "{% assign count = count | plus: 1 %}{% endfor %}{% endfor %}[{{ count }}]",
            "[9]",
            id="nested-loops",
        ),
        pytest.param(
            "{% for item in items %}{% assign value = 'loop' %}{% endfor %}"
            "{% assign value = 'outside' %}"
            "{% for item in no_items %}{% assign value = 'skipped' %}{% endfor %}"
            "[{{ value }}]",
            "[outside]",
            id="reassigned-between-loops",
        ),
        pytest.param(
            "{% assign value = 'kept' %}{% for item in items %}"
            "{% if item == 'never' %}{% assign value = item %}{% endif %}"
            "{% endfor %}[{{ value }}]",
            "[kept]",
            id="assign-not-reached",
        ),
        pytest.param(
            "{% for item in no_items %}{% assign given = item %}{% endfor %}"
            "[{{ given }}]",
            "[from context]",
            id="loop-not-run-keeps-context-value",
        ),
        pytest.param(
            "{% for item in items %}{% capture label %}<{{ item }}>{% endcapture %}"
            "{% endfor %}[{{ label }}]",
            "[<b>]",
            id="capture-in-loop",
        ),
        pytest.param(
            "{% assign item = 'outer' %}{% for item in items %}{% endfor %}"
            "[{{ item }}]",
            "[outer]",
            id="loop-variable-stays-local",
        ),
        pytest.param(
            "{% for item in items %}{% increment counter %}{% endfor %}"
            "|{% increment counter %}",
            "012|3",
            id="increment-in-loop",
        ),
    ],
)
def test_assign_inside_a_block_is_visible_after_it(
    set_default_standard, source, expected
):
    rendered = Liquid(source).render(items=ITEMS, no_items=[], given="from context")
    assert rendered == expected


@pytest.mark.parametrize(
    "environment",
    [Environment, SandboxedEnvironment, LiquidEnvironment],
    ids=lambda environment: environment.__name__,
)
def test_configured_environments_keep_loop_assignments(environment):
    env = environment(
        loader=DictLoader(
            {
                "main": "{% assign total = 0 %}{% for item in items %}"
                "{% assign total = total | plus: item %}{% endfor %}{{ total }}"
            }
        )
    )
    configure_environment(env, mode="shopify")
    assert env.get_template("main").render(items=[1, 2, 3]) == "6"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "{%- liquid\n  assign count = 0\n  for item in items\n"
            "    assign count = count | plus: 1\n  endfor\n-%}[{{ count }}]",
            "[3]",
            id="liquid-tag",
        ),
        pytest.param(
            "{% tablerow item in items %}{% assign last = item %}{% endtablerow %}"
            "[{{ last }}]",
            "[b]",
            id="tablerow",
        ),
    ],
)
def test_shopify_blocks_keep_loop_assignments(set_default_shopify, source, expected):
    assert Liquid(source).render(items=ITEMS).endswith(expected)


def test_rendered_snippets_keep_their_own_assigned_values():
    env = LiquidEnvironment(
        loader=DictLoader(
            {
                "outer": "{% assign value = 'outer' %}{% for item in items %}"
                "{% render 'inner', items: items %}{% endfor %}[{{ value }}]",
                "inner": "{% for item in items %}{% assign value = item %}{% endfor %}",
            }
        )
    )
    configure_environment(env, mode="shopify")
    assert env.get_template("outer").render(items=ITEMS) == "[outer]"


class _LocalBlockExtension(Extension):
    tags = {"local"}

    def parse(self, parser):
        lineno = next(parser.stream).lineno
        body = parser.parse_statements(("name:endlocal",), drop_needle=True)
        return nodes.Scope(
            [
                nodes.Assign(nodes.Name("value", "store"), nodes.Const("tag")),
                *body,
            ],
            lineno=lineno,
        )


def test_scopes_built_by_extensions_stay_local():
    env = LiquidEnvironment(
        loader=DictLoader(
            {
                "main": "{% assign value = 'outer' %}{% assign count = 0 %}"
                "{% local %}{{ value }}{% for item in items %}"
                "{% assign count = count | plus: 1 %}{% endfor %}{{ count }}"
                "{% endlocal %}[{{ value }}]"
            }
        ),
        extensions=[_LocalBlockExtension],
    )
    configure_environment(env, mode="shopify")
    assert env.get_template("main").render(items=ITEMS) == "tag3[outer]"


def test_the_assign_scope_filters_are_reserved():
    env = LiquidEnvironment(
        loader=DictLoader(
            {
                "loop": "{% for item in items %}{% assign last = item %}{% endfor %}",
                "misuse": "{{ items | _liquid_store_assigned }}",
            }
        )
    )
    configure_environment(env, mode="shopify")
    env.get_template("loop")
    with pytest.raises(TemplateSyntaxError, match="reserved"):
        env.get_template("misuse")
