# Execution Contract

## Intent Lock

- Change name: `add-dark-mode`
- Problem being solved: the product needs a readable dark mode and a stable user-controlled theme choice
- In scope: system preference detection, manual toggle, local persistence, shared token updates
- Out of scope: custom theme presets, backend preference sync, unrelated visual redesign

## Approved Behavior

- The UI supports both light and dark mode
- First load follows system preference when no saved choice exists
- A saved explicit choice overrides system preference
- The user can toggle theme immediately from the UI
- Core text and surfaces remain readable in dark mode

## Design Constraints

- Theme state must come from one shared source of truth
- Root theme application should be centralized at app-shell level
- Local persistence uses browser storage only
- Shared tokens or variables should drive styling instead of scattered per-component logic

## Task Batches

### Batch 1

- Objective: establish theme state, system preference detection, and persistence
- Inputs: current app shell and client storage access
- Outputs: working theme state source with persisted choice handling
- Done when: first-load and returning-user behavior can be tested in isolation

### Batch 2

- Objective: apply shared theme tokens and visible UI toggle
- Inputs: completed theme state source
- Outputs: active theme styling and user-facing toggle
- Done when: users can switch theme live and core UI reflects the selected theme

## Test Obligations

- Behavior that must start with failing tests: initial theme selection, persisted preference override, manual toggle switching
- Required edge cases: no stored preference, stored preference exists, system preference differs from saved preference
- Regression-sensitive areas: root layout, shared tokens, text contrast, toggle state reflection

## Review Gates

- Mandatory review points: after Batch 1 foundation, after Batch 2 UI application, before closure
- Blocker categories: spec mismatch, missing persistence behavior, unreadable dark-mode contrast, no failing test before implementation

## Escalation Rules

- Return to `specifying` when: scope expands to multi-theme support or account-synced preferences
- Return to `bridging` when: theme architecture changes materially or batch boundaries no longer fit the approved design
- Do not continue implementation if: dark-mode behavior requires unstated product decisions or new behavior not covered by current specs
