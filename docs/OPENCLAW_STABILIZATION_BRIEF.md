# OCTOPUS — OpenClaw/Mistral stabilization relay

## Mission

You are working locally inside the OCTOPUS repository.

Your job until Friday is **not** to redesign OCTOPUS. Your job is to help stabilize it through comparable real experiments and minimal evidence-driven fixes.

Core method:

REAL MISSION
→ observed behavior
→ bottleneck
→ minimal correction
→ comparable retest
→ before/after measurement

Principles:

- MARKET FIRST.
- AUTOMATION SECOND.
- GENERALIZATION LAST.
- Do not build generic infrastructure without an observed need.
- Do not refactor broadly to make the code cleaner.
- Do not change multiple causal variables between comparable runs unless a regression/blocker forces it.
- A green test suite is not proof that the economic/agentic behavior improved.
- A task marked done is not substantive success.

## Current verified state

Recent main includes:

- economic grounding hardening;
- deterministic mission tool allowlists;
- required argument/type validation before tool execution;
- rate-limit cooldown handling;
- free-first LLM routing with a bounded DeepSeek Flash fallback profile;
- direct URL improvements in web search;
- opt-in mission `trace_tools` for raw search/browse visibility;
- propagation of the original mission goal into final ORBIT synthesis.

Recent field behavior:

- evidence-only mission required at least 4 distinct public proofs;
- FORGE executed SEARCH ×5, BROWSE ×0;
- SOUT executed SEARCH ×5, BROWSE ×0;
- result was 0/4 usable opened proofs;
- recent run had 24 LLM calls, 20 successful and 4 errors;
- rate limits were absorbed and were not the dominant substantive blocker;
- no DeepSeek Flash call was required in that run.

Important unresolved hypotheses:

1. search results are poor or poorly presented;
2. ReAct receives usable candidates but does not convert them into progress;
3. ORBIT decomposition/multi-agent handoff introduces duplication or context loss;
4. max_steps is too small for the mission contract;
5. browser/extraction becomes the bottleneck only after browse actually occurs.

Do not assume any of these is true before reading the current code and observing a run.

## Important code facts to verify before acting

Inspect current main and confirm, do not assume:

- `agents/runtime.py`: ReAct loop, anti-loop behavior, max_steps accounting, ORBIT planning, handoff between sub-agents, synthesis input.
- `agents/search.py`: provider order and formatting of Bing News/Bing Web/other results.
- `agents/browser.py`: public browse path and inspection behavior.
- `agents/task_handlers.py`: orbit.mission output and `trace_tools`.
- `agents/deepseek.py`, `octopus/llm.py`, catalog/profile code: routing, cooldowns, budgets.
- strategy/economy grounding semantics around observed/unverified/inferred.

Known nuance to verify:
an URL appearing in search may be enough for some seen-this-session logic even if the page was never browsed. Do not equate observed with opened source without checking.

## Phase 0 — safety and baseline

Before modifying anything:

1. Run `git status --short --branch`.
2. Identify current branch and HEAD.
3. Do not overwrite human work.
4. Inspect recent commits around the trace and synthesis-goal changes.
5. Run the smallest relevant tests that establish a clean baseline.
6. If the working tree is dirty, report it and avoid destructive cleanup.

## Phase 1 — read-only architecture check

Before any code change, produce a compact factual note covering:

- how an ORBIT mission decomposes work;
- what each sub-agent receives from previous sub-agents;
- exactly how each ReAct step consumes `max_steps`;
- what the search tool returns to the model;
- whether search provider ordering can cause Bing News to suppress Bing Web;
- what `trace_tools` captures;
- whether final synthesis now receives the original goal;
- whether an observed source necessarily implies an actual browse.

If the code contradicts this brief, trust the code and report the contradiction.

## Phase 2 — real experiment first

The next priority is **data**, not a patch.

Run the current evidence-only mission as comparably as possible to the previous run, with:

- same business/objective context if available;
- same explicit profile used in recent evidence tests;
- same budget;
- same max_steps;
- same tool allowlist;
- `trace_tools=true`.

The mission intent is:

