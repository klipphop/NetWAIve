# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.1] - 2026-07-31

### Fixed
- Switched official DTL retrieval to the `main` branch.
- Exposed normalized DeviceType fields and physical component-template collections to the planner.

## [0.4.0] - 2026-07-31

### Added
- Added read-only retrieval of official Device Type Library YAML templates through the universal read tool.
- Added bilingual Beta banners and widget headers.

### Changed
- Instructed planning to propose close NetBox matches and DTL-backed component templates before creating missing dependencies.

## [0.3.27] - 2026-07-30

### Fixed
- Added live termination-occupancy preflight for generic `*_terminations` relations.
- Rendered duplicate-termination API conflicts as an actionable occupied-interface message.
- Preserved user-provided labels as direct payload values.

## [0.3.26] - 2026-07-30

### Changed
- Replaced endpoint-specific confirmation rendering with a generic action/endpoint/payload renderer.
- Added OpenAPI-driven preflight validation for required fields and enum choices before pending writes.

### Fixed
- Prevented incomplete or enum-invalid payloads from reaching NetBox before the user selects a schema-supported value.

## [0.3.25] - 2026-07-30

### Changed
- Added an internal graph-completion contract before a pending plan is exposed.
- Reworked the NetBox interactive approval card with Allow Once, Allow Session, and Deny actions.

### Fixed
- Prevented partial prerequisite plans and transition prose from being returned before the complete pending plan is assembled.

## [0.3.24] - 2026-07-30

### Fixed
- Added an internal plan-completion turn so isolated prerequisite creation cannot prematurely end a complex objective.

## [0.3.23] - 2026-07-30

### Changed
- Removed Device-specific relation rewriting and structured-text state injection from the generic runner.
- Returned native NetBox API error payloads without resource-specific translation.

## [0.3.22] - 2026-07-30

### Fixed
- Added live idempotence checks before generic creates and reused existing NetBox objects instead of triggering 409 conflicts.
- Normalized Device site, role, device type, and manufacturer text relations to real NetBox IDs.

## [0.3.21] - 2026-07-30

### Fixed
- Suppressed user-visible verification/continuation transitions during autonomous tool chaining.
- Identified existing reused Manufacturer, Device Type, Device Role, Site, and VLAN objects in confirmation cards.

## [0.3.20] - 2026-07-30

### Fixed
- Accepted opaque provider tool-call IDs during plan sanitation and resolved them against ordered execution outputs.
- Added confirmation transparency for existing NetBox objects reused by a plan.

## [0.3.19] - 2026-07-30

### Changed
- Added generic plan sanitation, strict deduplication, reference validation, and topological execution for all NetBox resources.

### Fixed
- Stopped unresolved variables before any NetBox API call and translated 400/409 validation errors into actionable French.

## [0.3.18] - 2026-07-30

### Fixed
- Translated NetBox and pynetbox dependency errors into actionable French messages without raw API payloads.
- Added proactive device role, site, and device type discovery guidance before device creation.

## [0.3.17] - 2026-07-30

### Fixed
- Resolved `${call_X.data.id}` and `${call_X.id}` symbolic references against real NetBox execution results.
- Cleared failed pending plans before a new request can be processed.

## [0.3.16] - 2026-07-30

### Changed
- Topologically order same-plan parent creations before dependent device operations.

### Fixed
- Prevented failed parent operations from being retried indirectly through dependent pending calls.
- Preserved only independently executable operations for partial-failure recovery and exposed the original NetBox error.

## [0.3.15] - 2026-07-30

### Added
- Structured YAML, JSON-like, and ASCII-tree inputs are treated as direct global creation plans.
- Recovery confirmation for operations that were not executed after a partial plan failure.

### Changed
- Cable planning now requires discovery or creation of both interfaces before the cable can reference their real or same-plan IDs.

### Fixed
- Removed unsupported site filters from IPAM prefix searches.
- Preserved unexecuted operations after a failed multi-step confirmation for immediate completion planning.

## [0.3.14] - 2026-07-30

### Fixed
- Updated the contextual-read regression fixture to model a read-only request under the stricter mutation tool-chain contract.

## [0.3.13] - 2026-07-30

### Fixed
- Prevented intermediate acknowledgement messages from ending a clear NetBox mutation request before its read/write tool chain and pending confirmation are produced.
- Added bounded same-cycle retries for transitional LLM responses, without exposing them to the user interface.

## [0.3.12] - 2026-07-30

### Fixed
- Prioritized explicit English action words over incidental French words in NetBox object names when selecting the response language.

## [0.3.11] - 2026-07-30

### Added
- Automatic language matching for French and English user requests, including deterministic confirmation cards and execution summaries.
- `CHANGELOG.md` as the release history for future Git tags.

### Changed
- Confirmation resource labels now use the language selected for the current session.

## [0.3.10] - 2026-07-30

### Fixed
- Closed the post-approval execution cycle without re-entering the LLM planner.
- Prevented prose-only confirmations without a live read and server-side pending plan.
- Prevented duplicate create plans when a matching live NetBox object already exists.
- Added explicit `execution_status` values to approval API responses.

## [0.3.9] - 2026-07-30

### Fixed
- Cleared pending confirmation state immediately after successful structured approval.
- Added live-object detection before proposing another create operation.

## [0.3.8] - 2026-07-30

### Changed
- Renamed the internal package, NetBox plugin, URLs, templates, static assets, and editable distribution to `netwaive`.
- Rebranded the user interface as NetWAIve.

### Fixed
- Added structured approval requests to avoid double prompting.
- Added semantic NetBox resource labels and cleaned delete summaries.
- Rejected L3 interface names derived from IP addresses.

## [0.3.7] - 2026-07-30

### Added
- Universal `pynetbox` read, write, and schema tools.
- Global confirmation workflow for dependent NetBox mutations.
- Floating and docked NetBox chat interface with session-backed history.
- Native available-IP discovery through `prefix.available_ips.list()`.

### Fixed
- Added read-before-write safeguards, symbolic reference resolution, and reset state cleanup.
