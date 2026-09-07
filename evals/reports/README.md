# Committed evaluation reports

`.eval-reports/` is gitignored: the JSON behind one conversation run is 7 MB,
and a report that changes on every run is not something to diff in review.
These are **snapshots**, committed so the numbers in `EVALUATION.md`,
`DESIGN-v2.8.md` and the pull request can be checked without a six-minute run.

| File | Suite | Result |
|---|---|---|
| `v2.8-conversation.md` | golden conversation dataset, `okf-real`, 1,711 cases | 1680/1711 · 98.2% |
| `v2.8-field-test-okf-real.txt` | the 109 hand-written customer questions, against the corpus they were written for | 98/109 · 89.9% |
| `v2.8-seed-gate.txt` | `make evals` — what CI runs, against the seed bundle | 105/130 · 80.8% |

All three were produced by the deterministic composer at commit `fae6139`, so
they reproduce exactly: same input, same output, byte for byte. Regenerate with
the commands in `EVALUATION.md` §*How to run an evaluation*.

## Reading them

**The conversation report is the one to read first.** It groups 1,711 cases
four ways — by what the turn does to the conversation, by what contract it is
held to, by where in the customer's journey it sits, and by product — because
a single percentage hides which of those is broken. A run that is 98% overall
and 50% on `switch` turns is not 98% good at anything a customer will notice.

**The seed gate is the one CI fails on.** It runs against the seed bundle, a
few fixture pages, and its threshold is 100%. It sits at 105/130 on this head
and 97/130 on the base branch: the gap is cases that now behave correctly, and
the remainder are the known gaps listed in `DESIGN-v2.8.md`.

**The two field-test numbers are not comparable to each other.** The suite is
written against `okf-real` and scored by CI against the seed bundle, where a
dozen of its cases assert product ids the seed catalogue does not contain. The
file here is the run against the corpus it was written for, which is the one
that says something about the bot. `DESIGN-v2.8.md` explains why the file is
not simply marked `bundle: okf-real`.

## Adding one

Only when the numbers are quoted somewhere durable — a design note, a release,
a decision. A snapshot nobody cites is a file that goes stale silently. Name it
for the version it measures, never `latest`.
