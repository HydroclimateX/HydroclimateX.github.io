# Historical Teal Accent Design

## Goal

Restore the immediate previous “resilient” accent colour for the homepage subtitle and the `Flood&Drought.jpeg` border.

## Historical Source

Immediately before commit `57268a1`, the homepage headline read “Hydroclimate science for a resilient future.” The word “resilient” was styled by `.hero h1 em` with `color: var(--teal)`. At that revision, and in the current design system, `--teal` is `#078f91`.

## Approved Design

- Change the subtitle “From flood to drought — across scales” from `var(--muted)` to `var(--teal)`.
- Change the image border from `var(--orange)` to `var(--teal)`.
- Reuse the existing custom property instead of hardcoding `#078f91` or adding another colour token.
- Preserve all typography, spacing, sizing, image-shadow, markup, and responsive rules.

## Scope

Only two colour declarations in `style.css` will change. No HTML, JavaScript, backend, or asset changes are required.

## Verification

- Confirm both selectors resolve to `rgb(7, 143, 145)` in a browser.
- Confirm the image shadow is unchanged.
- Confirm the page has no new horizontal overflow at desktop and mobile widths.
- Confirm the final diff contains only the two approved colour changes.
