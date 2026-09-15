# Decisions

One numbered entry per significant choice. Each names the alternative it
rejected and what it cost.

---

## D1. Trust is reciprocation, not contact — and 050 is what that costs.

**Rejected:** treating "this sender appears in the recipient's message history"
as evidence of a relationship.

**Why:** it is unsatisfiable for exactly the population it was meant to catch.
Repeat phishers accumulate history by sending — `u_050` had **12 prior messages**
to one recipient and zero replies. Sending volume was buying trust. The gate now
requires an opened **and** replied round trip inside a 30-day window, so a
phisher can send indefinitely without ever clearing it. All four labeled phishing
rows evaluate `reciprocated=False` under the new gate; under the old one, none of
them fired at all.

**What it cost:**

> Sample 050 regressed under the stranger-detection rule added to fix 049. A real
> friend's message ("Reached home and had dinner") was classified as unknown
> because no opened+replied reciprocation existed inside the 30-day window.
> Action stayed correct. Ground truth is digest/personal; we emit digest/unknown.
> Cost accepted: fixing 049 (a genuine stranger) required a gate that misses 050.
> The alternative — a longer window or a different signal — would have introduced
> other failure modes on data we haven't seen.

Net effect on the labeled set was +5 both-correct (19/30 → 24/30); 050 is the
single row it costs, and it costs only the `message_type`, not the action.

**Why the window was not widened.** 30 days is the horizon every behavioural
column in the dataset is measured over — `messages_opened_30d`,
`notifications_dismissed_30d`, `messages_read_30d`. Widening it to fit one row
would decouple the gate from the only cadence the data actually describes, and
would weaken it precisely where it does safety-critical work in `p2p-phishing`.
Trading phishing recall for one `message_type` on one sample is not a trade worth
making.

---

## D2. Voice notes reach the decision tree as text, through the same seam.

**Rejected:** a dedicated voice path — branches keyed on duration, media type, or
"voice note from a family member".

**Why:** it would have created a second way of reasoning about the same question,
and the two would have drifted. A transcript is a description, not a verdict:
Whisper converts audio to text and makes no routing claim. Routing it through the
existing seam means voice inherits every property the text path already has —
deterministic flags, negation handling, injection resistance, and the guarantee
that no branch reads raw content to reach a verdict.

**How it works.** The transcript is written into the observation's `text_found`
field, the same slot an image's OCR text would occupy. From there `observe_text()`
produces the boolean flag record and `_text(ctx)` exposes it to every marker list
in the tree. No branch knows whether a message was typed or spoken.

**The case that decided it.** `sample_msg_041` and `sample_msg_042` are both voice
notes from members of the same `extended_family` group, identical on every
metadata field available. The transcripts are *"Had dinner, call when free,
nothing urgent"* and *"Please call now. Dad is unwell and we are going to the
clinic."* Ground truth splits them `digest/personal` and `notify/urgent`. No
amount of metadata reasoning separates those two rows.

**What could break.** A Whisper mis-transcription is unfixable at run time and
fails silently — there is no confidence signal the tree could act on, and a wrong
transcript produces confidently wrong flags rather than an error. We saw the
failure mode once: `vn_014` emitted a ~110× repetition loop until
`condition_on_previous_text=False` was set. That was caught by reading the output.
A subtler one — a dropped negation, "not urgent" heard as "urgent" — would not be.
This is the only place in the pipeline where a wrong answer cannot be traced to a
rule we wrote.

Two smaller consequences. The `NEGATORS` list in `observe.py` exists largely
because of this path: both labeled voice notes contain urgency keywords and one of
them means the opposite. And transcripts are cached by
`(path, mtime, params_hash)`, so changing the model or decode flags re-transcribes
rather than silently serving a stale reading.

---

## What I explicitly chose not to build

**A `payment` branch.** `payment` is one of the 11 legal message types and appears
in **zero** of the 30 labeled examples. Any branch emitting it would have been
written from the label name alone, with no case to test against and no way to know
whether it fired correctly. The cost is visible and accepted: `payment` is
predicted 0 times across all 110 rows, so if the hidden set contains payment
reminders, every one is a type miss. Inventing a branch would have converted a
known gap into an unmeasurable one.

**A widened reciprocation window.** Extending the 30-day horizon would have
recovered `sample_msg_050` (see D1). It would also have degraded the phishing
gate, which is the same predicate — a longer window gives a persistent sender more
chances to accumulate an incidental round trip. One `message_type` on one row is
not worth loosening the only defense against repeat phishers.

**AI-as-judge scoring on the 30 samples.** With n=30, one row is already 3.3
percentage points and the numbers are noisy enough to be near-uninformative below
a two-row margin. Adding a model-based grader would have layered a second,
uncalibrated source of variance on top of that, and left us unable to tell a
scoring artifact from a routing change. Exact-match scoring against the labels is
cruder but its error bars are the ones we actually know.

**A vision retry against a different provider.** The vision seam is implemented
and unrun because the account has no credits. Swapping in another provider twelve
hours before submission would have meant a new SDK, a new request shape, a new
schema contract, and a new failure surface — for a marginal return on the 5 image
rows currently using the metadata fallback, against a real risk of breaking a
frozen, preflight-verified `output.csv`. The right move at that point was to keep
the correct implementation in place, degrade honestly, and document the ceiling.
