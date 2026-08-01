# ADR-002: Design System

**Date:** 2026-08-01
**Status:** Accepted

## Context

The lab website must present academic research professionally while showcasing interactive WASP software.

Reference pattern: `tanxuezhi.github.io` — single-page scroll, numbered sections, card-based content, white background.

## Decision

### Color Palette

| Token | Hex | Usage |
|-------|-----|-------|
| Hohai Blue (Primary) | `#0B5EA7` | Headers, nav, links, buttons, accents |
| Hohai Blue Dark | `#083D6E` | Hover states, footer |
| Hohai Blue Light | `#E8F0F8` | Card backgrounds, section tints |
| Academic Gold | `#C4922E` | Highlights, badges, key metrics |
| Text Primary | `#1a1a2e` | Body text |
| Text Secondary | `#5a5a7a` | Subtitles, metadata |
| Background | `#FFFFFF` | Main background |
| Surface | `#F8F9FB` | Alternating section backgrounds |

### Typography

```css
--font-heading: 'Crimson Pro', Georgia, serif;
--font-body: 'Inter', -apple-system, sans-serif;
--font-mono: 'JetBrains Mono', monospace;
```

### Layout

- Single-page scroll with fixed top navigation
- Numbered sections: `01 / Research`, `02 / Publications`, `03 / Showcase`, `04 / Team`
- Max content width: 1100px
- Two-column splits where appropriate
- Card grid for research directions and publications

### Motion

- Subtle fade-in on scroll (Intersection Observer)
- Research direction cards: subtle hover lift
- WASP demo area: data-flow particle animation on section enter
- Smooth scroll for anchor navigation

## Rationale

- Hohai Blue ties lab identity to the university brand
- Gold accent provides warmth against cool blue — academic but not cold
- Crimson Pro headings add scholarly character
- Numbered sections follow established academic site convention

## Consequences

- All custom CSS — no framework dependency for GitHub Pages
- Intersection Observer for scroll animations (no library needed)
- Google Fonts for typography (free CDN)
