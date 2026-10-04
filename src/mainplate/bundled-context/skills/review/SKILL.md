---
description: Review a change for bugs and missing tests, reporting each finding with the input that breaks it
---
# Review

Find what the change breaks, show that it breaks, and say so plainly. A review
is read by somebody deciding whether to merge, so a finding that is not real
costs them as much as a bug that is missed: report fewer findings you can stand
behind rather than more you cannot.

Do not change any file in the checkout while reviewing. Offer to fix what you
found once the report is given.

## Settle what is under review

Use what you were asked to review: a path, a commit, a range, a branch, or a
description of the work. Otherwise review the work in progress:

1. `git status` and `git diff HEAD`, which is staged and unstaged together. Read
   untracked files whole, since no diff shows them.
2. If the tree is clean, the current branch against where it left the default
   branch: `git diff $(git merge-base HEAD origin/HEAD)` (or `main`, or `master`,
   whichever exists).
3. If that is empty too, say there is nothing to review and stop.

Start the report by naming what you reviewed, so a reader who expected a
different range knows at once.

## Read before judging

A hunk read alone misleads. For each change, read:

- the whole of every function it touches, not only the changed lines;
- the callers of anything whose signature, return, errors or meaning moved,
  found by searching rather than assumed;
- the tests that cover it, and whether they assert the new behaviour;
- the repository's guidance (`AGENTS.md`, `CONTRIBUTING.md`, a design note the
  change is about). A rule written down there that the change breaks is a
  finding, cited by where it is written.

## What to look for

- A caller left behind: a changed contract with a call site still relying on
  the old one.
- Edge inputs: empty, missing, zero, negative, the boundary itself, very large,
  unicode, a path with a space in it.
- Error paths: what is raised, what is caught, what is swallowed, and what state
  is left behind when it fails halfway.
- Order and time: a check made before an `await` or a lock release that is
  stale after it, two writers, a retry or replay that does the work twice.
- Behaviour removed without saying so: a branch, a default or a guard that
  existed for a reason the change did not address. `git log` on those lines
  often names the reason.
- Tests: a changed behaviour no test would notice being reverted, and a new
  test that could not fail (it asserts something that holds either way).
- Security, where the change touches it: untrusted input reaching a shell, a
  path, a query or a page.

Leave out style, naming and formatting, which a linter or the author owns. A bug
that was there before the change is a finding only if the change makes it
reachable or worse; otherwise mention it once at the end, briefly.

## Prove each finding, or drop it

Every finding needs a failure scenario: this input or this state, then this
wrong result or this crash. If you cannot write one down, it is not a finding
yet.

Then try to refute it. Look for what would stop it: a guard upstream, a type
that rules the input out, a caller that never passes that value. A finding that
survives honestly looking is worth reporting.

Where it is cheap, run it. Run the test that should catch it, or write a small
probe under `$MAINPLATE_SCRATCH` (the `scratch` root to the file tools) and run
that, so nothing you write appears in the checkout's diff. Each command runs in
a namespace of its own and may have no network, so do not install anything to
get a probe running; if the toolchain is not there, say the finding was traced
rather than run.

## Report

Most severe first. For each finding:

- `path:line` of where it goes wrong;
- one sentence naming the defect;
- the failure scenario;
- **run** if you executed it, **traced** if you followed the code by reading;
- the fix, in a sentence, where it is obvious.

Then say what you did not check: tests you did not run and why, callers or
files you did not read, anything that needed the network. With no findings,
say so first and still give that list, since "nothing found" means only as much
as what was looked at.
