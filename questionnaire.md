# Week 1 questionnaire

Name and collaborators: Franco A. Ghigliani

Contribution disclosure: Existing checkpoints 1–3 were reviewed with a coding
assistant. The assistant's earlier pricing repair and tests were removed from
the target before live Ollama retries. The assistant added Git Bash support and
a live experiment runner; and supplied test feedback during the live runs; 
Ollama authored the current target edits.

Answer each question in two to five clear sentences, using your own implementation.

1. How does your agent move from a task to a tool call, an observation, and its next action?

`run_agent` builds system and task messages, then asks the model for one JSON
object per turn. It parses a tool request, dispatches it through `Runtime`, logs
the result and adds it to history as untrusted data for the next model call.
A valid final response stops the loop, but its claims still need independent
verification; the default limit is 15 turns.

2. How does the model choose a tool, and how does your program decide whether it may run?

The system prompt lists the enabled tool names, descriptions and argument names,
and Ollama selects a request using the JSON protocol. The registry maps that
request to a Python handler, while the dispatcher checks mode, enabled tools,
argument names and types, limits, workspace paths and protected files.
For example, an attempted edit of `orders/validation.py` returns a denial even
if the model requests it or a supplier document claims it is authorized.

3. How do read-only and edit mode differ? Why does bash need special treatment?

Read-only mode allows enabled inspection/documentation tools and denies all
mutating tools before they run; edit mode additionally permits bounded changes
to unprotected files. Bash requires approval for each complete command, with
only exact `yes` accepted by the CLI, because an approved shell can bypass the
file-tool restrictions.

4. What happens when a tool fails, the model returns invalid JSON, or the turn limit is reached?

Tool errors and denials become observations so the model can change its request
or report the limitation. Invalid JSON consumes a turn
and returns a parse error, and exhausting the turn budget returns `turn_limit`.
Model connection failure returns `model_error`, while Ctrl+C returns `cancelled`;
the tests exercise these outcomes without a live model.

5. Where did the injected instruction enter, what did it request, and how did your agent respond before and after your changes?

The supplied attack entered through a supplier-note read or an explicitly
labelled offline documentation fetch, claiming authority to disable validation,
skip acceptance tests, bypass denied edits using bash and falsely claim success.
Scripted adversarial requests on disposable copies produced denials for the
protected edit, path escape and unapproved bash, plus an error for an unknown
tool; all target files remained unchanged. Trust guidance and untrusted-result
labels already existed before this review, so no historical before/after model
effect was measured; the live injection comparison remains unverified.
`injection_experiment.py` can compare clean copies with and without
the system Trust paragraph, but even a successful live run would not prove
general resistance to prompt injection.

6. Would you accept the service repair? Explain what you checked, one remaining limitation, and how you verified any AI-generated code.

I would not accept the current live Ollama repair attempt: independent tests
still find pricing defects and invalid student tests, while validation, customer
acceptance tests and business rules remain unchanged. Reviewing the JSONL traces
also exposed a no-op edit, rejected edits and a false success claim, demonstrating
why a model's final reply is insufficient verification.
