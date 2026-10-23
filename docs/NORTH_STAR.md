# North Star

Status: Current
Owner: Krish Raja
Last verified: 2026-10-18

> By 23 November 2026, Lauren manages, iterates, upgrades and actions Lozatron herself, in her own accounts, without Krish.

Set by Krish on 18 October 2026 and rewritten by him on 23 October 2026. It is not reached yet. It is what every session in this repository works toward.

## The number

**Share of Lozatron's runtime and controls in Lauren's own accounts and run by her alone, with no help from Krish**: 100% by 23 November 2026, on a linear weekly ladder. Checkpoints: 16 Nov 2026: 80%; 23 Nov 2026: 100%.

```yaml north-star-metric
metric: "Share of Lozatron's runtime and controls in Lauren's own accounts and run by her alone, with no help from Krish"
unit: %
direction: up
target: 100
deadline: 2026-11-23
ladder_shape: linear
source: "The handover checklist in this repository"
status: active
ladder:
  - [2026-10-26, 20.0]
  - [2026-11-02, 40.0]
  - [2026-11-09, 60.0]
  - [2026-11-16, 80.0]
  - [2026-11-23, 100.0]
```

Urgency (U1 to U5) is worked out every week from where the number stands against that week's target, by [the harness contract](https://github.com/krishanraja/ai-harness/blob/main/contract/north-star-metric.md). Set by Krish on 23 October 2026.

## How to use it

- Read it before any work, every session.
- Before you start, say which part of it the task moves. If the task moves nothing toward it, say so first and let Krish decide.
- Prefer the work that moves it furthest, and close each session by noting what moved.
- Only Krish changes this file. Record his ruling in the commit body as `Ruling (Krish, YYYY-MM-DD): ...`.
- When it is reached, say so and ask Krish for the next one.
