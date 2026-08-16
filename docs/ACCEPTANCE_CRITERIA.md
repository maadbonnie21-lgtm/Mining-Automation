# Acceptance Criteria

## Global release gates

A release candidate fails if any required gate fails.

Required for production release:
- all required automated tests pass
- zero known release-blocking defects inside the supported envelope
- supported workflows complete repeated end-to-end cycles
- pause/resume and scheduled break transitions pass
- recovery scenarios pass
- regression suite passes
- installation and launch are verified
- GUI is integrated and production-ready
- diagnostics capture sufficient evidence for failures
- supported environments are explicitly documented

## Milestone gates

### M1 — Foundation and capture
- package installs in a clean supported environment
- application entry point starts without import errors
- capture interface is defined and testable
- frame objects carry identity and monotonic timing metadata
- capture failures are surfaced explicitly

### M2 — Resource perception
- detector contract returns typed resource observations
- validation dataset exists for selected initial supported workflow
- available/depleted classification has objective evaluation metrics
- low-confidence/unknown classification is supported
- failures are diagnosable and do not become controller success

### M3 — Inventory perception
- occupancy model matches the declared supported inventory layout
- empty/occupied states are tested against representative fixtures
- uncertainty/staleness is represented

### M4 — World state
- observations are fused into deterministic typed state
- stale evidence expires
- confidence/unknown states are preserved
- attempted actions are distinct from verified results

### M5 — Closed-loop mining
- controller selects only validated available targets
- interaction intent includes preconditions and expected evidence
- progress depends on observed result, not a blind fixed wait
- timeout/failure enters a defined recovery path

### M6/M7 — Navigation and banking
- supported checkpoints/landmarks are versioned
- route progress requires localization evidence
- bank interface opening is verified
- deposit result is verified from inventory state
- failed checkpoint/banking events recover or safely stop

### M8 — Scheduling and reacquisition
- arbitrary reasonable active/inactive sequences are represented
- transition timing is deterministic/tested
- resume requires reacquisition
- pre-break state is not blindly assumed after inactivity

### M9 — Recovery/diagnostics
- supported failure classes have explicit recovery policies
- recovery attempts and escalations are logged
- failure snapshots/evidence can be retained
- reproducible failures can be promoted to regression fixtures

### M10 — GUI/package/release
- core user workflow requires no terminal, coordinates, detector training, or config editing
- selectors expose only production-supported workflows by default
- Start/Pause/Stop and routine state are clear
- errors/recovery are understandable to a normal user
- installer/package launches successfully on supported environment

## Definition of supported

A mine/location/ore combination may only be labeled production-supported after its perception, localization, navigation, banking, scheduling/resume, recovery, regression, and end-to-end validation requirements are satisfied.
