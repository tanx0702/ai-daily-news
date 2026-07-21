# Change Proposal

## Why

Authentication decisions are currently spread across route handlers, service helpers, and ad hoc token parsing utilities.

This matters now because inconsistent auth enforcement is creating regression risk, makes tests hard to trust, and slows any work that touches protected endpoints.

## What Changes

- Introduce a single authentication boundary for protected request evaluation.
- Move request-level auth checks behind a stable interface.
- Standardize unauthorized and forbidden decision handling.
- Preserve existing externally approved login and session behavior.

## Capabilities

### New Capabilities

- None

### Modified Capabilities

- `auth-request-gating`

## Scope

### In Scope

- Request authentication boundary definition
- Centralized auth decision object or equivalent
- Shared token parsing and validation entry point
- Migration of protected request paths onto the new boundary
- Standardized unauthorized and forbidden response mapping

### Out of Scope

- Changing identity provider integrations
- Replacing session storage
- Redesigning roles or permissions
- Adding single sign-on or multi-factor authentication

## Impact

- Affected code areas: route middleware, auth helpers, protected controllers, service entry points
- Affected APIs or interfaces: request auth context, auth decision interface, protected route entry points
- Dependencies or systems touched: token verification utilities, session lookup logic, request middleware chain