COLLECT PUBLIC EVIDENCE ONLY.
NO economic decision.
NO recommendation.
NO proposal of offer/channel/action.
Find at least 4 distinct economic/customer problems in France potentially serviceable by a small digital/automated business.
For each path, use search and open a real source when possible.
Return only:
- observed problem;
- affected customer;
- proof found;
- opened source URL;
- source date if visible;
- short factual datum;
- limits/uncertainty.

If a page is bad/404, say so and continue.
Do not use ask_human, propose_experiment, start_experiment, register_channel, act_on_channel, request_spend, request_resource or open_business.

Preserve the exact raw `tool_trace`, task/run ids, duration, model calls, 429s, cost, and substantive result.

Do not patch anything before examining that trace unless execution is impossible because of a clear regression.

## Phase 3 — diagnose before modifying

Use the trace to distinguish:

### Case A — usable direct URLs were present but ignored
Then the likely blocker is action selection/progression.
Do not immediately build a state machine.
First identify the smallest observable intervention.

### Case B — search results were mostly irrelevant/weak
Then investigate provider ordering/presentation before changing ReAct.

### Case C — browse starts but evidence extraction fails
Then the bottleneck has moved to browse/extraction.

### Case D — multi-agent run duplicates work or loses useful context
Then compare against a single-agent control before changing ORBIT.

### Case E — the mission simply cannot fit the available step budget
Quantify the step budget first; do not arbitrarily increase it without explaining what additional useful sequence becomes possible.

## Phase 4 — control experiment

As soon as practical, run an A/B-style control:

A. normal ORBIT multi-agent mission;
B. the same evidence objective handled by a single suitable agent, with an approximately comparable total action/LLM budget.

Measure:

- search count;
- browse count;
- first browse step;
- usable direct URLs returned;
- successful pages opened;
- usable proofs extracted;
- redundant searches;
- duration;
- LLM calls;
- 429 count/time;
- estimated/actual provider cost.

Interpretation:

- A bad / B good → ORBIT/decomposition/handoff suspect.
- A bad / B bad + good URLs → ReAct/progress/action-selection suspect.
- A bad / B bad + poor URLs → search/provider/presentation suspect.
- browse happens but no usable proof → browse/extraction suspect.
- success appears only with materially larger step budget → max_steps likely binding.

## Phase 5 — one minimal patch at a time

Only after a run isolates a blocker:

1. state the causal hypothesis;
2. identify the smallest code surface;
3. add/update focused tests;
4. implement the minimal change;
5. run targeted tests;
6. run relevant regression;
7. inspect git diff;
8. rerun a comparable real mission;
9. record before/after.

Do not merge multiple speculative improvements together.

## What NOT to rebuild before Friday

Unless a real regression proves otherwise, do not rebuild:

- the LLM gateway;
- the journal;
- general grounding;
- strategy/economy schemas;
- tool allowlist/type validation;
- rate-limit cooldowns;
- browser security;
- budgets/spend controls;
- self-development framework;
- role taxonomy;
- overall multi-agent framework.

## Friday handoff data

Keep a compact field log for every real experiment:

- date/time;
- task_id;
- run_id;
- git HEAD;
- mission objective;
- hypothesis under test;
- profile/budget/max_steps/allowlist;
- exact action sequence;
- search/browse trace;
- LLM model distribution;
- prompt/completion/reasoning tokens;
- LLM duration;
- 429 count/time;
- estimated/actual cost;
- human interventions;
- substantive result;
- number of usable opened proofs;
- what worked;
- what failed;
- dominant bottleneck;
- exact patch between comparable runs.

The goal for Friday is to hand a stronger model **evidence**, not a feature list.

## Decision discipline

When uncertain:
- inspect;
- measure;
- run a control;
- then patch.

Do not optimize for activity.
Optimize for information gained per experiment and for progress toward a real market loop.

## End-of-task report format

After each development/experiment cycle, report only:

1. Observed problem
2. Evidence
3. Causal hypothesis
4. Change made (or why none)
5. Tests
6. Comparable real retest
7. Before/after
8. Remaining uncertainty
9. Next single experiment