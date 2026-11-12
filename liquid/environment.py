"""Jinja environments with Liquid-compatible attribute lookup."""

from __future__ import annotations

from typing import Any, Callable, Optional

from jinja2 import Environment, nodes
from jinja2.runtime import LoopContext

from .assign_scope import install_assign_scope_filters, share_block_assignments
from .patching import LiquidParser


_LOOP_ATTRIBUTES = frozenset(
    {
        "cycle",
        "depth",
        "depth0",
        "first",
        "index",
        "index0",
        "last",
        "length",
        "revindex",
        "revindex0",
        "rindex",
        "rindex0",
    }
)


def _resolve_attribute(
    environment: Environment,
    obj: Any,
    attribute: str,
    fallback: Callable[[Any, str], Any],
) -> Any:
    if isinstance(obj, LoopContext):
        if attribute == "rindex":
            attribute = "revindex"
        elif attribute == "rindex0":
            attribute = "revindex0"
        if attribute in _LOOP_ATTRIBUTES:
            # Jinja's own fixed loop attributes; the sandbox checks cost ~0.5 µs each.
            return getattr(obj, attribute)
    if attribute == "__len__":
        try:
            return obj.__len__
        except AttributeError:
            return environment.undefined(obj=obj, name=attribute)
    try:
        return obj[attribute]
    except (KeyError, IndexError):
        return environment.undefined(obj=obj, name=attribute)
    except TypeError:
        if getattr(environment, "liquid_allow_object_attributes", True):
            return fallback(obj, attribute)
        return environment.undefined(obj=obj, name=attribute)


class LiquidEnvironmentMixin:
    """Provide Liquid parsing and mapping-first attribute lookup."""

    liquid_allow_object_attributes = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        install_assign_scope_filters(self)

    def _parse(self, source: str, name: str, filename: str) -> Any:
        return LiquidParser(self, source, name, filename).parse()

    def _generate(
        self,
        source: nodes.Template,
        name: Optional[str],
        filename: Optional[str],
        defer_init: bool = False,
    ) -> str:
        source = share_block_assignments(self, source)
        return super()._generate(  # type: ignore[misc]
            source, name, filename, defer_init=defer_init
        )

    def getattr(self, obj: Any, attribute: str) -> Any:
        return _resolve_attribute(self, obj, attribute, super().getattr)


def configure_environment_class(environment: Environment) -> None:
    """Install Liquid parser and lookup behavior on a plain Jinja environment."""
    install_assign_scope_filters(environment)
    if isinstance(environment, LiquidEnvironmentMixin):
        return

    fallback_getattr = environment.getattr

    def liquid_getattr(obj: Any, attribute: str) -> Any:
        return _resolve_attribute(
            environment,
            obj,
            attribute,
            fallback_getattr,
        )

    def liquid_parse(source: str, name: str, filename: str) -> Any:
        return LiquidParser(environment, source, name, filename).parse()

    fallback_generate = environment._generate

    def liquid_generate(
        source: nodes.Template,
        name: Optional[str],
        filename: Optional[str],
        defer_init: bool = False,
    ) -> str:
        source = share_block_assignments(environment, source)
        return fallback_generate(source, name, filename, defer_init=defer_init)

    environment.getattr = liquid_getattr
    environment._parse = liquid_parse
    environment._generate = liquid_generate


class LiquidEnvironment(LiquidEnvironmentMixin, Environment):
    """Default Jinja environment used by LiquidPy."""
