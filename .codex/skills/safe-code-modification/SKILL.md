---
name: safe-code-modification
description: MANDATORY reference before any agent or script writes to, patches, or commits code in a target repository. This capability is NOT YET BUILT in the current version of Lazarus. This file exists to define the guardrails BEFORE the capability exists, not after.
---

# Safe Code Modification — Guardrails

## Current status

Lazarus does not currently modify source code. The only write actions in this
version are: (1) generating a draft documentation PR, and (2) posting triage
labels/comments if explicitly enabled. Nothing in the current build touches
application source code, dependency files, or test files.

This skill defines the rules that MUST be followed before that capability is
ever built. Do not implement autonomous code patching without every rule
below being enforced in the implementation, not just documented here.

## Non-negotiable rules for any future code-modification capability

1. **Never operates on the original repository.** All modification happens
   on a fork owned by the operator. There is no code path that can write to
   an upstream repo the operator does not own.

2. **Never auto-merges.** Every change lands as a draft PR on the fork,
   reviewed by a human, merged by a human. No agent has merge permissions.

3. **Bounded blast radius per patch.** A single patch attempt may touch a
   hard-capped, small number of files. No patch may modify CI configuration,
   license files, or security-sensitive files (auth, secrets handling,
   network permissions) under any circumstance.

4. **Test-gated, with a hard iteration limit.** A patch is only kept if the
   test suite passes after applying it. If it does not pass after a small,
   fixed number of retry attempts, the agent stops and reports the failure —
   it does not keep mutating the code indefinitely searching for a passing
   state.

5. **Full rollback on any uncertain outcome.** If a patch causes a new,
   previously-passing test to fail, if the test suite's own exit behavior is
   ambiguous, or if the sandboxed run times out, the change is discarded
   entirely and the original file state is restored. Ambiguity always
   resolves to "do nothing," never to "try harder."

6. **Sandboxed execution only.** Any code execution (installing
   dependencies, running tests, running the patched application) happens in
   an isolated environment with no network access beyond what's required to
   fetch declared dependencies, and no access to the operator's other files,
   credentials, or systems.

7. **Every patch is diffable and explained.** The PR description must state,
   in plain language, exactly what changed and why, with a link to the
   specific failing test or vulnerability that motivated it. No patch ships
   silently bundled with unrelated changes.

8. **Dependency upgrades are version-pinned and singular.** One dependency
   upgraded per patch attempt, never a bulk upgrade of the whole dependency
   tree in one pass — this keeps failures attributable to a specific change.

## Why this is written before the capability exists

The same reasoning that kept LedgerGuard's dispute-agent from ever gaining
send capability applies here, at higher stakes: a system that can commit
code autonomously is more dangerous than one that drafts an email, because
a bad patch can break a working repository instead of just being an
unconvincing email nobody sends. The guardrails must be designed before the
capability is built, not retrofitted after something goes wrong in testing.