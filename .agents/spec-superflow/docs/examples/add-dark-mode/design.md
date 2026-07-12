# Technical Design

## Context

- Current state: the UI uses a single default theme and does not expose theme preference controls
- Constraints: the feature should work without server persistence and should not introduce heavy theming dependencies
- Stakeholders: end users, frontend engineers, QA reviewers

## Goals

- Add reliable light/dark theme switching
- Follow system preference on first load
- Persist an explicit user choice locally
- Minimize the amount of UI code that needs per-component theme branching

## Non-Goals

- Shipping multiple branded themes
- Adding backend preference storage
- Redesigning unrelated UI components

## Decisions

### Decision 1

- Choice: use a single theme state source with a root-level theme attribute and tokenized colors
- Rationale: centralizing theme state reduces drift and avoids scattered conditional styling
- Alternatives considered: per-component toggles and duplicated light/dark class logic

### Decision 2

- Choice: persist explicit choice in localStorage
- Rationale: it is simple, browser-native, and enough for a client-side preference
- Alternatives considered: cookies and backend profile storage

## Risks And Trade-Offs

- Theme flash on initial load -> initialize theme as early as possible in the app shell
- Incomplete dark-mode coverage -> prioritize shared tokens and core surfaces first

## Migration Plan

- Rollout steps: add theme provider, add toggle, migrate core tokens, verify contrast
- Rollback steps: remove toggle entry point and fall back to the existing single-theme token set

## Open Questions

- Question: should the toggle live in the header or a settings drawer?
- Decision owner: product or design owner
