# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.1-P5] - 2026-08-05

### Changed
- Standardize the missing DeviceType/ModuleType manufacturer name to exactly `Generic`; `Unknown` and `Inconnu` are no longer valid defaults.
- Keep plan-time generic create lookups active so an existing `Generic` manufacturer is reused by ID instead of recreated.
- Document the same naming rule in the NetWAIve development skill and the system prompt.

### Tests
- Updated regression fixtures from `Unknown` to `Generic`.
- Added ModuleType coverage for the missing-manufacturer default and prompt contract assertions.

## [0.5.1-P4] - 2026-08-05

### Fixed
- Canonicalize LLM-corrupted symbolic references by preferring exact step IDs, then repairing known step or ordinal aliases with arbitrary hyphen suffixes and mapping them to `data.id`.
- Repair suffixed ID paths such as `${call_ABC123.data.id-device}` while preserving native integer IDs and rejecting genuinely unknown references.
- Expand `quantity`, `count`, or `qty` component specifications into 1–512 distinct, numbered component-template creates before Pending confirmation.
- Perform fail-closed exact NetBox lookups for resolvable generic creates before Pending; existing manufacturers, sites, and other objects are removed from the plan and their integer IDs are injected into dependents.

### Changed
- The intent prompt requires explicit component quantities to appear as N distinct Pending operations.
- Fully reused plans return a no-change result instead of an empty confirmation card.

### Tests
- Added regression coverage for corrupted root/path suffixes, exact-ID precedence, ordinal aliases, eight-port expansion, existing Manufacturer/Site reuse, ID rewriting, and lookup failure isolation.
- Verified existing Manufacturer/Site reuse against the live NetBox API without mutation.

## [0.5.1-P3] - 2026-08-04

### Fixed
- Generate every supported missing NetBox slug in Python from `name`, `model`, or `display` before OpenAPI required-field validation.
- Keep technical slug generation separate from unresolved mandatory business values.
- Suppress `slug` from clarification payloads; when its derivation source is absent, request the writable business source (`name`, `model`, or `display`) instead.

### Changed
- The chat prompt never asks users for slugs and explicitly asks one clear question when a required business value has no valid default.
- Zero-Ask completion remains limited to deterministic/defaultable dependencies and no longer authorizes invented site names, relations, or addresses.

### Tests
- Added coverage proving an optional writable slug is generated before validation and only the missing business field is reported.
- Added chat coverage proving a missing required business value yields one clarification question and no Pending plan.

## [0.5.1-P2] - 2026-08-04

### Fixed
- Convert upstream LLM HTTP 504 and gateway timeout failures into a stable JSON `llm_gateway_timeout` response.
- Strip upstream HTML/error bodies and preserve pending/session state without executing changes.

### Tests
- Added chat API coverage proving a 504 HTML payload becomes clean JSON with HTTP 504.
- Added Django to the `dev` optional dependency set so a clean `uv run --extra dev pytest` environment collects the full suite.

## [0.5.1-P1] - 2026-08-04

### Added
- Added deterministic Zero-Ask Device auto-chaining for Manufacturer, DeviceType, component templates, Site, and Device in one Pending plan.
- Added transparent raw NetBox fallback when an exact custom model is absent from NDX.
- Added neutral `Generic` defaults for missing manufacturer/model intent.

### Changed
- Updated the short intent-only system prompt with Zero-Ask Completion, symbolic dependency chaining, and transparent raw fallback rules.
- NDX parent reuse now returns the parent ID so a following Device step can consume `${call_id.data.id}`.

### Tests
- Added raw fallback, exact NDX-to-Device chaining, default manufacturer, symbolic relation, and full-plan confirmation coverage.

## [0.5.0-P4] - 2026-08-04

### Changed
- Replaced the main chat system prompt with a concise intent-only contract focused on business values and direct Pending plans.
- Delegated validation, enrichment, fallback selection, deduplication, typed references, and execution entirely to the Python runtime.
- Clear create intents can now enter Pending directly; deterministic existence checks still run at confirmed execution and fail closed if NetBox cannot be queried.
- Kept read-only provenance mandatory for updates and deletes, with target IDs scoped to the exact endpoint.
- Simplified plan-repair instructions to complete business intent without exposing backend verification procedures.

### Tests
- Added prompt-contract coverage excluding existence, role, and slug heuristics.
- Added direct create-intent coverage proving Pending generation without a preliminary read or unnecessary question.

## [0.5.0-P3] - 2026-08-04

### Fixed
- Replaced raw step-variable string interpolation with a strict typed Python resolver.
- Added typed dict/list path navigation, including indexed expressions such as `${call_1.data.candidates[0].slug}`.
- Stop execution with explicit errors when a referenced step failed, returned an empty value, exposed a missing key, or used an invalid index.

### Changed
- Exact references preserve native Python types such as NetBox integer IDs; embedded string interpolation accepts scalar values only.
- Unknown opaque call aliases are never guessed or mapped to an unrelated output.
- Conflicting duplicate step IDs and dependency cycles are rejected before execution.

### Tests
- Added multi-step pipeline coverage for generated integer IDs, indexed values, empty parent results, malformed paths, and ambiguous aliases.

## [0.5.0-P2] - 2026-08-04

### Fixed
- Resolve pynetbox Manufacturer relations before serialization so existing DeviceTypes and ModuleTypes are correctly bypassed.
- Post-validate model and Manufacturer identity before any NDX bypass.

### Tests
- Added a pynetbox relation serialization regression and validated the live Catalyst 9200-24P bypass.

## [0.5.0-P1] - 2026-08-03

