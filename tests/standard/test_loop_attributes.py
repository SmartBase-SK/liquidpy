from jinja2 import DictLoader
from jinja2.sandbox import SandboxedEnvironment

from liquid import configure_environment


def _render(source, **context):
    env = SandboxedEnvironment(loader=DictLoader({"main": source}))
    configure_environment(env, mode="shopify")
    return env.get_template("main").render(**context)


def test_forloop_attributes_in_a_sandbox():
    source = (
        "{% for item in items %}"
        "{{ forloop.index }}{{ forloop.index0 }}{{ forloop.rindex }}"
        "{{ forloop.rindex0 }}{{ forloop.length }}"
        "{% if forloop.first %}F{% endif %}{% if forloop.last %}L{% endif %},"
        "{% endfor %}"
    )
    assert _render(source, items="ab") == "10212F,21102L,"


def test_private_loop_attributes_stay_blocked_in_a_sandbox():
    source = "{% for item in items %}[{{ forloop._iterator }}]{% endfor %}"
    assert _render(source, items="a") == "[]"
