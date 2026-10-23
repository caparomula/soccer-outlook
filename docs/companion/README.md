# Soccer Outlook companion app

These documents describe how to build a phone and tablet companion to Soccer Outlook while keeping its website useful and consistent. The app should answer the same question: what soccer can I watch with my services, and which matches are most worth my time?

This is a proposed development baseline; the companion app and its new data feed are not implemented.

The current target is a personal-use prototype, as clarified on October 23, 2026. Build and test it locally without waiting for data-permission inquiries or supplier selection. A new public feed and app distribution are outside this phase.

## Read the plan

| Document | What it answers |
| --- | --- |
| [Product specification](specification.md) | What should the app do, and what must remain consistent with the website? |
| [Architecture](architecture.md) | Which layer owns each decision, and how do data and state reach the screen? |
| [Data contract](data-contract.md) | What does the publisher supply, and how can clients safely accept updates? |
| [Engineering practices](engineering.md) | How do we test, document, review and maintain the implementation? |
| [Implementation plan](implementation-plan.md) | In what order do we build it, and what evidence completes each stage? |
| [Data licensing research](../data-licensing.md) | Which permission routes and paid suppliers could support the required uses? |

The [current user guide](../user-guide.md), [scoring guide](../scoring.md) and [schedule guide](../schedule-data.md) still describe the working website. Where this plan proposes a change, it identifies it explicitly.

## Recommended direction

Use Flutter for iOS and Android, with separate data, domain, application and presentation responsibilities. Reuse the existing Python collection and AI pipeline, adding a documented JSON export for local or private use by the companion. Keep source adapters replaceable so a future supplier change does not require rewriting the app. Do not make the app parse website markup or depend on the internal build cache.

Give the app and website one owner for preference-dependent rules. The proposed owner is a pure Dart core used directly by Flutter and compiled behind a small JavaScript interface for the existing website. First prove that interface works within acceptable browser size, startup and compatibility limits. This is a feasibility gate, not a claim that compiling Dart automatically makes the integration straightforward.

Keep Python's existing ownership of source reconciliation, broadcast evidence, rights configuration and the base Outlook score. The shared core consumes those facts and owns eligibility, personalized ranking, AI selection and expiry, and time grouping. It must not recalculate the base score independently.

Start with a complete viewing experience: saved preferences, live and upcoming matches, top picks, optional AI content, details and useful offline behavior. Account connections, reminders, home-screen widgets and cross-device synchronization can follow separately. The personal prototype needs no new AI subscription or public hosting. Revisit the [licensing research](../data-licensing.md) and delivery model if public distribution becomes a goal.

## What we are taking from Pit Dash

The design adopts Pit Dash's strongest practices: immutable snapshots; explicit clocks; one owner for rules and configuration facts; persistent last-good data; independent rule examples; recorded regressions; measured responsive layout; and documentation that changes with the code.

It adapts these practices to this project. It does not require Pit Dash's robotics services, temporal history infrastructure, daemon system, or replay interface. The [engineering guide](engineering.md) maps the adopted practices to their Pit Dash sources and to verification work.

## About this document

- Status: proposed, October 20, 2026. Requesting these documents does not authorize an app release or mean the feasibility gates have passed.
- Current Soccer Outlook baseline: [`cb0475e`](https://github.com/caparomula/soccer-outlook/tree/cb0475e).
- Pit Dash reviewed: [`865fa263`](https://github.com/caparomula/pit-dash/tree/865fa263062b0870b9ae71555132d4ace894dccf).
- These are original Soccer Outlook planning documents. Existing project licensing and third-party attribution requirements continue to apply.
