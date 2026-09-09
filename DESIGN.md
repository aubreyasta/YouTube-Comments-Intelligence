# DESIGN.md

Canonical visual and interaction system for the `app/` frontend, scanned from the shipped implementation (`app/style.css`, `app/index.html`, `app/app.js`). Owns tokens, typography, color, layout, responsive behavior, shape, depth, component language, interaction states, motion, and presentation accessibility. It does not own product behavior or scope ([PRODUCT.md](PRODUCT.md)) or delivery sequencing ([PLAN.md](PLAN.md)).

No `.impeccable/design.json` sidecar exists in this repository and the `impeccable` tooling that normally generates it is not installed in this environment. This document is the sole design record until that tooling is available; regenerate the sidecar through it when it is.

---

## Visual thesis

The frontend ships under the internal UI codename **Resonance** (see `<title>` and the `app/style.css` header comment) even though the product is named YouTube Intelligence in [PRODUCT.md](PRODUCT.md) and [README.md](README.md). One locked pink-and-ink visual language, sourced from an approved mockup (`UI mockups for backend flows (1)/Resonance MVP Screens.dc.html`), covers every screen. No framework, no build step, no component library: `app/app.js` renders HTML strings and `app/style.css` is the entire styling surface.

## Design tokens

Defined once on `:root` in `app/style.css`. Treat this table as canonical; do not redeclare a color or radius value inline elsewhere.

| Token | Value | Role |
|---|---|---|
| `--ink` | `#1A1A2E` | Headings, primary text, active/selected state |
| `--body` | `#3D3C52` | Default body text |
| `--muted` | `#6E6E85` | Secondary text |
| `--quiet` | `#737286` | Tertiary text; ≥4.5:1 on white (comment in source) |
| `--disabled` | `#8A8896` | Disabled-state text/icon — chosen because the mockup's `#B4B3C0` fails contrast |
| `--disabled-soft` | `#B4B3C0` | Decorative-only disabled elements (icons, rules), never text |
| `--pink` | `#D6246E` | Primary brand accent, primary actions, links, data-forward numbers |
| `--pink-hover` | `#B01B5B` | Primary button/link hover |
| `--pink-deep` | `#A8194F` | Emphasis text on pink-tint backgrounds |
| `--pink-tint` | `#FDEEF5` | Selected/active background, badges, banners |
| `--pink-light` | `#FEF7FA` | Dropzone background |
| `--pink-border` | `#F3BFD6` | Active border on pink-tint surfaces |
| `--pink-border-soft` | `#F6D3E3` | Softer pink border, spinner track |
| `--border` | `#E6E6EE` | Default hairline border |
| `--divider` | `#F0EFF3` | Row/section dividers |
| `--neutral` | `#FAFAFB` | Neutral surface fill (table heads, inputs-on-dark contexts) |
| `--row-hover` | `#FDFAFC` | Table row hover |
| `--drawer` | `#FCFCFD` | Evidence drawer background |
| `--surface` | `#F5F4F7` | Secondary surface fill (icon chips, disabled buttons) |
| `--page` | `#EFEEF1` | Reserved page background |
| `--chev` | `#C6C5D0` | Chevrons, inactive dots, dim chart fill |
| `--thumb` | `#E9E8ED` | Thumbnail placeholder fill |
| `--dash` | `#D5D4DD` | Dashed borders (empty states, demo controls) |
| `--card-line` | `#EDECF1` | Evidence card border |
| `--danger` | `#B3452F` | Error text/border |
| `--danger-tint` | `#FBEFEA` | Error banner background |
| `--radius-btn` | `8px` | Buttons, inputs |
| `--radius-card` | `12px` | Cards, modals, empty blocks |
| `--radius-panel` | `10px` | Panels, tables, notices, dropzone |
| `--radius-pill` | `18px` | Pills (component uses 20px directly in places — treat 18–20px as the pill range) |
| `--font` | `"Plus Jakarta Sans", system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif` | The only typeface |

## Typography

One family (`--font`), sized per component rather than through a shared type-scale variable. Weight carries hierarchy at least as much as size: `700` is the standard heading/label weight, `800` marks the largest display moments.

| Use | Size | Weight |
|---|---|---|
| Report headline (`.report h1`) | 33px | 800 |
| Setup headline (`.setup h1`) | 31px | 800 |
| Big stat (`.big-stat`) | 46px | 800 |
| Home greeting (`.greeting`) | 24px | 800 |
| Run title (`.run-title`) | 20px | 800 |
| Chart/section heading (`.chart h2`, `.read h2`) | 18px | 800 |
| Empty-card title (`.empty-card h1`) | 22px | 800 |
| Panel/card title (`.panel-title`) | 14.5px | 700 |
| Body default (`body`) | 14px | 400, line-height 1.5 |
| Table row primary (`.trow-name`) | 14.5px | 700 |
| Small labels (`.field label`, `.section-label`) | 12.5–13px | 700 |
| Kicker / eyebrow (`.kicker`, `.rail-kicker`, `.ev-kicker`) | 10.5–11px | 800, letter-spacing .1–.12em, uppercase where applicable |
| Badge | 10.5px | 700, letter-spacing .04em, uppercase |

