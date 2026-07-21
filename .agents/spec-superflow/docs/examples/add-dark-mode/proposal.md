# Change Proposal

## Why

Users need a dark mode because the current interface is uncomfortable in low-light environments and visually inconsistent with modern system theme expectations.

This matters now because the product is moving beyond internal use and theme mismatch creates immediate usability friction.

## What Changes

- Add a user-facing dark mode for the web UI.
- Respect system preference on first load.
- Allow manual light/dark switching after first load.
- Persist the user's explicit theme choice locally.

## Capabilities

### New Capabilities

- `ui-theme`

### Modified Capabilities

- None

## Scope

### In Scope

- Theme state management
- System preference detection
- User toggle in the UI
- Persistent theme preference
- Core page colors and text contrast updates

### Out of Scope

- Custom theme palettes
- Per-page theme overrides
- Server-side preference sync

## Impact

- Affected code areas: app shell, root layout, shared styling tokens, settings or header UI
- Affected APIs or interfaces: theme provider or theme hook used by the UI
- Dependencies or systems touched: browser localStorage and CSS variables or equivalent theme token system
