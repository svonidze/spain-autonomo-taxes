# Interface languages

The source of user-facing copy is `frontend/src/locales/<code>/<domain>.json`.
Keep semantic IDs stable when editing wording. Technical identifiers, currency
codes and saved user/source data are not translated. The HTML shell is filled
from the same catalogs at build time; it contains no separate Russian copy.

## Add a language

1. Copy the domain files into the new language directory and translate their
   values without renaming keys or arguments.
2. Add its code, native display name, Intl locale and direction to `registry.json`.
   Quarter numerals can optionally be specified there; the default is numeric.
3. Run `npm run locales:check`, `npm run typecheck`, `npm run test:unit`, and the
   browser tests. No screen-specific language condition or new switcher code is
   required. The existing default/fallback remains Russian.

Use full sentences and ICU named arguments. For example, Russian counts need
`one`, `few`, `many` and `other`; English uses `one` and `other`. The compiler checks
the actual categories reported by Intl for each configured locale. Preserve ICU
argument types as well as names. Write literal apostrophes as `''` and keep markup
out of messages. Links and controls belong to the component structure.

Catalog checking rejects duplicate, empty, missing or extra keys, malformed ICU,
missing plural categories and mismatched arguments. It generates ICU AST and
TypeScript message signatures under ignored `frontend/src/generated/`. Runtime
formatting uses FormatJS, not a separate hand-written interpolation engine.

`t()` provides checked IDs/parameters for typed components. `message()` creates a
descriptor for an error, notice or toast whose wording must change with locale.
Keep raw server diagnostics distinct from application-authored messages. The thin
Vue locale adapter uses this shared engine; no runtime global dictionaries remain.
The help reference map contains stable message IDs, never translated prose.

## Verify layout and state

`npm run test:pseudo` generates a complete test-only third locale with elongated,
marked text, runs its browser scenario, then restores an ordinary RU/EN build.
The normal web host and Python package builder reject test-only artifacts even
if a test was interrupted. The browser fixture explicitly opts in and uses only
ephemeral synthetic data. `npm run build` always produces the normal profile.

Changing language does not navigate a screen or invalidate a pending write.
Review/workflow components preserve their draft fields; modal labels update
without replacing selected file inputs. Settings retain the latest master's
explicit dirty/busy leave policy. Existing names, amounts, reasons and document
data stay intact; number/date presentation follows the selected Intl locale.
