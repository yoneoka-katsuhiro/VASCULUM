# v0.1.17 Validation Record

Date: 2026-11-02. A software-release check, not a positional-accuracy study.

## Offline checks

`bash check_release.sh`: shell syntax, Python compilation, 116 unittest cases
(collector and curator), CLI entry points and a credential-pattern scan. New
regression tests (`tests/test_coarse_coordinates.py`, `tests/test_claude_provider.py`):

- 23.51667, 120.8 and 23.45, 120.91667 get arc-minute rounding (< 1.5 km);
  ambiguous pairs such as 23.6, 120.95 keep the conservative decimal step.
- Minute-precision points and points shared across collecting dates are
  `coarse`; documented GPS points and precise unshared points stay `trusted`.
- Two MNHN-style records sharing 23.51667, 120.8 with photographs are both sent
  to label reading and locality research and receive the researched point.
- A coarse point without a photograph is kept with low confidence.
- A precise point without a datum gets a 2000 m radius (1000 m positional +
  1000 m datum) and medium confidence; a printed arc-second label coordinate is
  high confidence.
- Non-terminal output contains one header and one line per record, with no
  stage lines.
- The Claude provider builds a `claude -p` call with the JSON schema, the Read
  tool and `--add-dir` for the photo folder, and parses structured output and
  token usage (fake CLI; no network).

Earlier expectations changed deliberately: the E01539315 regression now expects
2000 m / medium (was 6000 m / low) and the printed arc-second label case expects
high confidence (was low).

## Live run (Codex CLI, gpt-6.1-sol, medium)

First 10 records of the *Haplopteris mediosora* collector output, 5 min 25 s,
816k tokens (807k input, 9k output):

| Specimen | v0.1.16 | v0.1.17 |
| --- | --- | --- |
| E01539315 | label coords, 6.0 km, low | label coords, 2.0 km, high |
| P02439185, P01189407, P02437991, P01187634 | 23.51667, 120.8 kept, ~10 km | re-estimated from labels (Alishan), 3.0–5.5 km |
| P01189854 | 23.6, 120.95 kept, 10.6 km | re-estimated (JunDa forest road, Nantou), 2.5 km |
| P01185529 | 23.45, 120.91667 kept, 6.0 km | re-estimated (Zizhong), 11 km |
| P01187635 | 23.51667, 120.8 kept | kept (no photograph downloaded), low |
| BOTBC122149, BOTBC122151 | excluded (`miss_`) | excluded (`miss_`) |

No failures. The Claude provider was not run live in this check because the
Claude Code CLI on the test machine was not signed in; its argument handling is
covered by the offline test only.

## Limits

Locality research still costs about 90k–220k tokens per specimen with Codex.
No independent ground-truth comparison was made, so no accuracy claim follows
from these runs.
