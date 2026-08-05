
You may checkout the documentation for shopfiy liquid:

- https://shopify.dev/api/liquid

The compatibility issues list on:

- https://pwwang.github.com/liquidpy/standard

also applied in shopify mode.

Shopify mode supports line-oriented `{% liquid %}` blocks and the `{% echo %}`
tag. Each statement in a `liquid` block must be on its own line. Blank lines and
`#` line comments are accepted, and the opening and closing hyphens retain their
normal whitespace-control behavior.
