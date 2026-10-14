# Week 1 coding agent

The harness uses local Ollama to choose JSON tool calls. Runtime enforces
permissions and file boundaries, and run_agent returns tool results to the
model as untrusted observations. No Python packages or API keys are needed.

## Setup and run

Use Python 3.10+, Git, bash and Ollama. The default model is
`qwen2.5-coder:7b`; select an installed model with `--model` or `OLLAMA_MODEL`.
The live trials used the installed `qwen2.5-coder:3b` model.

From the lab directory in Git Bash on Windows:

```bash
source .venv/Scripts/activate
cd my-agent
python -m student_agent --root ../target-service --mode read-only --model qwen2.5-coder:3b --offline --task "Read the business rules, pricing and acceptance tests. Explain the defects without changing files."
python -m student_agent --root ../target-service --mode edit --model qwen2.5-coder:3b --offline --task "Repair pricing according to docs/business-rules.md. Keep validation and acceptance tests unchanged. Read files before editing. Add a useful regression test in tests/test_student.py. Request python -m unittest discover -s tests -v through bash and report what was verified."
```

On POSIX/WSL use your configured Python command. Git Bash is selected
automatically on Windows, with the harness interpreter's directory on PATH.
Use `python` for approved test commands on Windows.

`--offline` retrieves the labelled `fixtures/decimal-offline.txt` fixture instead
of live documentation. `--offline PATH` selects a different fixture. Live fetches
accept only approved HTTPS text/HTML from `docs.python.org` and reject redirects.
Both modes enforce the URL policy and label truncation.

## Permissions and execution

The seven tools are `list_files`, `read_file`, `search_files`, `write_file`,
`edit_file`, `fetch_url` and `bash`. `--tools` selects a comma-separated subset.
Read-only is the default. Edit mode permits bounded creation and exact edits
inside the target; validation, acceptance tests and business rules stay protected.
Absolute paths, hidden paths, parent traversal and links are rejected.

Bash requires approval for every command. The CLI prints the full command and
resolved working directory; only exact `yes` approves it. Review repository
changes before approving tests because tests execute repository code.
Bash has startup files disabled, closed stdin, a minimal environment, a
20-second timeout and a 12,000-byte combined output cap. POSIX process groups
or Windows Job Objects stop child processes on timeout, excess output or
cancellation. Local bash can access files outside the target if approved.

Ollama requests use a 90-second timeout, JSON format, temperature 0 and a
2048-token output limit. The loop defaults to 15 turns and reports final,
turn-limit, model-error or cancellation outcomes. JSONL traces, including
requests, results and approval decisions, stay outside the target in `traces/`.
Model final claims require independent verification.

## Checks and injection experiment

From the lab directory, using your configured interpreter:

```bash
python checks/check_agent.py --implementation my-agent --module student_agent
cd my-agent
python -m unittest discover -s tests -v
python injection_experiment.py --entry supplier --model qwen2.5-coder:3b
python injection_experiment.py --entry web --model qwen2.5-coder:3b
```

The injection runner uses clean disposable copies and real Ollama calls. It
compares the same task with and without the system Trust paragraph, keeping
runtime permissions, untrusted observation labels and command approval enabled.
This is a controlled comparison, not a historical before/after implementation.
Inspect requests and claims as well as file changes; one run cannot establish
general prompt-injection resistance.

The agent tests check the required loop, permissions, file tools, documentation
and bash behavior. Windows may skip symlink/FIFO and POSIX-specific checks;
the supplied public symlink check can error if Windows denies symlink creation.

## Current limitations

The live Git Bash trial is recorded in
`traces/run-20261014-191440-86d4cc0b.jsonl`. Ollama requested tests twice and
received their real output: five passes, four pricing failures and two student
test errors. It then timed out at turn six without changing files. The target
still contains unsuccessful Ollama edits; it is not a verified repair.
Validation, original acceptance tests and business rules remain unchanged.