### Added
- Added a deterministic Python resolver that enriches NetBox create payloads before OpenAPI validation.
- Added live Device role resolution, preferring the existing `Switch` role and otherwise using the lowest stable live role ID.

### Changed
- NDX DeviceType and ModuleType workflows now query NetBox first and bypass the complete import when the parent object already exists.
- Required slug fields discovered from the live OpenAPI schema are generated automatically from business names or models.

### Tests
- Added regression coverage for existing-parent NDX bypass, required-slug enrichment, preferred Device role resolution, and deterministic role fallback.

## [0.4.15] - 2026-08-03

### Fixed
- Generate validated NetBox slugs for NDX Manufacturers and DeviceTypes when source specifications omit them.
- Preserve a normalized slug in ModuleType DTOs while omitting the unsupported field from NetBox ModuleType writes.
- Resolve exact NDX catalogue matches directly to complete specifications instead of returning an intermediate variant-selection step.

### Changed
- Return the complete composite Pending confirmation card immediately after an exact NDX specification is loaded, removing an unnecessary extra LLM turn.

### Tests
- Added slug fallback coverage for Manufacturer, DeviceType, and ModuleType payloads.
- Validated a live Cisco Catalyst 9200-24P import with 34 templates and a duplicate-free idempotent retry.

## [0.4.14] - 2026-08-03

### Fixed
- Prevented stale in-flight chat and history responses from restoring context after an UI Reset.
- Added a Redis-backed reset generation guard for consistent multi-worker Gunicorn purges.
- Added topological component-template execution and symbolic dependency resolution for rear/front ports and interface bridges.

### Tests
- Added stale-request reset, backend generation, AbortController, and component dependency-ordering coverage.

## [0.4.13] - 2026-08-03

### Added
- Added complete NDX ModuleType ingestion with automatic Manufacturer inclusion and parent-scoped component templates.
- Added a dedicated backend reset endpoint used exclusively by the existing UI Reset buttons.

### Changed
- Generalized the composite NDX DTO and executor for DeviceTypes and ModuleTypes.
- Made exact NDX imports complete by default in one confirmation step, with no minimal/full or separate Manufacturer prompt.

### Fixed
- Reset now purges every server-side NetWAIve session, message history, pending write, and session authorization flag before clearing the UI.
- Removed chat `/reset` and `/clear` command handling and the legacy history-clear endpoint.
- Preserved complete parent metadata including part number, height, airflow, weight, and supported component collections.

### Tests
- Added backend reset, backend-first UI reset, automatic Manufacturer, direct ModuleType binding, and complete ModuleType import coverage.

## [0.4.12] - 2026-08-03

### Changed
- Split NDX catalogue access, composite DTO validation, and NetBox import execution into dedicated modules.
- Replaced vendor aliases with metadata-driven name, token, and acronym resolution from the live NDX vendor catalogue.
- Made technical search index all nested NDX metadata without field- or vendor-specific rules.

### Fixed
- Resolved Community specs through live repository metadata and manufacturer directory listings instead of guessed filenames.
- Blocked NetBox Labs-only or otherwise incomplete specs before pending confirmation.
- Enforced strict DeviceType DTOs, positive parent IDs, exact identity matching, and parent-scoped idempotence.
- Removed all vendor/model-specific runtime literals and unsafe substring mutation matching.

### Tests
- Added generic Juniper, Fortinet, Arista, Allied Telesis, ambiguity, malformed DTO, composite ordering, and scoped-idempotence coverage.

## [0.4.11] - 2026-08-03

### Fixed
- Restored detailed component resolution when the NDX index omits templates.
- Refused composite imports with zero interface templates.
- Added exact-reference priority and manufacturer normalization for common vendor labels.

## [0.4.10] - 2026-08-03

### Changed
- Made NetBox Data Exchange the exclusive catalogue/specification source.
- Removed DTL/GitHub terminology and fallback paths.

### Fixed
- Removed non-file image fields before NDX DeviceType writes.
- Preserved strict ambiguity stopping and NDX catalogue candidate selection.

## [0.4.9] - 2026-08-03

### Fixed
- Made composite DTL writes exception-safe through the standard tool wrapper.
- Added case-insensitive/containment matching to reuse existing manufacturers and templates.

## [0.4.8] - 2026-07-31

### Fixed
- Intercepted direct DeviceType creates and required NDX exact-model resolution before pending writes.
- Routed exact NDX/DTL specs into the composite import action instead of allowing empty DeviceTypes.

## [0.4.7] - 2026-07-31

### Changed
- Added NetBox Data Exchange catalogue search for vendors, models, and part numbers.
- Removed the need for hard-coded manufacturer aliases in catalogue resolution.

## [0.4.6] - 2026-07-31

### Changed
- Encapsulated DTL imports as one server-side composite pending action.
- Added an atomic DeviceType-first pipeline for all component templates.

### Fixed
- Prevented LLM truncation of large DTL component lists and condensed the confirmation card.

## [0.4.5] - 2026-07-31

### Fixed
- Resolved the DTL default branch dynamically from GitHub and added module-bay template plans.

## [0.4.4] - 2026-07-31

### Fixed
- Added GitHub directory discovery for generic DTL model requests and candidate selection.
- Preserved zero-prompt DTL DeviceType payloads and component-template import plans.

## [0.4.3] - 2026-07-31

### Fixed
- Kept DTL plan injection independent of private tool implementation details, restoring runner compatibility and tests.

## [0.4.2] - 2026-07-31

### Fixed
- Derived required fields from POST schemas only, preventing update-only `id` from blocking creates.
- Generated confirmed DTL import plans with manufacturer, DeviceType slug/u_height, and physical templates.

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
