# Pipeline & Prompts Reference

This document exists so nothing in the build is a black box. It defines every stage in plain English, then reproduces the *actual* text of every real prompt in the system - not a summary or a paraphrase. If a sentence in here doesn't match `pipeline.py`, the code is the source of truth and this file is stale and needs updating.

## How many prompts define this system?

Exactly three - the real system prompts sent to the Claude API at runtime, embedded in `pipeline.py`. These are what the deployed pipeline actually says to the model on every message, reproduced in full below.

## Pipeline stage glossary

Each stage below is a distinct step in a fixed, code-orchestrated sequence (a **workflow**, not an agent deciding its own steps). The one exception, the agentic investigation step, is marked as such.

| Stage | What it means, concretely |
|---|---|
| **Classify + Extract** | One Haiku 4.5 API call. Reads the raw message text and returns a single JSON object: which of Service/Success/Sales it best fits, any other categories that are also plausible, whether it's actually contradictory, the account reference if any, a short issue-type label, sentiment, urgency, and several boolean/array flags (expansion intent, retention-risk language, sensitive-topic matches, matched keywords). Ground truth is never shown to the model - only the message text and generic instructions. |
| **Confidence scoring** | Not an LLM call - pure Python arithmetic over the extraction output (`score_confidence` in `pipeline.py`). An additive/subtractive rubric (see table below) produces a 0-100 score and a high/medium/low band. Deliberately rule-based rather than asking the model "how confident are you 0-100" - every point is independently checkable, not an opaque number the model made up. |
| **Routing / guardrails** | Pure Python (`determine_queue`). Decides the queue (who owns the message) from the extraction + confidence, applying overrides in a fixed priority order (see table below). Includes a deterministic account-size lookup (`is_large_account`) and a regex-based formal-request check (`is_formal_close_cancel`) - both plain Python, no extra API call. |
| **Multi-team loop-in** | Part of `determine_queue`. When a second team has an independent signal (not just uncertainty about the same decision), that team is added to a `loop_in` list rather than taking ownership - a single primary owner is always kept. |
| **Enterprise AE routing** | Part of `determine_queue`. A Sales-category message with a stated team size in the top band(s) (config's `enterprise_ae_team_size_bands`) gets a `sales_handling_path` of "Enterprise AE" instead of "Standard Sales" - a handling-path distinction within Sales, not a 5th top-level queue. |
| **Health/expansion flag** | Pure Python (`health_expansion_flag`). A lightweight, explicitly-caveated "this message mentions growth/expansion" note, shown whenever Success has visibility (owner or looped in). Not a verified account-health score. |
| **Reference retrieval** | Pure Python (`find_matching_article`), no API call. Before drafting, looks up the queue's mock Help Centre/playbook content (`data/help_centre_articles.json`, `success_playbook.json`, `sales_playbook.json`) by keyword overlap against the extraction's `matched_keywords`/`issue_type`. Whether a match was found feeds both the draft prompt and the draft-quality confidence score below. |
| **Drafting** | One Sonnet 5 API call (`draft_response`). Writes a short reply for human review, grounded in the matched reference article if one was found, reading brand guidelines fresh from a JSON file on every call. If it's Service with a missing reference, the draft is a clarification request instead of a guess at resolution. If the queue is Team Lead Triage, the draft says so explicitly rather than presenting a guess as a decision. |
| **Draft-quality confidence** | Not an LLM call - pure Python (`score_draft_confidence`), distinct from the routing confidence score above. Answers "is this specific draft likely good enough to send," not "did this land in the right queue." See the dedicated section below. |
| **Agentic investigation** (the only agentic step) | One-to-four Sonnet 5 API calls in a tool-use loop (`investigate_uncertain_message`), triggered only for low-confidence messages. Unlike every step above, the model itself decides which of three read-only tools (if any) to call - two account lookups plus a Help Centre search - based on what's actually in the message, not a prescribed sequence. Output is advisory text for the human reviewer; it can never send, modify, or action anything. |

## Confidence rubric (exact signals, from `score_confidence`)

| Signal | Effect |
|---|---|
| Account reference present | +35 |
| Single fitting category, no hedging | +35 |
| Category-specific terminology matched | +15 |
| Sentiment/urgency stated unambiguously (not "mixed") | +15 |
| Contradictory category signals | -40 |
| No reference where the category normally expects one | -30 |
| Message very short/generic (<8 words) | -20 |
| Multiple categories plausible (no outright contradiction) | -15 |

Clamped to 0-100. Bands: high >= 80, medium >= 50, low < 50 (config).

## Routing priority order (exact, from `determine_queue`)

1. Sensitive topic present -> queue = Service, unconditionally (never downgraded by low confidence).
2. Else retention-risk language **and** a formal close-account/cancel-subscription request (regex match) -> queue = Service (Support keeps ownership of routine account-lifecycle requests); Success is looped in only if the account is large.
3. Else retention-risk language (softer language, not a formal request) -> queue = Success, unconditionally.
4. Else contradictory signals -> queue = **Team Lead Triage**, not Success.
5. Else confidence score <= Team Lead Triage floor (20/100) -> queue = Team Lead Triage.
6. Else -> queue = the model's predicted category.

Whenever the queue differs from the model's raw predicted category, that raw category is looped in for context rather than lost.

### Why contradictory signals don't default to Success

Routing every contradictory-signals message straight to Success would make Success a dumping ground for ambiguous technical escalations that Support should own, and would push Success toward being reactive (handling overflow triage) rather than proactive. It routes to Team Lead Triage instead - a Support-side escalation point - and Success only gets looped in through the same content-driven signals any other queue would trigger (an expansion mention, a Success category alternative), not merely because the signals were ambiguous.

### Why formal close/cancel requests don't default to Success either

"Close Account" and "Cancel Subscription" are 2 of the 8 real Help Centre support-form categories - a customer explicitly using that form is asking for a routine account-lifecycle action, not necessarily opening a relationship conversation. Support keeps ownership; Success is looped in only when the account is large (`arr_band` in config's `large_account_arr_bands`) - the retention stakes are high enough there to warrant proactive visibility. Softer language ("we'll have to look at other providers") isn't a formal request and keeps the original behaviour: Success owns it directly, since that genuinely is a relationship conversation.

---

## Prompt 1 of 3: `classify_and_extract` (model: claude-haiku-4-5)

Purpose: the single call that produces the structured extraction every later stage depends on. Runs on every message, no exceptions.

The system prompt is assembled per-message from config + an optional entry-channel block. Below is the exact template with the config-driven parts shown as `{...}`:

```
You classify inbound customer messages for {company_name},
a B2B project-management SaaS company, into exactly one of:
{categories}.

Service = support/technical issues (login, access, bugs,
integrations, outages, billing problems, refunds, compliance).
Success = existing customer wanting a business review, renewal
discussion, or to grow/expand their usage.
Sales = a prospect or existing customer asking about pricing,
plans, or signing up for something new.

[Entry channel prior - only included if entry_channel is known,
same "helpful prior, never determining" framing as the Success
mailbox note below.]

Reference terminology per category (a hint, not an exhaustive list):
- Service: login, password, 2fa, sso, outage, downtime, bug, error,
  crash, integration, api, sync, webhook, billing, invoice, refund,
  chargeback, compliance, gdpr
- Success: qbr, ebr, renewal, expand, expansion, scale, scaling, grow,
  growth, upgrade, review, account health, onboarding, enterprise,
  new team, new department
- Sales: pricing, price, plan, demo, trial, quote, discount, compare,
  comparison, sign up, signing up, new customer, setup fee,
  contract terms

retention_risk_language: set this true for explicit close/cancel
account or subscription requests, and also for softer but real
language about leaving or switching providers even without a formal
cancellation request. Anger alone ("this is ridiculous for a paying
customer") is NOT retention risk unless the message also expresses
an intent to leave or reconsider the relationship.

team_size_band only matters for Sales-category messages, mirroring a
real Sales/Contact form's "Approx. team size" field: under_10,
10_to_50, 50_to_200, 200_to_1000, 1000_plus, or unknown.

sensitive_topic_flags is a NARROW field. Only use terms from this
exact list, and only when clearly present: {sensitive_topics}.
Match the FULL concept, not a substring - a locked-out login is not
"unauthorized access"; a routine billing question is not a dispute.
[... full worked examples distinguishing near-misses from the real
thing, at temperature=0 for deterministic extraction ...]

Be honest about ambiguity: if a message clearly fits more than one
category, say so via category_alternatives and contradictory_signals
rather than forcing false confidence.
```

**Note:** this quoted block is abbreviated for readability - the real prompt in `pipeline.py` includes the full worked positive/negative examples for the sensitive-topic and retention-risk fields. `pipeline.py` is the source of truth; treat this doc as a plain-English orientation to it, not a byte-exact mirror.

The message itself is sent as the (only) user-turn content, with no ground truth attached. The response is constrained to a strict JSON schema (`output_config: {"format": {"type": "json_schema", ...}}`) - see `EXTRACTION_SCHEMA` in `pipeline.py` for the full field list.

---

## Prompt 2 of 3: `draft_response` (model: claude-sonnet-5)

Purpose: writes the actual reply draft a human reviews before sending. Never called for messages where a prior step failed; always produces a draft, never a sent message.

Three variants of the *instruction* line depending on routing outcome, then a shared brand-guidelines block appended when `data/brand_guidelines.json` is present (read fresh on every call, not cached):

```
[If Service and a reference is required but missing:]
Key information is missing (no account reference). Draft a
brief, polite reply asking the customer for that specific missing
detail. Do not attempt to resolve the issue.

[If routed to Team Lead Triage:]
Draft a brief, helpful reply addressing: {issue_type}. This message's
queue assignment is uncertain and pending manual review by a team lead,
so treat {category} as a best guess only, not a confirmed team. This
is a draft for human review before sending, not a final answer.

[Otherwise:]
Draft a brief, helpful reply for the {queue} team to send this
customer, addressing: {issue_type}. This is a draft for human review
before sending, not a final answer.
```

Reference block (appended when `find_matching_article` finds a matching Help Centre/playbook article for this queue):

```
Relevant reference material found for this message ("{article title}"):
{article answer}
Ground your reply in this - reuse its substance in your own words
rather than inventing an answer, but don't just paste it verbatim if
the customer's specific situation needs a more tailored response.
```

Brand block (appended whenever the guidelines file loads successfully):

```
Brand guidelines for {company_name} (follow these exactly):
Tone: {tone}
Voice principles:
{voice_principles, one per line}
Never use these words/phrases: {banned_words_or_phrases}
Formatting: {formatting}
Sign off with: {sign_off}

Separately, avoid AI-isms - words and patterns that read as
AI-generated rather than human-written:
Never use these words: {avoid_ai_isms.banned_words}
Never use these phrases: {avoid_ai_isms.banned_phrases}
Style rules:
{avoid_ai_isms.style_rules, one per line}
```

Full system prompt is: `You draft short customer-support replies for {company_name}. Keep it to 2-4 sentences unless technical detail requires more, no filler.` + the instruction + the reference block + the brand block. The user-turn content is `Original message: {text}\n\nInstruction: {instruction}`. `thinking` is explicitly disabled for this call - a short drafting task doesn't benefit from extended reasoning, and leaving it on would only inflate cost.

### Draft-quality confidence (the second, distinct confidence score)

The routing confidence score answers one question: *did this message land in the right queue?* It says nothing about whether the drafted reply itself is any good - a message can be routed perfectly and still get a weak, generic, unaided answer, or land in an uncertain queue and still happen to get a well-grounded draft. So `score_draft_confidence` is a second, separate score answering: *is this specific draft likely good enough to send?*

Rule-based, same philosophy as the routing confidence score: was this draft actually grounded in a real, matched reference article, or is it the model's own unaided attempt?

```
if needs_clarification:            band = "n/a"    (no answer was attempted)
elif queue == "Team Lead Triage":   band = "low"    (queue itself unconfirmed)
elif a reference article matched:   band = "high"   (grounded in known-correct source material)
else:                               band = "low"    (fully generative, nothing to check it against)
```

---

## Prompt 3 of 3: `investigate_uncertain_message` (model: claude-sonnet-5, agentic)

Purpose: the only agentic component. Triggered only when `confidence["band"]` is in `investigation_trigger_bands` (currently `["low"]`) - never on the full batch.

System prompt (fixed, no per-message templating):

```
You are helping a human support reviewer triage an uncertain customer
message. You have three read-only tools available: two account
lookups and a Help Centre search. Decide for yourself which, if any,
are worth calling, based on what the message actually contains - do
not call a lookup tool with a reference you are guessing at or
inventing, and do not search the Help Centre with a query unrelated to
what's actually being asked. If the message has no usable reference,
say so plainly rather than calling a tool anyway. When you are done,
write a short (2-3 sentence) note for the human reviewer summarising
what you found and what it means for handling this message.
```

User turn: `Message: {text}\n\nExtracted account reference (if any): {reference or "none found"}`.

Three tools available (`INVESTIGATION_TOOLS` in `pipeline.py`):

- **`lookup_subscription_status(account_reference)`** - live subscription/billing status (plan tier, billing status, seats used, last login). Returns `not_found` if the reference doesn't exist.
- **`lookup_account_context(account_reference)`** - account-level context (plan tier, account age, recent ticket volume, ARR band) for the same reference. Returns `not_found` if there's no account on file.
- **`search_help_centre(query)`** - free-text search over the mock Help Centre articles, returning the best-matching article's title and answer, or `not_found`.

All three are backed by synthetic mock data - read-only, no write capability exists at all. Hard cap of 4 iterations. The model decides for itself, per message, which (if any) of the three tools are worth calling - this is the one place in the whole build where the model chooses its own next action rather than following a fixed sequence, which is what makes it agentic rather than another workflow step.

### Why not just always call all three tools?

Two reasons. First, cost/latency: calling every tool regardless of relevance on every low-confidence message adds real latency for no accuracy benefit on messages where a given tool has nothing useful to return. Second, and more importantly: forcing a fixed "call everything" sequence would turn this back into a workflow, not an agent - the entire point of this being the one agentic step is that the model exercises judgement about *which* lookups are worth making based on what the message actually says, the same judgement a human reviewer exercises before looking things up.

## Error handling and latency (why neither is a production risk)

In production, this pipeline would run as a background enrichment step, not a blocking one: a new message would still land in the normal helpdesk/CRM inbox exactly as it does today, visible and workable by a human immediately. The AI call happens asynchronously and writes its output (category, confidence, draft, flags) onto the ticket once it finishes - it never has to complete before a human can pick up and work the ticket manually.

That means a slow response or a failed API call (rate limit, timeout, partial outage) has a bounded, safe failure mode: that one ticket's enrichment simply arrives late or not at all, and a human handles it exactly as they would have without the AI at all. Nothing blocks, nothing silently mis-routes, and no message is ever auto-sent. `batch_runner.py` sets explicit `timeout=60.0` and `max_retries=3` on the API client so transient failures retry automatically before falling back to "no enrichment yet" rather than raising.

## What is NOT a prompt

`score_confidence`, `determine_queue`, `health_expansion_flag`, and `score_draft_confidence` make no API calls at all - they are plain Python functions operating on the structured output of Prompt 1. No prompt exists for them because none is needed; the whole point of extracting structured fields first is that routing logic can then be ordinary, auditable code instead of another opaque model judgement call.

## Honest limitations

- **The confidence rubric needs real recalibration, not just tuning.** Every weight in `score_confidence`'s rubric was chosen by looking at the score distribution on this build's own small synthetic dataset - a reasonable starting point, not a substitute for real usage data. It should be treated as a first draft.
- **This is a prototype, not a production system.** 100 synthetic messages is enough to exercise the pipeline's design and guardrails, not enough to make a statistical accuracy claim that would hold on real, messy production traffic.
- **Attachment/malware scanning is deliberately out of scope.** That's a specialised security capability that belongs in a dedicated scanning service ahead of this pipeline, not homebrewed inside a triage agent.
