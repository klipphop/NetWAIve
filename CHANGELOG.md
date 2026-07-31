# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
