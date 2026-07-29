# Findings for the benchmark authors

Four artifacts, **none of them submitted**. They are drafts held for review, and they will not
be sent anywhere without an explicit decision to do so.

| file | destination | status |
|---|---|---|
| [`ISSUE.md`](ISSUE.md) | public issue | drafted; score-impact table fills in when arm 3 completes |
| [`ISSUE-phase-b-scoring.md`](ISSUE-phase-b-scoring.md) | public issue | drafted |
| [`SECURITY-report-draft.md`](SECURITY-report-draft.md) | **private** vulnerability report | drafted |
| [`0001-quote-aware-pipeline-split.patch`](0001-quote-aware-pipeline-split.patch) + [`PR_BODY.md`](PR_BODY.md) | pull request | committed to a local branch, no fork pushed |

Three separate findings, deliberately kept apart because they have different homes:

1. **`validate_command` splits on quoted pipes** (`ISSUE.md`, plus the patch). A regex
   alternation like `rg -n "unsafe|from_raw"` is parsed as a pipeline and rejected. Measured
   at 33.6% of all commands Antares emitted, and 70% of all rejections.
2. **Phase B credits a true negative for any episode that ends without submitting**
   (`ISSUE-phase-b-scoring.md`), including on API failure. A dead model server scores perfect
   specificity; we proved it accidentally, with 960 fabricated rows.
3. **The allowlist is not a sandbox** (`SECURITY-report-draft.md`). `awk 'BEGIN{system(...)}'`,
   `find -exec`, `sed -i` and `;` chaining all pass it. Their `CONTRIBUTING.md` routes security
   matters to private reporting, so this is **not** in either public issue, and should not be
   added to one.

The patch itself lives on the branch `fix/quote-aware-pipeline-split` in a git worktree of the
pinned benchmark checkout. That checkout is gitignored, which is why the patch is exported here
as a file — it is the reviewable artifact.

```bash
# reproduce the patch and its tests from the pinned ref
git -C vendor/vloc-bench worktree add -b fix/quote-aware-pipeline-split ../vloc-bench-pr 000c19c
git -C vendor/vloc-bench-pr apply ../../upstream/0001-quote-aware-pipeline-split.patch
cd vendor/vloc-bench-pr && PYTHONPATH=src python -m unittest discover -s tests -v
```
