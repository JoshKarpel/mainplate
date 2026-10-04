---
description: Write a pull request title and description for the work in this session
---
# Describe this work as a pull request

Write a title and a description for a pull request carrying the work in this
session. Check rather than recall: the conversation says why, but the
repository says what, and what you remember changing is the part most likely to
be wrong.

1. Find the change: `git log` and `git diff` from where this branch left the
   default branch (`git merge-base HEAD origin/HEAD`), plus anything
   uncommitted, which you should name as not yet part of it.
2. Find the repository's own shape for one: a pull request template under
   `.github/`, `docs/` or the root, and the guidance on how changes are
   described. Follow it where there is one.
3. Write:
   - **A title** in the imperative, saying what the change does ("Add X",
     "Fix Y when Z"), short enough to read in a list.
   - **What changed and why**, leading with the why, for a reviewer who has not
     seen this conversation. Name the decisions a reviewer would otherwise ask
     about, and what each one costs.
   - **How it was checked**: what you ran and what it showed. Say plainly what
     was not run.
   - **What it leaves**: known gaps, follow-ups, anything deliberately out of
     scope.

Describe the change as it stands, not the path taken to it: no "first I tried",
and no narration of the conversation. Do not push, open the pull request, or
change any file; give the title and description in your reply, each in a fenced
block so they can be copied whole.
