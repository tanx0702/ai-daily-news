# Execution Contract

## Intent Lock

- Change name: `refactor-auth-boundary`
- Problem being solved: request authentication decisions are scattered and inconsistent, creating regression risk and making protected flows hard to evolve safely
- In scope: one shared request authentication boundary, standardized auth decisions, migration of protected request paths, consistent unauthorized and forbidden handling
- Out of scope: new identity providers, new login behavior, permission model redesign, session storage replacement

## Approved Behavior

- Approved requirements summary: protected requests must pass through one shared authentication boundary, auth failures must map consistently, and externally approved login behavior must remain unchanged
- Key scenarios: valid protected request, missing credentials, invalid token, authenticated but forbidden requester, existing login flow remains stable
- Acceptance checks: migrated protected routes no longer perform ad hoc request auth parsing, unauthorized and forbidden outcomes are standardized, login regression checks still pass

## Design Constraints

- Architecture constraints: centralize request auth decisions without rewriting the entire auth subsystem
- Interface constraints: downstream handlers should consume one stable auth context or decision shape
- Dependency constraints: keep current token validation and session lookup systems, adapting them behind the boundary
- Data constraints: preserve existing session and token semantics visible to clients

## Task Batches

### Batch 1

- Objective: establish failing tests and identify current request auth entry points
- Inputs: protected route inventory, current auth helpers, existing login flow behavior
- Outputs: failing auth-path tests and baseline regression coverage
- Done when: missing credentials, invalid token, forbidden path, and login regression checks are in place

### Batch 2

- Objective: introduce the shared auth boundary and migrate the first protected route group
- Inputs: batch 1 tests, auth helper inventory, target route group
- Outputs: boundary module, standardized auth result shape, first migrated route group
- Done when: migrated routes use the new boundary and pass the relevant tests

### Batch 3

- Objective: migrate remaining protected entry points and remove duplicate branching
- Inputs: established boundary, remaining protected handlers
- Outputs: consolidated request auth flow across protected routes
- Done when: duplicate request-level auth branching is removed and all relevant tests pass

## Test Obligations

- Behavior that must start with failing tests: missing credentials rejection, invalid token rejection, forbidden access mapping, shared boundary usage on protected routes
- Required edge cases: no credentials, malformed token, expired or invalid token, authenticated user without required access
- Regression-sensitive areas: login flow, session continuity, middleware ordering, protected route error mapping

## Review Gates

- Mandatory review points: after baseline tests are added, after the first route group migration, and before removing legacy auth branching
- Blocker categories: changed client-visible login behavior, inconsistent unauthorized or forbidden responses, hidden scope expansion into permission redesign

## Escalation Rules

- Return to `specifying` when: preserving existing login behavior is no longer sufficient and externally visible auth behavior needs to change
- Return to `bridging` when: migration batching, boundary shape, or test obligations change materially
- Do not continue implementation if: protected routes still depend on incompatible auth result shapes that were not accounted for in the current plan