Letter-spacing tightens as size increases (`-.02em` to `-.03em` on the largest display text) and opens up on small uppercase kickers/badges (`.04em`–`.12em`) — the standard large-tight / small-wide pairing.

## Color usage rules

- **Pink is the only accent.** It marks primary actions, active navigation/filter state, links, data bars/values that are clickable evidence, and drafted/pending status. It is never used for large background fills outside `--pink-tint` surfaces.
- **Ink vs. body vs. muted vs. quiet** is a strict four-step text hierarchy: ink for headings and primary values, body for default prose, muted for secondary/supporting text, quiet for tertiary metadata (kickers, sub-values, timestamps).
- **Danger is reserved for errors and destructive actions** (`.btn.danger`, `.banner.error`, `.field-error`, failed status dot). It never doubles as a warning color — warnings use `--pink-tint`/`--pink-border-soft` (`.banner.warn`).
- **Disabled state has its own token**, deliberately overriding the source mockup's `#B4B3C0` because that value fails 4.5:1 contrast. Never reuse `--disabled-soft` for disabled text.

## Layout

- **App shell:** fixed 60px icon sidebar (`.sidebar`, sticky, full height) + flexible main column. Sidebar items are icon-only with `sr-only` text labels and `title` tooltips.
- **Topbar:** 56px, sticky, holds the view title, optional skip-pause control, and workspace label.
- **View padding:** the five direct children `#view` ever receives — `.view-pad`, `.run-layout`, `.setup-wrap`, `.campaign-layout`, `.report-scroll` — each set their own outer padding (default `30–44px` vertical, `24–40px` horizontal, tightening on tablet).
- **Two-column detail layouts** (`.campaign-layout`, `.run-layout`) pair a flexible main column with a fixed-width rail (`.campaign-rail` 388px, `.run-rail` 300px). `.campaign-layout.single-col` drops the rail for a centered 780px single column (Session detail).
- **Setup** centers a fixed 720px column (`.setup-wrap` → `.setup`).
- **Report** caps content at 720px inside a scrolling pane (`.report-scroll`) that can share width with a fixed evidence drawer.
- **Responsive breakpoint: 1179px** (`@media (max-width: 1179px)`, i.e. tablet and below). Two-column layouts stack to one column, rails go full-width, the three-column `.steps` grid becomes one column, outer padding tightens, and the evidence drawer clamps to `min(400px, 88vw)`. There is no phone-specific breakpoint below that; the stacked tablet layout is the smallest supported width.

## Shape and depth

- Radii step from `8px` (buttons, inputs) → `10px` (panels, tables) → `12px` (cards, modals) → `18–20px` (pills). Nothing uses a fourth, sharper or rounder value.
- Depth is nearly flat: borders (`--border`, `--divider`) do the separating work, not shadows. The only shadows in the system are the evidence drawer's entrance shadow (`-8px 0 24px rgba(26,26,46,.08)`) and the modal/drawer backdrop scrim (`rgba(26,26,46,.28–.4)`). Do not add drop shadows to cards, buttons, or panels.

## Component language

- **Buttons** (`.btn`): `secondary` (white, border, hover recolors to pink), `primary` (solid pink, white text), `danger` (danger-colored text on the secondary shape), `lg` size variant. Disabled buttons swap to `--surface`/`--disabled` regardless of variant.
- **Disabled-with-reason** (`.dis-wrap`): a hover/focus tooltip (`.dis-tip`, `role="tooltip"`) wraps a disabled control, because a native `disabled` element swallows pointer/focus events and can't show its own tooltip. This is the required pattern anywhere a control must stay visibly disabled and explain why (see AGENTS.md's disabled-feature-honesty rule).
- **Badges / pills / status dots**: badges are static uppercase labels; pills are interactive filters/toggles with `aria-pressed`; `.status` pairs a colored dot with colored text per state (`ready`/`running` pink, `complete` ink, `failed` danger, `draft` quiet).
- **Tables** (`.table`/`.thead`/`.trow`) are CSS grid rows, not `<table>` markup; header row uses the kicker typography.
- **Forms**: `.input` and the Key-Message/brief-review inline editors (`.km-*`, `.brief-item`) share the same focus treatment (pink border + 3px pink-tint ring) and the same error treatment (`--danger` border + `.field-error` text).
- **Cards / panels / empty states**: `.card`/`.rail-card` for content blocks, `.empty-block`/`.empty-card` for zero-state prompts with a centered icon badge, heading, supporting copy, and action row.
- **Stepper** (`.stepper`/`.step-row`/`.step-dot`): dot states are `done` (solid pink), `current` (spinning pink-ring), and pending (plain border); a connecting `.step-line` runs between rows.
- **Evidence drawer** (`.ev-drawer`): fixed overlay on narrow/tablet widths, inline panel on wide viewports (`.ev-drawer.overlay` toggles the fixed positioning + shadow + backdrop). Filter pills at the top, scrollable evidence cards below.
- **Modal** (`.modal-backdrop`/`.modal`): centered, capped at 860px wide and 90vh tall, scrollable body; used for both the file-preview modal and the confirm dialog (`.confirm-modal` narrows it to 440px).
- **Toggle switch** (`.switch`): custom track-and-thumb control layered over a visually-hidden native checkbox.
- **Banners / notices**: `.banner.warn` and `.banner.error` for run-level messages; `.notice`/`.notice.pink` for lower-emphasis inline guidance.

