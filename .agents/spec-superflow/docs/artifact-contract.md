# Artifact Contract

`spec-superflow` uses five primary artifacts in each change:

1. `proposal.md`
2. `specs/`
3. `design.md`
4. `tasks.md`
5. `execution-contract.md`

The first four are planning artifacts. The fifth is the execution handshake.

## Artifact Roles

### `proposal.md`

Defines:

- why the change exists
- what is in scope
- what is explicitly out of scope
- which capabilities are affected

### `specs/`

Defines:

- required behavior
- scenarios and acceptance conditions
- behavioral edges the implementation must respect

### `design.md`

Defines:

- architecture and component boundaries
- interface and dependency decisions
- trade-offs and risk areas

### `tasks.md`

Defines:

- implementation ordering
- dependency-aware work breakdown
- completion units that can become execution batches

### `execution-contract.md`

Defines:

- the approved intent lock
- the approved behavior summary
- implementation constraints
- task batches
- test obligations
- review gates
- escalation rules

## Mapping

`spec-superflow` converts planning artifacts into execution inputs:

- `proposal.md` -> intent lock and scope fence
- `specs/` -> test obligations and acceptance checks
- `design.md` -> implementation constraints
- `tasks.md` -> execution batches

## Guardrail

Implementation starts only after:

- planning artifacts exist
- `execution-contract.md` exists
- the user approves the execution contract
