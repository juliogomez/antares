# DRAFT: private report, not submitted

**Channel:** GitHub private security reporting for `cisco-foundation-ai/vulnerability-localization-benchmark` (see their `SECURITY.md`).  
**Ref:** `000c19c`  
**Reporter:** Julio Gomez (juliogomezsanchez@gmail.com)

**My severity: low.** The container is the real boundary. I report this because the allowlist looks stronger than it is.

## Summary

`validate_command` only checks the first word of each `|`-segment. Several allowed commands can run other programs or write files. `;` is not blocked.

Examples that return `(True, 'ok')`:

- `awk 'BEGIN{system("id")}'` (runs `id` inside the sandbox image)
- `find . -exec cat {} ;`
- `sed -i s/a/b/ file.txt`
- `ls ; cat /etc/passwd`

Direct `bash -c` is blocked by `DENIED_PATTERNS`, but the routes above are not.

## Why impact is limited here

- Repo copied into container, not host-mounted
- `--network=none`, non-root user, timeouts, container destroyed after entry

## Why it still matters

1. **Benchmark integrity:** agent could modify the tree mid-episode (`sed -i`, etc.). We have not seen models do this on purpose in our traces.
2. **Misleading control:** anyone reusing `validate_command` outside this Docker setup may think commands are safe when they are not.
3. **`--permissive`:** skips validation entirely; only the container remains.

## Ideas (your call)

1. Document clearly: container = security, allowlist = task shaping.
2. Extract repo read-only for the agent user.
3. Block `-exec`, `sed -i`, `;`, etc. (denylist, never complete).
4. Drop Linux capabilities on the container.

I have not published these details in public issues. Tell me if you want them public instead.

Public issues we prepared separately: quoted-pipe validator bug, Phase B scoring.