## Interaction states

- **Focus:** global `:focus-visible` gives a 2px solid pink outline with 2px offset and a 4px corner radius. Primary buttons invert this (white outline, pink box-shadow ring) so it stays visible against the solid pink fill. Inputs and the toggle switch use a 3px `--pink-tint` box-shadow ring instead of the default outline.
- **Hover:** secondary surfaces (buttons, pills, sidebar items, table rows, icon buttons) recolor toward pink and/or gain a `--pink-tint`/`--row-hover` background; nothing changes size or elevation on hover.
- **Pressed / active state:** `aria-pressed` on every filter pill and inclusion chip (`.km-chip`), `aria-disabled` on unavailable sidebar items, `role="status"`/`role="alert"` on banners, `role="status" aria-live="polite"` on the Key-Message drafting status line and the async progress region, `role="dialog" aria-modal` on the confirm modal, file-preview modal, and evidence drawer.
- **Disabled:** every disabled control uses the `--disabled` text/icon color (never `--disabled-soft`) and `cursor: not-allowed`; where the disabled reason needs to be legible, it goes through the `.dis-wrap` tooltip pattern above rather than a `title` attribute alone.

## Motion

- Route changes, stage advances, and the evidence drawer's entry all animate purely from DOM insertion — inserting or replacing a node triggers its keyframe animation, so no JavaScript drives the motion itself (only the stepper needs a one-off `.advanced` marker class, because it rebuilds all rows on every progress tick and would otherwise re-animate all five).
- `view-rise` (`.22s`, custom `cubic-bezier(.22,.61,.36,1)`, fade + 8px rise) applies to exactly the five top-level nodes `#view` ever mounts: `.view-pad`, `.run-layout`, `.setup-wrap`, `.campaign-layout`, `.report-scroll`. The report screen animates `.report-scroll`, not `.report-layout`, because the wide-viewport evidence drawer is a `position: fixed` sibling inside `.report-layout`, and a `transform` on an ancestor of a fixed element becomes that element's containing block — animating the parent would drag the fixed drawer along with it.
- `step-advance` (`.3s`, overshoot `cubic-bezier(.34,1.4,.64,1)`) scales in the current step's dot when the stepper's active row changes.
- `drawer-in` / `backdrop-in` (`.22s` / `.18s`) fade+slide the evidence drawer and fade its backdrop scrim in on mount.
- **Reduced motion:** `prefers-reduced-motion: reduce` collapses every animation/transition duration to `.01ms` globally, then explicitly restores static equivalents for the two states that would otherwise lose meaning — the spinner and the current step-dot keep their pink coloring without spinning.

## Presentation accessibility

- Contrast: text tokens are chosen to clear 4.5:1 on white; `--disabled` intentionally overrides the source mockup's failing gray (see Color usage rules above). Preserve this deviation when re-syncing from the mockup.
- Every interactive element is keyboard-operable and shows a visible focus ring (see Interaction states).
- Live regions (`aria-live="polite"`) cover the Key-Message drafting status and run-progress text so screen-reader users get async updates without re-reading the page.
- Modals and the evidence drawer use `role="dialog" aria-modal="true"` with a descriptive `aria-label`; the evidence drawer restores focus to its trigger on close (`docs/architecture.md`).
- Filter/toggle controls expose state via `aria-pressed`, not color alone; unavailable sidebar destinations use `aria-disabled="true"` rather than being removed from the tab order silently.
- Reduced-motion support is mandatory for any new animation (see Motion) — a new keyframe must be added to the `prefers-reduced-motion` block in the same change that introduces it.
