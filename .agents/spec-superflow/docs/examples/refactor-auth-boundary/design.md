# Technical Design

## Overview

Refactor request authentication behind one explicit boundary so protected routes stop making ad hoc auth decisions.

The design focuses on consolidation, not capability expansion.

## Architecture

- Introduce an `AuthBoundary` interface or equivalent module responsible for request-level authentication decisions.
- Return a stable `AuthDecision` or `AuthenticatedRequestContext` shape that downstream handlers can consume directly.
- Move token parsing, session lookup, and principal assembly behind this boundary.
- Keep login issuance flows and persistence mechanisms unchanged.

## Data And Control Flow

1. Protected request enters middleware or route guard.
2. The guard delegates to `AuthBoundary`.
3. The boundary validates credentials and builds the request auth result.
4. Success attaches the shared auth context for downstream use.
5. Failure maps to standardized unauthorized or forbidden handling.

## Constraints

- Preserve externally visible login behavior.
- Avoid changing the current identity provider or session store.
- Keep the boundary thin enough to adapt existing helpers instead of forcing a total rewrite.
- Migrate incrementally so regressions are isolated by route group or middleware layer.

## Trade-Offs

- A thin adapter-based boundary is safer for brownfield code than a full auth subsystem rewrite.
- Centralization may reveal legacy helper inconsistencies that need small compatibility shims.
- Short-term duplication may exist during migration, but the target state is one request auth entry point.

## Testing Strategy

- Add failing tests that prove protected requests stop bypassing the shared boundary.
- Add failing tests for missing credentials, invalid tokens, and authenticated-but-forbidden outcomes.
- Run regression tests for existing login and session issuance behavior to confirm no external contract drift.
