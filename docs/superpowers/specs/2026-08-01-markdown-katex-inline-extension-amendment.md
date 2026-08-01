# Markdown / KaTeX inline-extension architecture amendment

## Decision

The renderer must discover mathematics inside Marked's inline lexer, not by replacing every formula in the raw source before Markdown tokenization. A per-render Marked extension recognizes `$$…$$`, `$…$`, `\\(…\\)`, and `\\[…\\]` and emits a private `math` token. This keeps code spans, link destinations/titles, image destinations, and raw HTML attributes as separate Marked tokens, so a dollar sign in one context cannot pair with a delimiter in another.

## Rendering contract

- The extension is passed to `marked.parse` (or the equivalent lexer/parser call) for that render only; it must not call `marked.use()` or mutate global options.
- `startInline` advertises the next possible formula opener. The tokenizer must reject escaped dollars, newline-spanning single-dollar formulas, empty formulas, and currency-like openers. A single-dollar opener may not begin when the next character is whitespace or a digit; a matching closer may not be followed by a digit, and `$5-$10` remains literal.
- KaTeX is read once after tokenization per `render()` call and receives exactly `{ displayMode, throwOnError: false, trust: false, maxExpand: 1000 }` for each math token. Missing or throwing KaTeX falls back to escaped source text.
- `math` tokens nested in raw HTML tokens or image alt tokens are converted back to their literal delimiters and source. Math in a link label remains renderable. This walk must use a fresh context for each image alt so alt HTML cannot open or close an outer raw-HTML state.
- Any user-provided HTML, image source, or disallowed URL is emitted only as escaped/plain text. Non-string values, including values whose string conversion throws, never escape the public `render()` API.

## Acceptance examples

The renderer must call KaTeX only for `ordinary` in each of these inputs:

```text
<span data-x="$attribute">$ordinary$
`$code`$ordinary$
[link](https://example.com/$path)$ordinary$
```

It must call KaTeX for `x` only in `$5 and $x$` and `$5 USD and $x$`, and call it zero times for `$5-$10`. Image alt formulas remain literal while a following ordinary formula still renders.
