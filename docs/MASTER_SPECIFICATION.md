# Master Specification

## Product objective

Build a complete, polished, highly aesthetic, exceptionally user-friendly, vision-driven autonomous Old School RuneScape mining desktop application.

The final product is not a proof of concept, coordinate macro, disconnected script collection, developer demo, or framework requiring the user to finish configuration or debugging.

## Intended user workflow

**Open application → select a supported ore → select a supported mine → select world → configure run/break routine → press Start.**

The user should not need programming knowledge, terminal use, screen-coordinate entry, detector training, configuration-file editing, or knowledge of the internal computer-vision system.

## Core engineering requirements

### Professional desktop UX
The GUI is a first-class engineering requirement. It must provide clear ore/mine/world selection, visual routine building, Start/Pause/Stop controls, current activity/location, inventory/session status, next break information, and understandable recovery/error messages. Advanced diagnostics may exist but normal operation must not require them.

### Vision-driven closed-loop control
Live game frames are the primary source of truth. The system should combine deterministic CV, temporal/image-change analysis, feature matching, classifiers, object detection, and other appropriate methods rather than forcing all perception through a single model.

Raw observations must be converted to structured world state. Decision logic operates on that state, not uncontrolled individual pixels.

Important actions follow:

**observe → estimate → act → observe → verify → continue or recover**

Fixed waits may be used as bounded timeouts or pacing but must not be the primary proof that an action succeeded.

### Structured state
The world-state model should represent, as appropriate:
- current location and confidence
- selected ore/mine/world
- inventory occupancy
- detected resources and availability state
- player/activity state where inferable
- navigation checkpoint/progress
- bank visibility/interface state
- current objective/workflow
- scheduler state and elapsed runtime
- expected next observation/event
- confidence and uncertainty

### Mining workflow
Identify a validated available target, interact within its valid interaction region, observe the resulting state, verify expected evidence, and then select the next action. Inventory state is continuously monitored and triggers banking at the configured condition.

### Banking workflow
Localize the supported bank destination, interact with the supported banking object/interface, verify interface appearance, perform the configured deposit operation, verify inventory state, leave/close appropriately, and return to mining.

### Navigation and localization
Supported mine/bank pairs ship with validated navigation knowledge. Navigation uses landmarks/checkpoints/localization confidence and does not blindly assume waypoint motion succeeded. Failure to recognize expected checkpoints or sufficient localization confidence must invoke recovery instead of compounding uncertainty.

### Recovery
Recovery is first-class. Define recovery for missed interactions, depleted targets, delayed results, banking failures, uncertain location, unexpected interfaces, checkpoint failures, break/resume transitions, and other supported faults. Prefer reacquisition or safe stop over blind progression.

### Interaction variation
Interactions may vary within validated target regions and bounded timing/movement parameters. Variation is subordinate to correctness and must never intentionally move outside validated interaction regions.

### Scheduling
Provide a visual ordered routine builder supporting arbitrary reasonable sequences of active and inactive segments. Users can add, remove, edit, duplicate, and reorder segments. Show active segment, remaining time, upcoming segment/break, and total planned duration. Resume requires visual reacquisition rather than assuming pre-break state persisted.

### Knowledge base
Research reputable public OSRS resources, especially the OSRS Wiki, and encode verified mining knowledge in a structured, version-controlled knowledge base. Distinguish knowledge from production support. Public reference imagery may bootstrap understanding; captured validation frames establish actual supported behavior.

### Diagnostics
Development/validation builds record structured events, state transitions, perception confidence, expected vs observed outcomes, navigation checkpoints, scheduling transitions, recovery attempts, and failure frames. Failures should become reproducible regression cases where practical.

## Reliability and release standard

Pursue 100% reliability inside an explicitly documented tested support envelope. Do not claim universal reliability against future game updates, network events, OS changes, unknown resolutions, or untested states.

A production release must:
- pass 100% of required release acceptance tests
- have zero known release-blocking defects in the supported envelope
- pass repeated end-to-end and long-duration validation
- pass pause/resume and scheduling transitions
- pass supported recovery/fault-injection scenarios
- pass regression tests
- have tested installation and launch
- include the finished integrated GUI, diagnostics, and documentation

If a required test fails, the release fails.

## Development artifacts are not the final product

Standalone detectors, coordinate/route tools, test harnesses, debug overlays, dataset tools, prototype GUIs, partial controllers, CLI launchers, and milestone builds may exist internally but must be labeled development/test artifacts.

## Canonical milestone order

1. application foundation and reliable capture
2. resource detection
3. available/depleted classification
4. inventory recognition
5. structured world-state engine
6. closed-loop mining at one supported workflow
7. visual localization/checkpoints
8. mine-to-bank navigation
9. verified banking
10. bank-to-mine navigation
11. complete autonomous cycle
12. routine builder/session scheduler
13. break transition and state reacquisition
14. recovery framework
15. diagnostics/replay/regression conversion
16. production GUI integration
17. packaging/installer
18. regression/endurance/fault injection
19. release-candidate validation
20. final production handoff

Later milestones must not conceal unreliable earlier foundations.

## Definition of done

The project is complete only when a normal user can install and launch the production application, select a validated ore/mine/world/routine, press Start, and successfully run the complete supported workflow without terminal use, manual coordinate entry, detector training, configuration-file editing, or developer intervention, while retaining diagnostics sufficient to explain and reproduce failures.
