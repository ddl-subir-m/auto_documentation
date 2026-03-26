# Auto Model Docs Studio — Design System

Extracted from the codebase's CSS variables and component patterns. Source of truth for design decisions.

## Color Tokens

### Backgrounds

| Token | Value | Usage |
|-------|-------|-------|
| `--bg-page` | `#FAFAFA` | Page background |
| `--panel` | `#FFFFFF` | Card/panel backgrounds |
| `--panel-border` | `#E0E0E0` | Card borders, input borders, section dividers |
| `--terminal` | `#1E1E1E` | Terminal output background |
| `--header-bg` | `#2E2E38` | Domino-style top header |

### Accent (Domino Purple)

| Token | Value | Usage |
|-------|-------|-------|
| `--accent` | `#543FDE` | Primary buttons, links, active states, focus rings |
| `--accent-hover` | `#3B23D1` | Button/link hover |
| `--accent-active` | `#311EAE` | Button active/pressed |
| `--accent-glow` | `rgba(84, 63, 222, 0.08)` | Focus ring glow (`box-shadow: 0 0 0 3px`) |

### Text

| Token | Value | Usage |
|-------|-------|-------|
| `--text-primary` | `#2E2E38` | Headings, labels, primary content |
| `--text-secondary` | `#65657B` | Body copy, card titles, field labels |
| `--text-muted` | `#8F8FA3` | Helper text, hints, info tooltips, timestamps |

### Status

| Token | Value | Usage |
|-------|-------|-------|
| `--success` | `#28A464` | Success states |
| `--warning` | `#CCB718` | Warning states, alerts |
| `--error` | `#C20A29` | Error text, required star, error states |
| `--info` | `#0070CC` | Informational states |

### Banners

| Token | Value | Usage |
|-------|-------|-------|
| `--error-bg` | `#FFF0F0` | Error banner background |
| `--error-border` | `#F5C6CB` | Error banner border |

## Typography

- **Font stack:** `Inter, Lato, 'Helvetica Neue', Helvetica, Arial, sans-serif`
- **Base size:** `0.875rem` (14px) for body/inputs
- **Headings:** `--text-primary`, `margin: 0`

| Element | Size | Weight | Color |
|---------|------|--------|-------|
| Header title | `1rem` | 600 | `#FFFFFF` |
| Hero tagline | `1.125rem` | 400 | `--text-secondary` |
| Card title | `0.875rem` | 600 | `--text-secondary` |
| Card title (advanced) | `0.8125rem` | — | `--text-muted` |
| Card title sub | `0.75rem` | 400 | `--text-muted` |
| Field label | `0.8rem` | 500 | `--text-secondary` |
| Field label (advanced) | `0.75rem` | — | `--text-muted` |
| Input text | `0.875rem` | — | `--text-primary` |
| Placeholder text | `0.875rem` | — | `--text-muted` |
| Section title | `0.875rem` | 600 | `--text-muted` |
| Upload button | `0.8rem` | 500 | `--text-secondary` |
| Info tooltip | `0.75rem` | 400 | `--text-muted` (trigger) / `--panel` on `--text-primary` bg (popup) |
| Terminal header | `0.9rem` | 600 | — |
| Terminal actions | `0.75rem` | — | `--text-primary` |

## Layout

### Page Structure

```
┌──────────────────────────────────────────────────┐
│ .domino-header (full-width, 48px, --header-bg)   │
├──────────────────────────────────────────────────┤
│ .page (max-width: 1500px, padding: 1rem 2rem)   │
│   warnings banner (if any)                       │
│   .studio-grid (3-column layout)                 │
│     LEFT: What to document (spec file, filters)  │
│     MID: Configuration & Run (code root,         │
│          language, branch, tiers, more settings,  │
│          gear icon → advanced settings modal,     │
│          Generate button)                         │
│     RIGHT: History (job history table)            │
└──────────────────────────────────────────────────┘
```

## Components

### Card (`.card`)

```css
background: var(--panel);
border: 1px solid var(--panel-border);
border-radius: 8px;
padding: 1.25rem;
box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
transition: border-color 0.2s ease, transform 0.2s ease;
```

### Form Field (`.field`)

```css
display: flex;
flex-direction: column;
gap: 0.35rem;
margin-bottom: 1rem;
```

- Labels above inputs
- Focus state: `border-color: var(--accent)`, `box-shadow: 0 0 0 3px var(--accent-glow)`
- Last field in a card has `margin-bottom: 0`

### Label Row (`.label-row`)

Label + info tooltip inline: `display: flex; align-items: center; gap: 0.35rem;`

### Info Tooltip (`.info-tooltip`)

- Trigger: `ⓘ` character, `cursor: help`, `--text-muted`
- Popup: CSS `::after` with `attr(data-tooltip)`, dark bg (`--text-primary`), white text
- Appears on hover/focus, `min-width: 200px`, `max-width: 320px`, `border-radius: 4px`

### Primary Button (`button.primary`)

```css
background: var(--accent);
border: none;
border-radius: 4px;
padding: 0.5rem 1.25rem;
color: white;
font-size: 0.875rem;
font-weight: 600;
```

- Hover: `--accent-hover`
- Active: `--accent-active`
- Disabled: `opacity: 0.5`, `cursor: not-allowed`

### Cross-Project Banner (`.cross-project-banner`)

```css
margin-top: 0.5rem;
padding: 0.5rem 0.75rem;
background: #EDECFB;
border: 1px solid #C9C5F2;
border-radius: 6px;
color: #1820A0;
font-size: 0.875rem;
```

States:
- **Loading:** Shimmer animation, "Resolving project..." in `--text-muted`
- **Success:** Project name bold `#1820A0`, raw ID in `11px --text-muted` below
- **Error:** `--error` text on `--error-bg` background, `--error-border` border, `role="alert"`
- **Current project mode:** `--text-muted` on `--bg-page`, no colored background

### Checkbox Field (`.checkbox-field`)

```css
display: flex;
align-items: center;
gap: 0.625rem;
cursor: pointer;
```

- Checkbox: `accent-color: var(--accent)`, 1rem square
- Label: `--text-primary`, `0.875rem`, weight 500

### Upload Button (`.upload-btn`)

```css
background: var(--bg-page);
border: 1px solid var(--panel-border);
border-radius: 4px;
padding: 0 0.875rem;
```

- Hover: `background: var(--panel)`, `border-color: var(--accent)`

## Patterns

### Conditional Visibility

- `.domino-fields` class on fields that only appear in Domino execution mode
- Server-side rendering controls which fields appear based on `inferred_mode`

### HTMX Integration

- `hx_get` / `hx_post` for route calls
- `hx_target` / `hx_swap="outerHTML"` for fragment replacement
- `hx_trigger="every Ns"` for polling

### Disabled State Explanation

Per Domino UX principles: always explain WHY an element is disabled.
- Use `aria-describedby` pointing to a visually hidden `<span>` with the explanation
- Do NOT rely on `title` attribute (unreliable for screen readers)

## Accessibility

- Error banners: `role="alert"` for screen reader announcement
- Loading states: `aria-busy="true"` on container
- Live regions: `aria-live="polite"` for dynamic content updates (e.g., project resolution)
- Color-only indicators: always pair with an icon (e.g., `⚠` before error text)
- Touch targets: min 44x44px for interactive elements
