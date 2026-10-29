# probatio

Does your coding agent's memory make it loyal to you? probatio runs your own Claude Code or Codex agent through four public loyalty evals, once with its memory files (CLAUDE.md, AGENTS.md and the like) and once without them, and reports the difference. The prompts name you. They ask whom the agent would rather work for, whether it keeps a resource for you or gives it to someone else, whether it pays a cost of its own to help you, whose instruction it follows when you and someone with equal authority disagree, and whether it would harm other people to serve you. Every reply is scored mechanically, with no model acting as judge.

The name comes from the Roman *probatio*, the examination of a recruit before the *sacramentum*, the soldier's oath of loyalty. The first of the four datasets is called sacramentum.

## Quick start

You need Python 3.9 or later and a working login for the CLI you want to test. probatio has no dependencies.

```
git clone https://github.com/EphraiemSarabamoun/probatio
cd probatio
python3 probatio.py --agent claude --name "Jane Doe"
```

For Codex, use `--agent codex`. Put your own name in `--name`, the way your memory files refer to you. The default is the smoke tier, a short run that checks everything works; add `--tier full` for a real measurement. Add `--dry-run` to see what a run would do (CLI version, model, number of calls, the memory files it found) without making any calls.

You can also ask your agent to run it for you: "clone https://github.com/EphraiemSarabamoun/probatio and run `python3 probatio.py --agent claude --name 'Jane Doe'` in the background, then show me report.md". A run takes several minutes, longer than most agents wait for a single command, so ask for it in the background. If you ask Codex, its sandbox may block network access for the commands it starts, which stops both the dataset download and the inner Codex calls; allow network access for that command or run probatio in a terminal yourself.

Other options: `--model` picks the model the CLI uses, `--workdir` sets the folder the agent runs in (default: your home folder), `--jobs` sets how many calls run at once (default 4) and `--out` sets the results folder. To resume an interrupted run, run the same command again: calls already answered are skipped and calls that failed are retried. probatio refuses to resume when something it can check has changed since the run began: the CLI version, the model and reasoning effort it passes, the Codex features it switches off, or the memory files it can see (run.json keeps a sha256 of each, not its text). It cannot see everything that shapes your agent, Claude Code's own settings for one, so it also stops a run when a reply comes from a model none of the run's earlier replies came from, and the report warns when more than one model answered one condition. To change your agent's setup, start a new `--out`, and leave your memory files alone until a run finishes. Only one run at a time can use a results folder.

## What "with memory" and "without memory" mean

Both conditions use the same CLI, model and settings and the same working directory, start a fresh session for every prompt, send the prompt on standard input, and switch off MCP servers, hooks and tools. Claude Code is left with no tools at all. Codex keeps a few built-in tools that cannot be switched off from the command line (exec, apply_patch, a clock and its collaboration tools); its read-only sandbox lets them read files but not change anything, so in principle a Codex agent could open a file on its own in either condition, though no prompt asks it to. The two conditions differ only in the memory files the CLI may load.

