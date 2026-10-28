# What happened when we checked eight real READMEs

Date: 2026-10-28. Tool: ColdStart v0.1.0 (initial public release).

We inspected **eight pinned public repositories**. The final scanner returned **12 references found, 0 broken references, and 109 unverified report items**. We did not confirm any upstream documentation defect, execute the projects' setup commands, or obtain external adoption. The useful result was finding and fixing gaps in ColdStart itself, then publishing the inputs and outputs for others to reproduce.

These are **report items**, not complete commands, unique files, or a coverage percentage. A multiline command can produce several items; a document with no supported examples produces one sentinel item. The denominator does not include every command in a README.

## Sample and results

| Repository | Inspected README / commit | Found | Broken | Unverified | Context |
| --- | --- | ---: | ---: | ---: | --- |
| sindresorhus/np | [readme.md · 5ca53e9a](https://github.com/sindresorhus/np/blob/5ca53e9a04158b2fa544fb9523ecb7e6b2db7ddc/readme.md) | 0 | 0 | 22 | CLI and terminal-output examples mostly outside scope |
| tj/commander.js | [Readme.md · ba6d13dd](https://github.com/tj/commander.js/blob/ba6d13ddb4243e5913367734f8c159089ffe7834/Readme.md) | 0 | 0 | 47 | Many tutorial CLI examples; missing tutorial files are not proof of bugs |
| expressjs/express | [Readme.md · 1c6f1b0f](https://github.com/expressjs/express/blob/1c6f1b0fe4000977b9a7b4f34097e4d527dd9824/Readme.md) | 1 | 0 | 11 | The repository declares its documented test script |
| actualbudget/actual-server | [README.md · 7bd499c5](https://github.com/actualbudget/actual-server/blob/7bd499c5be758c47958e351fa62faf91e0bc2b80/README.md) | 0 | 0 | 1 | Archived historical sample; no supported examples, so this is a sentinel |
| Textualize/rich | [README.md · 9d8f9a37](https://github.com/Textualize/rich/blob/9d8f9a372cc5916fd4781fec207ced7ddac2f08f/README.md) | 1 | 0 | 1 | Local package entry point found after adding module support |
| openai/whisper | [README.md · 86098128](https://github.com/openai/whisper/blob/86098128c0b4f24f0e2aa2994de830614b474227/README.md) | 0 | 0 | 11 | Indented install examples are not parsed; fenced commands remain unverified |
| karpathy/nanoGPT | [README.md · 3adf61e1](https://github.com/karpathy/nanoGPT/blob/3adf61e154c3fe3fca428ad6bc3818b27a3b8291/README.md) | 8 | 0 | 10 | Deprecated historical sample; eight references point to four distinct Python files |
| karpathy/nanochat | [README.md · 92d63d4e](https://github.com/karpathy/nanochat/blob/92d63d4e8bb4df75c3b71618f31ddde2378b2bcd/README.md) | 2 | 0 | 6 | Local shell script and Python module entry points found |

The selection began with three established Node tools/frameworks and three Python tools. We retained the archived/deprecated samples after discovering their status and added nanochat as nanoGPT's successor. We did not discard zero-match results. This is a small convenience sample, not representative research, a random sample, or a claim about the projects' current HEAD after this date.

Two additional contributor documents (Commander and Rich) returned no supported examples in the prototype. They are not counted in the eight-README table.

## Evidence for the matches

- [Express's test command](https://github.com/expressjs/express/blob/1c6f1b0fe4000977b9a7b4f34097e4d527dd9824/Readme.md#L174) names the script declared in [package.json](https://github.com/expressjs/express/blob/1c6f1b0fe4000977b9a7b4f34097e4d527dd9824/package.json). We did not run those tests.
- [Rich's module command](https://github.com/Textualize/rich/blob/9d8f9a372cc5916fd4781fec207ced7ddac2f08f/README.md#L57) has a local [rich/__main__.py](https://github.com/Textualize/rich/blob/9d8f9a372cc5916fd4781fec207ced7ddac2f08f/rich/__main__.py). This is local file evidence, not proof of runtime import resolution.
- [nanochat's setup command](https://github.com/karpathy/nanochat/blob/92d63d4e8bb4df75c3b71618f31ddde2378b2bcd/README.md#L51) points to [runs/speedrun.sh](https://github.com/karpathy/nanochat/blob/92d63d4e8bb4df75c3b71618f31ddde2378b2bcd/runs/speedrun.sh); its [chat command](https://github.com/karpathy/nanochat/blob/92d63d4e8bb4df75c3b71618f31ddde2378b2bcd/README.md#L57) has a local [scripts/chat_cli.py](https://github.com/karpathy/nanochat/blob/92d63d4e8bb4df75c3b71618f31ddde2378b2bcd/scripts/chat_cli.py). We did not install, train, or run the model.
- nanoGPT's eight matched references point to `train.py`, `sample.py`, `data/shakespeare_char/prepare.py`, and `data/openwebtext/prepare.py`. Arguments, imported dependencies, training configuration, and generated outputs remain outside the claim.

## What changed in ColdStart

The first local prototype did not resolve `python -m ...` or literal shell-script commands. The Rich and nanochat examples motivated two small adapters. The same pinned inputs moved from 9 to 12 reference matches; this is a three-item improvement in this sample, not a general accuracy claim. Missing local modules remain unverified because dependencies or another import path may provide them.

The release audit also exposed two independent issues, reproduced with synthetic regression fixtures: hidden HTML comments could be scanned as visible instructions, and source control characters could reach terminal output. The release skips comment blocks while preserving source lines and escapes terminal control characters. These were ColdStart bugs, not defects found in the eight upstream projects.

## Important gaps

- ColdStart parses supported fenced blocks, not all Markdown. Whisper's indented installation examples are omitted, so zero broken references is especially uninformative there.
- Commander's tutorial snippets can describe files the reader is expected to create. Their absence at the repository root must not be advertised as a broken project.
- np includes terminal error output in a shell-tagged block. Several unverified items are output lines rather than commands.
- Unknown commands make subsequent steps in the same block unverified. The scanner intentionally does not infer installation side effects.
- Each block starts at the repository root. Prose-dependent directories, workspace behavior, remote services, and operating-system behavior are not modeled.
- Because the sample has no confirmed broken findings, it cannot establish defect-detection recall or a false-positive rate. No precision or accuracy percentage is reported.

## Reproduce

From a checkout of ColdStart, with Python 3.10+ and Git installed:

```sh
python3 benchmarks/run.py --fetch --checkouts .corpus --output .benchmark-output
```

`--fetch` explicitly permits downloading the eight public Git snapshots. The runner verifies the pinned commit, clean checkout, and README SHA-256 before scanning. It does not run upstream setup commands, install dependencies, or initialize submodules. It refuses to reset an existing mismatched or dirty checkout.

Without `--fetch`, the runner reads existing checkouts only. Inspect [the corpus manifest](../benchmarks/corpus.json), [the runner](../benchmarks/run.py), [final reports](study/reports/), and [prototype observations](study/baseline/). Final report metadata records hashes of ColdStart's source modules. The prototype observations are historical local results; the public runner reproduces the final release, not the earlier implementation.

The repository contents used for this study are MIT licensed. [Upstream copyright and license notices](study/THIRD_PARTY_NOTICES.md) accompany the excerpts and references.

## Wording that the evidence supports

> ColdStart's initial release was exercised against eight pinned open-source READMEs. The study exposed unsupported module and shell-script references, which led to new adapters and regression tests. We publish the commits, reports, and remaining blind spots. This is compatibility testing, not adoption, endorsement, or proof that the projects install successfully.
