import pytest
from jinja2 import BytecodeCache, DictLoader, Environment, nodes
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
        pytest.param(
            "{% for item in items %}{% decrement counter %}{% endfor %}"
            "|{% decrement counter %}",
            "-1-2-3|-4",
            id="decrement-in-loop",
        ),
        pytest.param(
            "{% for item in items %}{% capture label %}"
            "{% assign last = item %}{{ item }}{% endcapture %}{% endfor %}"
            "[{{ last }}][{{ label }}]",
            "[b][b]",
            id="assign-in-capture-in-loop",
        ),
        pytest.param(
            "{% for item in no_items %}{% assign value = item %}"
            "{% else %}{% assign value = 'empty' %}{% endfor %}[{{ value }}]",
            "[empty]",
            id="for-else",
        ),
        pytest.param(
            "{% for item in items %}{% assign x = item %}{% endfor %}"
            "{% for row in items %}{% for x in items %}{% endfor %}"
            "{% assign seen = x %}{% endfor %}[{{ x }}][{{ seen }}]",
            "[b][b]",
            id="inner-loop-target-named-like-an-assigned-name",
        ),
        pytest.param(
            "{% macro show(x, change) %}{% for item in items %}"
            "{% if change %}{% assign x = item %}{% endif %}{% endfor %}{{ x }}"
            "{% endmacro %}{{ show('first', 1) }}|{{ show('second', 0) }}",
            "b|second",
            id="macro-parameter-not-taken-from-an-earlier-call",
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


def test_scopes_built_by_extensions_stay_local_beside_loop_assignments():
    env = LiquidEnvironment(
        loader=DictLoader(
            {
                "main": "{% assign value = 'outer' %}{% for item in items %}"
                "{% if item == 'never' %}{% assign value = item %}{% endif %}"
                "{% endfor %}{% local %}{% endlocal %}{% for item in items %}"
                "{% if item == 'never' %}{% assign value = item %}{% endif %}"
                "{% endfor %}[{{ value }}]"
            }
        ),
        extensions=[_LocalBlockExtension],
    )
    configure_environment(env, mode="shopify")
    assert env.get_template("main").render(items=ITEMS) == "[outer]"


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


class _MemoryBytecodeCache(BytecodeCache):
    def __init__(self):
        self.data = {}

    def load_bytecode(self, bucket):
        if bucket.key in self.data:
            bucket.bytecode_from_string(self.data[bucket.key])

    def dump_bytecode(self, bucket):
        self.data[bucket.key] = bucket.bytecode_to_string()


def test_templates_loaded_from_the_bytecode_cache_render_in_a_new_environment():
    cache = _MemoryBytecodeCache()
    templates = {
        "main": "{% assign total = 0 %}{% for item in items %}"
        "{% assign total = total | plus: 1 %}{% endfor %}{{ total }}"
    }

    def render():
        env = LiquidEnvironment(loader=DictLoader(templates), bytecode_cache=cache)
        configure_environment(env, mode="shopify")
        return env.get_template("main").render(items=ITEMS)

    assert render() == "3"
    assert cache.data
    assert render() == "3"
