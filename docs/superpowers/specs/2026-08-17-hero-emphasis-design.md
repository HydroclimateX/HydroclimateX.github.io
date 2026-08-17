# Hero Emphasis Design

## Goal

Improve the homepage hero so the title reads as a compact two-line hierarchy and the flood-and-drought image becomes the main visual focal point.

## Current Behaviour

At a 1440px desktop viewport, the deployed site renders the main heading at 86.4px and the hero image at approximately 378px wide. Although the image has a 540px maximum width, the existing `1.35fr / 0.65fr` grid prevents it from reaching that size.

## Approved Design

- Keep “Hydroclimate eXtreme” as the main heading and “From flood to drought — across scales” as a separate, muted subtitle within the heading.
- Reduce the main heading to approximately 55px at a 1440px viewport, with fluid limits that remain readable on larger and smaller screens.
- Reduce the subtitle to approximately 22px and retain the muted blue-grey colour.
- Change the desktop hero to equal-width columns. This gives the image approximately 566px of rendered width at 1440px, or about 1.5 times its current deployed size.
- Use the existing orange design token (`#ed7a32`) for the image border. Orange complements the site’s blue palette and connects visually with the drought-and-sun side of the photograph.
- Retain a restrained navy-toned shadow, strengthened enough to separate the image from the pale hero background without creating a glossy card effect.

## Responsive Behaviour

The existing single-column layout remains in place below 850px. The image should use the available mobile width while retaining a sensible maximum size, and the heading should continue to scale down without horizontal overflow.

## Scope

Only the homepage hero styles in `style.css` need to change. No content, JavaScript, backend, or image-file changes are required.

## Verification

- Confirm the desktop title, subtitle, and image computed sizes at a 1440px viewport.
- Confirm the title stays readable and the image does not overflow at tablet and mobile widths.
- Confirm the orange border and navy shadow render around `figs/Flood&Drought.jpeg`.
- Confirm the rest of the homepage layout is unchanged.
