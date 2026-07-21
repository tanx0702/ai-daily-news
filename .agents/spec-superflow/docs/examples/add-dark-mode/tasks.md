# Implementation Tasks

## 1. Theme Foundation

- [ ] 1.1 Add a centralized theme state source
- [ ] 1.2 Read system preference when no explicit user preference exists
- [ ] 1.3 Persist explicit user preference locally

## 2. Theme Application

- [ ] 2.1 Add a root-level theme attribute or class
- [ ] 2.2 Define light and dark color tokens for shared UI surfaces
- [ ] 2.3 Update core text, background, and control styling to consume shared tokens

## 3. User Interaction

- [ ] 3.1 Add a visible theme toggle
- [ ] 3.2 Ensure the toggle updates theme state immediately
- [ ] 3.3 Ensure the toggle reflects the active theme state

## 4. Verification

- [ ] 4.1 Add failing tests for initial theme selection and persisted preference behavior
- [ ] 4.2 Add failing tests for manual toggle behavior
- [ ] 4.3 Run regression checks for readable contrast on primary surfaces
- [ ] 4.4 Review implementation against the execution contract
