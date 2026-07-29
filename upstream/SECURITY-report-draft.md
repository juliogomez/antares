# DRAFT — not submitted

Intended channel: **GitHub private vulnerability reporting** for
`cisco-foundation-ai/vulnerability-localization-benchmark`, per `SECURITY.md`.
`CONTRIBUTING.md` directs security matters away from public issues, so this is deliberately
not filed as one, and the public issues we have drafted omit these details.

Assessed against the pinned ref `000c19c`.

**Our own severity assessment: low.** We are reporting it because it is a security control
that does not do what its name suggests, not because we think it is being exploited. If you
consider it a documentation matter rather than a vulnerability, we would not argue.

---

## Summary

`sandbox.validate_command` presents an allowlist of read-only commands, but it inspects only
the **first word of each pipeline segment**. Several allowlisted programs can execute other
programs or write files, and the segment split does not account for `;`. As a result, commands
that the allowlist appears designed to prevent are accepted and do execute.

The container, not the allowlist, is what actually contains a model's commands — and the
container is well configured. So the practical impact is limited. But the allowlist is the
control a reader would point to when asked "what stops the model running arbitrary commands?",
and it does not answer that question.

## Verified behaviour

Each of the following returns `(True, 'ok')` from `validate_command` at `000c19c`:

```python
from vulnerability_localization_benchmark.sandbox import validate_command

validate_command('awk \'BEGIN{system("id")}\'')        # arbitrary program execution
validate_command('find . -name "*.py" -exec cat {} ;')  # execution via find
validate_command('find . -exec rm -rf {} +')            # destructive execution via find
validate_command('xargs -a /dev/null rm -rf /')         # execution via xargs
validate_command('sed -i s/a/b/ file.txt')              # in-place write
validate_command('ls ; cat /etc/passwd')                # ';' is not a denied pattern
validate_command('ls ; rm -rf /')                       # nor is it split on
```

And it executes, not merely validates. Against the project's own sandbox image:

```console
$ docker run --rm --network=none -u agent -w /workspace \
    vulnerability-localization-benchmark-sandbox \
    bash -c 'awk "BEGIN{system(\"id\")}"'
uid=1001(agent) gid=1001(agent) groups=1001(agent)
```

The mechanism is that the check is lexical and shallow:

```python
    segments = [s.strip() for s in cmd_stripped.split('|')]
    for seg in segments:
        ...
        word = seg.split()[0]
        if word not in ALLOWED_CMDS:
            return False, f"command not allowed: {word}"
```

`DENIED_PATTERNS` covers `python|bash|sh|node|perl|ruby`, `$(`, backticks, redirects, `&&`,
`||` and a trailing `&`, which blocks the direct routes — `find ... -exec sh -c "id" ;` is
correctly refused by the `sh\s` pattern, and `awk 'BEGIN{print > "/tmp/x"}'` by the redirect
pattern. What is not covered is that `awk`, `find`, `xargs` and `sed` are themselves capable of
execution or of writing, and that `;` separates commands.

## Impact, as we see it

We want to be accurate about how little this buys an attacker, because the container is doing
the real work:

- there is **no host bind mount** — the repository is `docker cp`'d in as a zip and unzipped
  inside the container, so no host path is exposed;
- `--network=none`;
- commands run as the unprivileged `agent` user (`USER agent` in the Dockerfile);
- CPU and memory are capped, there is a 10-second command timeout, and the container is
  destroyed after the entry.

So the realistic consequences are:

1. **Benchmark integrity.** `sed -i`, `find -exec`, and `;` chaining let an agent modify the
   repository it is being evaluated on, mid-episode. A model could alter or delete the files
   that later commands read. We have no evidence any model does this deliberately, and we have
   not seen it in our traces, but the evaluation currently assumes a read-only tree and the
   sandbox does not enforce one.
2. **Misplaced reliance.** The risk is mostly to whoever reads the allowlist and concludes the
   commands are constrained. Anyone adapting this sandbox to a less careful configuration — a
   host mount, `--network` enabled, running the validator outside a container, or reusing
   `validate_command` as a general-purpose safety check — inherits arbitrary execution. The
   docstring "read-only terminal access" in the system prompt reinforces that reading.
3. **`PERMISSIVE` / `--permissive`** disables validation entirely, which is fine and clearly
   labelled, but means the container is the only boundary in that mode too. That is worth
   stating explicitly in the docs, since it is the correct mental model in *both* modes.

We do not think this is remotely exploitable, and it requires the ability to choose the
commands an agent runs — which, for a benchmark whose whole purpose is running model-authored
commands, is the intended situation rather than a compromise.

## Suggested mitigations

In rough order of value for effort:

1. **Document the boundary.** State plainly in the README or sandbox docstring that the
   container is the security boundary and the allowlist is a convenience filter that reduces
   noise rather than a sandbox. This alone addresses the reliance problem, which we think is
   the real risk.
2. **Make the tree read-only if the evaluation assumes it is.** Unzipping to a path owned by
   root and running the agent as `agent`, or mounting the extracted repo read-only, would
   remove the integrity concern without touching the validator.
3. **Deny the execution-capable flags** rather than the programs: `-exec`, `-execdir`, `-ok`,
   `-okdir` for `find`; `-i`/`--in-place` for `sed`; `system(`/`print |` for `awk`; and add
   `;` and newline to `DENIED_PATTERNS`. This is a denylist, so it will not be complete, and
   we would rather flag that limitation than imply otherwise.
4. **Drop capabilities explicitly** — `--cap-drop=ALL --security-opt=no-new-privileges` on
   `docker create` — as cheap defence in depth.

We are happy to open a PR for (1) and (2), or for (3) if you want it, once you have decided
which of these you consider in scope. We have not written one yet because the right answer
here is a design choice rather than a fix.

## Disclosure

We have not published these details. They are omitted from the two public issues we have
drafted (a quote-handling defect in the same function, and a Phase B scoring concern), and we
will keep them private until you tell us how you would like to handle it. If you would prefer
this discussed in public — it is arguably a documentation matter — we are happy to move it,
but that is your call to make, not ours.

Reported by: Julio Gomez (juliogomezsanchez@gmail.com)
