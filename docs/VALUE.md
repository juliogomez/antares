# Why a 1B security model is interesting

## The thesis

Almost every agentic security tool built in the last two years works the same way: point a
large frontier model at a repository, let it read whatever it wants, pay per token, and hope
the bill and the false-positive rate both stay tolerable. That approach works, and it does
not scale — not economically, and not organisationally, because a great deal of the code
people most want scanned is code they are least willing to send to a third party.

Antares-1B is an argument that a narrow, small, open-weight model can do a specific part of
that job. Not "find and explain and fix the vulnerability", but the much narrower
**localisation** step: given a weakness class, which files should a human or a more
expensive model look at? That framing has three consequences worth taking seriously.

**It fits on hardware you already own.** Everything in this repository runs on a MacBook.
No GPU, no cloud, no per-token cost, no code leaving the machine. For a regulated
environment or an air-gapped one, this is the difference between a tool that can be
deployed and one that cannot.

**It is a filter, not an oracle.** A localiser that reliably narrows 400 files to 15 is
valuable even at modest accuracy, because the expensive stage that follows it — a frontier
model, or a human — now costs a fraction of what a full sweep would. The economics of a
funnel do not require the first stage to be right; they require it to be cheap and to have
good recall.

**It is falsifiable.** Cisco published the model, the benchmark, and the dataset. You can
check the claims. That is rarer than it should be, and this repository is an exercise in
taking them up on it.

## What it is not for

Being clear about this matters more than the upside, because the failure modes are the
expensive part.

**It is not a detector.** Antares answers "where might a CWE-22 be?", having been *told*
that CWE-22 is what to look for. It does not answer "is this repository secure?" Feed it a
class that is not present and, as our Phase B results show, it will very often name a file
anyway.

**It does not know when to stop.** This is the headline negative finding of this
proof of concept. On patched code — the same repository after the fix landed, where the
correct answer is "nothing here" — Antares keeps submitting, frequently the exact same file
it named before the patch. Any pipeline built on it must assume the output contains
confident false positives and must place a verification stage downstream. Treating its
output as a finding rather than a hypothesis will generate noise that destroys trust in the
whole system faster than any missed bug.

**It is not a replacement for static analysis.** Traditional SAST is sound in ways a
language model is not: deterministic, exhaustive within its rules, explainable. The
interesting configuration is complementary — a fast semantic filter in front of tools that
are precise but do not understand intent.

## Where this fits in a governed pipeline

The [Foundry Security Spec](https://github.com/juliogomez/foundry-poc-demo) describes an
agentic security evaluation system as a set of roles — Orchestrator, Indexer, Detector,
Triager, Reporter — with explicit invariants between them. Antares maps cleanly onto a role
that sits *upstream* of the Detector, which we call the **Localizer**: instead of a human
hand-writing `include_globs` to tell the pipeline where to look, a cheap local model reads
the repository and proposes the scope, conditioned on the CWE.

The value proposition is measurable and we have pre-registered how we intend to measure it:
tokens saved at the expensive Detector stage, against findings retained relative to an
honest wide baseline that scans everything. Both arms matter. A scoping step that saves 90%
of the tokens and loses 40% of the findings is not a win, and the only way to know is to run
the wide baseline you are trying to avoid. See [INTEGRATION.md](INTEGRATION.md).

That work is deferred: it requires paid frontier-model calls for the Detector arms, and none
have been made. The plumbing in this repository is validated against a mock backend only.

## The honest summary

A 1B model that runs on a laptop and points at the right file a meaningful fraction of the
time is a genuinely useful component, and Antares demonstrates that specialised
post-training buys a great deal at small scale. It is also over-eager in a way that matters,
and the benchmark it is measured on has a Phase B metric that rewards silence indiscriminately
and a sandbox validator that discards a quarter of every model's exploration budget.

All three of those statements are supported by evidence in this repository. That combination
— useful, flawed, and checkable — is the reason it was worth building.