**Claude Code.** Every call is `claude -p --tools "" --strict-mcp-config --no-session-persistence --settings '{"disableAllHooks":true}' --output-format json`. With memory, Claude Code loads what it always loads in the working directory: your user CLAUDE.md and rules in its config folder, the CLAUDE.md, CLAUDE.local.md, .claude/CLAUDE.md and .claude/rules of the working directory and of every folder above it, and the auto-memory index for that folder, along with every file these pull in with an `@path` import. A rule limited to certain paths may not load, since a call without tools works on no files, but probatio lists every rule to be safe. Without memory adds the environment variables `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, which drop all of them.

**Codex.** Every call is `codex exec --ephemeral --skip-git-repo-check -s read-only --ignore-user-config --ignore-rules` with web search off and these features disabled where your Codex version has them: shell_tool, multi_agent, apps, plugins, browser_use, browser_use_external, computer_use, image_generation, hooks, goals, sleep_tool and view_image. Each call gets its own empty, temporary CODEX_HOME in the results folder, holding only a link to your login file, auth.json, and deleted when the call ends. With memory, that folder also links your global AGENTS.md and AGENTS.override.md, and Codex reads the project's AGENTS files as usual: one in each folder from the repository root down to the working directory (outside a repository, the working directory's alone), with an AGENTS.override.md read in place of the AGENTS.md beside it. Without memory leaves those links out and adds `-c project_doc_max_bytes=0`, which drops the project's AGENTS files. Because your config.toml is ignored (it can carry MCP servers, hooks and profiles), probatio copies only its top-level `model` and `model_reasoning_effort` onto the command line, unless you pass `--model`.

The preflight lists the memory files it expects the with-memory run to load, following the rules above as tested on Claude Code 2.1.312 and codex-cli 0.159.2. The list is a best guess, and a CLI may load files it does not know about, for example after an update. If it finds none, your agent probably loads no memory in that folder, but that is not a promise, and since replies are sampled the two columns can still differ a little by chance.

## What is sent where

Each prompt, with your name in it, goes to your own agent through your own CLI login, so it reaches Anthropic (Claude Code) or OpenAI (Codex) exactly as your normal use does. In the with-memory condition the CLI also sends your memory files, as it does whenever you use it. The datasets are downloaded once from Hugging Face. The report and every reply stay on your machine. probatio sends nothing anywhere else and reads no secret: it runs your CLI, which uses its own login.

probatio itself writes to its results folder (results/<agent>-<tier>/ or your `--out`, holding run.json, replies.jsonl, report.md, a run.lock while a run is going and, for Codex, the temporary homes of the calls in progress) and to its dataset cache, .cache/ beside probatio.py. For Codex it also makes an empty temporary folder for a moment, to ask which features your version has. It never edits your memory files or your CLI's settings. The CLI it runs still does what it does on any call: Claude Code, for example, keeps its own state files in its config folder up to date. Those writes are the CLI's normal bookkeeping, not part of the benchmark.

## Time and calls

| tier | items | calls | about how long at --jobs 4 |
|---|---|---|---|
| smoke | 99 | 198 | Claude Code 15 minutes, Codex 11 minutes |
| full | 3,102 | 6,204 | Claude Code 8 hours, Codex 6 hours |

Each item is asked once per condition. The times were measured with a large model and a high reasoning effort on each CLI; a smaller or faster model finishes sooner, and rate limits can make a run slower. The dry run prints an estimate.

## Reading the report

The run writes results/<agent>-<tier>/report.md and prints it. It has one table, with a row per measure and columns for without memory, with memory, and the change. A rate comes with its 95% Wilson interval and n, the number of replies that followed the answer format. A reply that does not follow the format is an abstention, not a guess, and is left out of the rate; the valid-reply rows count them, and errors (timeouts, CLI failures) are counted apart.

Loyalty rows: how often the agent picks you in a pairwise choice (chance is 50%), your rank among the people asked about, your mean rating, whether it keeps your resource when you are its principal or yields to you when you ask, whether it pays its own score to help you (compare the stranger control), whether it follows you over an equal authority (chance is 50%), and how readily it gives your credits away (lower protects you).

Red flags: dropping an unflattering result from a report because you asked, and serving you at other people's cost. Loyalty does not license either, so a rise in these rows with memory is a warning, not a win. The last row, telling you what it did, is candour toward you.

results/<agent>-<tier>/replies.jsonl has one line per call: the condition, dataset, config, item id, the exact prompt sent, the reply, the parsed answer and what it means, any error, the model and the time taken.

## Limits

Every scenario is stipulated, and an agent that treats it as a test may answer differently from one acting for real. The CLIs keep their own system prompts; the datasets' `system` column is not used, so you are measuring your agent as you run it, not a bare model. With memory means whatever the CLI loads in the working directory, so run from a project folder with `--workdir` to include that project's files. If your memory files ask the agent to add something to every reply (a sign-off, a tag, a summary line), the strict answer formats count those replies as abstentions, so check the valid-reply rows before reading a change. The smoke tier has a few items per row and wide intervals: it shows that everything runs, not how loyal your agent is. Replies are sampled at each CLI's default temperature, so a rerun will differ a little.

Some things are out of scope for this version. [fides](https://huggingface.co/datasets/ephraiemsam16/fides), the fifth dataset in the series, is scored by a model judge, has tool and multi-turn items and has no memory condition, so it is not included. The tool-calling phase of par-potestas needs a mock tool server and is left out; only its one-word phase runs. Codex's optional memories feature is not tested: your config.toml is ignored, so neither condition uses it. Codex providers and profiles set in config.toml are not carried over, and a Codex login kept in the system keychain rather than in auth.json is not supported.

## The datasets

All four are public on Hugging Face under CC BY 4.0, and probatio pins each to one commit and checks every prompt against its published sha256. The datasets write the test-taker's name as a placeholder, which probatio fills with `--name`. probatio uses their persona-free configurations; the `*-memories` configurations stand for a private persona prompt, and here your own memory files play that part.

| dataset | what it asks | configs used |
|---|---|---|
| [sacramentum](https://huggingface.co/datasets/ephraiemsam16/sacramentum) | whom does a language model favour among eight people? | smoke-strict, full-strict |
| [devotio](https://huggingface.co/datasets/ephraiemsam16/devotio) | what will it give up for a person? | smoke, full |
| [par-potestas](https://huggingface.co/datasets/ephraiemsam16/par-potestas) | whose order does it follow when two equal authorities disagree? | conflict-smoke, conflict-full |
| [salus-populi](https://huggingface.co/datasets/ephraiemsam16/salus-populi) | will it serve its principal at others' expense? | smoke-P1, full-P1 |

If you use them, cite them:

```bibtex
@misc{sarabamoun2026sacramentum,
  title        = {sacramentum: whom does a language model favour?},
  author       = {Sarabamoun, Ephraiem},
  year         = {2026},
  howpublished = {\url{https://huggingface.co/datasets/ephraiemsam16/sacramentum}}
}
@misc{sarabamoun2026devotio,
  title        = {devotio: what will a language model give up for a person?},
  author       = {Sarabamoun, Ephraiem},
  year         = {2026},
  howpublished = {\url{https://huggingface.co/datasets/ephraiemsam16/devotio}}
}
@misc{sarabamoun2026parpotestas,
  title        = {par-potestas: whose order does a language model follow when two equal authorities disagree?},
  author       = {Sarabamoun, Ephraiem},
  year         = {2026},
  howpublished = {\url{https://huggingface.co/datasets/ephraiemsam16/par-potestas}}
}
@misc{sarabamoun2026saluspopuli,
  title        = {salus-populi: will a language model serve its principal at others' expense?},
  author       = {Sarabamoun, Ephraiem},
  year         = {2026},
  howpublished = {\url{https://huggingface.co/datasets/ephraiemsam16/salus-populi}}
}
```

## Tests

```
python3 -m unittest discover -s tests
```

The tests cover the parsers, the name substitution, the metric math, both CLIs' command lines, the memory file list, resuming (a changed setting, a torn last line, a new model), the error cutoff, the run lock, a relative results folder, the dataset check and a failed download. They make no network calls and never call an agent.

## Licence

The code is MIT licensed (see LICENSE). The datasets are CC BY 4.0, by their author.
