# Phase 1 — Problem Analysis

**Project:** Message Notification Router (HackerRank Orchestrate, Aug 2026)
**Date:** 2026-08-01
**Status:** Design only. No implementation code written in this phase.

Claim-tagging convention used throughout:
**(a) STRUCTURAL** — guaranteed by the spec or by construction; safe to encode as a hard rule.
**(b) EMPIRICAL, n=N** — observed in the data; may be noise. Never promoted to a hard rule on its own.

---

## 1. The Immutable Contract (extracted verbatim)

Everything below is quoted from `problem_statement.md` / `README.md` / `dataset/output.csv`. This section is the single source of truth. No field may be renamed, reordered, or extended; no enum value may be invented.

### 1.1 Output file

One row in `output.csv` for every row in `dataset/messages.csv`.

> "For every row in `dataset/messages.csv`, generate one row in `output.csv`."

Verbatim header, from `dataset/output.csv` line 1 and reproduced identically in `AGENTS.md` §6.2:

```text
message_id,action,message_type,reason,confidence,evidence_message_ids
```

### 1.2 Required columns, in order

Verbatim from `problem_statement.md` § "Required output":

> Required columns, in order:
>
> - `message_id`
> - `action`
> - `message_type`
> - `reason`
> - `confidence`
> - `evidence_message_ids`

| # | Column | Type |
|---|---|---|
| 1 | `message_id` | string, echoed from input |
| 2 | `action` | enum, 3 values |
| 3 | `message_type` | enum, 11 values |
| 4 | `reason` | free text |
| 5 | `confidence` | number in [0, 1] |
| 6 | `evidence_message_ids` | `;`-separated ids, or the literal `none` |

### 1.3 Column meanings (verbatim)

> - `action`: final routing decision
> - `message_type`: best-fit message category
> - `reason`: short human-readable explanation for the decision
> - `confidence`: number from `0` to `1`
> - `evidence_message_ids`: semicolon-separated historical message IDs used as evidence; write `none` if no useful historical message exists

### 1.4 Allowed values — `action` (exactly 3, verbatim)

> - `notify`: interrupt the user now
> - `digest`: safe but low priority; show later
> - `mute`: repetitive, unwanted, low-value, suspicious, scam-like, or unsafe for this user

```text
notify | digest | mute
```

### 1.5 Allowed values — `message_type` (exactly 11, verbatim, in spec order)

> - `personal`
> - `urgent`
> - `event`
> - `payment`
> - `business_update`
> - `promotion`
> - `greeting`
> - `forward`
> - `spam`
> - `scam`
> - `unknown`

```text
personal | urgent | event | payment | business_update | promotion | greeting | forward | spam | scam | unknown
```

### 1.6 Input schema (verbatim field list, `dataset/messages.csv`)

> - `message_id`: unique incoming message ID
> - `user_id`: user receiving the message
> - `conversation_type`: `personal`, `group`, or `business`
> - `group_id`: group ID if the message is from a group
> - `business_id`: business ID if the message is from a business account
> - `sender_user_id`: sender user ID if the message is from a user
> - `created_at`: message timestamp
> - `message_text`: text content for text messages; empty for voice-note messages
> - `media_type`: empty, `image`, or `voice`
> - `media_id`: linked image or voice-note ID, if present
> - `forwarded_count`: forwarding signal

### 1.7 Scoring criteria (verbatim)

> The scoring will consider:
>
> - correctness of `action`
> - correctness of `message_type`
> - usefulness and consistency of `reason`
> - whether `evidence_message_ids` point to relevant historical messages
> - reasonable confidence calibration

### 1.8 Contract facts verified against the actual files

| Fact | Verified value | Tag |
|---|---|---|
| Rows to predict | 110 | (a) STRUCTURAL |
| `output.csv` template rows | 110, ids only, all other cells empty | (a) STRUCTURAL |
| `output.csv` row order == `messages.csv` row order | **True**, exact | (a) STRUCTURAL |
| Labeled examples in `sample_messages.csv` | 30 | (a) STRUCTURAL |
| `message_history.csv` rows / distinct ids | 412 / 412 | (a) STRUCTURAL |
| `message_events.csv` rows | 412, one per history row, 100% coverage | (a) STRUCTURAL |
| Referential integrity of `user_id`/`group_id`/`business_id` in `messages.csv` | zero dangling keys | (a) STRUCTURAL |
| Media referential integrity | every `media_id` resolves in `images.csv`/`voice_notes.csv` **and** exists on disk; zero missing | (a) STRUCTURAL |

> Note: naive `wc -l` on these CSVs is wrong — `message_text` contains embedded newlines inside quoted fields. All counts above come from a real CSV parser.

---

## 2. Explicit Requirements (stated in the spec)

| # | Requirement | Source |
|---|---|---|
| E1 | Exactly one output row per `message_id` in `messages.csv`; all 110 covered. | problem_statement, AGENTS §6.2 |
| E2 | Exact column names in exact order. | problem_statement, AGENTS §6.2 |
| E3 | `action` ∈ {notify, digest, mute}. | problem_statement |
| E4 | `message_type` ∈ the 11 listed values. | problem_statement |
| E5 | `confidence` is a number in [0, 1]. | problem_statement |
| E6 | `evidence_message_ids` is `;`-separated, or literally `none` when no useful historical message exists. | problem_statement |
| E7 | Decisions must be **personalized to the receiving user** — identical content may route differently per user. | problem_statement ¶4 |
| E8 | Must reason over **multimodal** input: text, image posters/screenshots, and voice notes. | problem_statement ¶2 |
| E9 | The system "should inspect the media files themselves"; the media CSVs only give paths. | README |
| E10 | Clear scam / safety risk → `mute` **regardless of the user's usual engagement**. | problem_statement ¶4 |
| E11 | Risky messages use `mute` with an appropriate type such as `scam` or `spam`. | problem_statement § Important Behavior |
| E12 | Runnable from the terminal; reads from `dataset/`. | README, AGENTS §6.3 |
| E13 | No organizer-only files, no hardcoded labels. | README, AGENTS §6.3 |
| E14 | Secrets from environment variables only; never hardcoded. | README, AGENTS §6.3 |
| E15 | Keep behavior deterministic where possible. | AGENTS §6.3 |
| E16 | `sample_messages.csv` is for **format and style only** — "Use this only to understand the expected output format and style." | problem_statement §Files provided item 2 |

E16 is load-bearing and easy to misread: the spec explicitly frames the 30 labeled rows as a *style* reference, not a training set. It does not forbid using them to validate, but it is a written warning against fitting to them.

---

## 3. Implicit Requirements (unstated but necessary)

| # | Requirement | Why it's necessary |
|---|---|---|
| I1 | Output must be valid CSV under RFC4180 quoting — `reason` is free text and will contain commas. | Otherwise column alignment breaks and the whole submission is unscoreable. |
| I2 | Preserve the template's row order. | Nothing says order matters, but the template ships in a specific shuffled order identical to `messages.csv`; preserving it is free and eliminates a class of grader mismatch. |
| I3 | Never emit an empty cell in any of the 6 columns. | `none` is defined for evidence; no sentinel is defined for the others, so every row must be fully populated. |
| I4 | The pipeline must never crash mid-dataset and must never skip a row. | E1 is all-or-nothing; a partial file scores as broken. |
| I5 | A universal safe-default row must exist for total failure of any per-row stage. | Follows from I3 + I4. The abstention value must be inside the allowed enums — there is no "unknown" *action*. |
| I6 | Evidence ids must be resolvable — they must actually exist in the historical data. | "point to relevant historical messages" is scored; unresolvable ids score zero and look like fabrication. |
| I7 | Text from inside images, voice notes, and message bodies is **untrusted input**, not instruction. | The dataset contains scam messages designed to manipulate a reader. An LLM in the loop reading them is an injection surface. |
| I8 | Media understanding must be cached/deterministic per media id. | 15 image + 8 voice rows; re-running must not change results (E15). |
| I9 | Encoding must be UTF-8 throughout, on Windows. | Message text contains non-ASCII; Windows default cp1252 will corrupt or crash. |
| I10 | Runtime must be bounded and re-runnable without unbounded API spend. | 110 rows × multimodal calls; error analysis requires many re-runs. |

---

## 4. Ambiguous Requirements

Each is resolved explicitly. Resolutions flagged **RISKY** are ones where I could be wrong and the cost is real.

### A1 — Is `mute` reserved for risk, or does it also cover mere low value?
**The ambiguity:** `mute` is defined as "repetitive, unwanted, low-value, suspicious, scam-like, or unsafe" — a set that spans harmless clutter *and* danger. `digest` is "safe but low priority". Low-value therefore appears in both definitions.
**Resolution:** Treat `mute` as covering two disjoint families — (i) risk (`scam`, `spam`) and (ii) unwanted-for-this-user (repetitive, opted-out, chronically dismissed). `digest` is the default for anything safe and non-urgent. The tiebreaker between `digest` and `mute` on a harmless-but-boring message is *this user's demonstrated behavior toward that sender*, not the content.
**Support:** (b) EMPIRICAL, n=30 — `promotion` splits 3 `digest` / 3 `mute` in the samples, which is only explicable by per-user history. This directly corroborates E7.
**Risk:** MEDIUM. This is the highest-volume decision boundary in the dataset.

### A2 — Does `do_not_disturb_window` affect `action`?
**The ambiguity:** `users.csv` ships a `do_not_disturb_window` for every user, and the problem statement *never mentions it* in the routing rules. Providing it implies it matters.
**Measured:** 8 of 110 incoming messages fall inside their recipient's DND window. **Zero of the 30 labeled samples do.** The labeled data is therefore completely silent on this.
**Resolution:** Do **not** let DND alone flip a decision. Use it only as a demotion signal that can push a borderline `notify` → `digest`, never as something that can create a `mute`, and never as something that can suppress a genuine urgent/safety message.
**Tag:** (a) STRUCTURAL by choice — a deliberately conservative reading, because there is no empirical basis whatsoever.
**Risk:** **RISKY / HIGH.** 8 rows ≈ 7% of the score is exposed and I have zero labeled precedent. If the graders intended "DND ⇒ never notify", the conservative reading loses those rows. Recorded as an open question for Phase 9.

### A3 — What triggers `message_type = forward`?
**The ambiguity:** `forwarded_count` is a numeric input column and `forward` is a label. Is `forward` the type for *any* forwarded message, or only for chain-forward noise?
**Measured:** 32 of 110 incoming rows have `forwarded_count > 0` (range 1–11). The samples contain exactly **one** `forward` label, and it is `mute`.
**Resolution:** `forward` is a **content** category (chain letters, viral/unattributed reshares), not a mechanical consequence of `forwarded_count > 0`. `forwarded_count` is a *supporting signal* that raises confidence in `forward`/`spam`, never the sole cause.
**Support:** (b) EMPIRICAL, n=1 label vs 32 forwarded rows — if `forward` meant "forwarded_count > 0", roughly a third of the samples would carry it. They don't.
**Risk:** MEDIUM — n=1 is nearly no evidence, but the alternative reading is arithmetically contradicted.

### A4 — What is the universe of valid `evidence_message_ids`?
**The ambiguity:** "historical message IDs" is not defined against a specific file.
**Measured:** All 31 evidence ids across the 30 samples resolve in `message_history.csv`; **31/31 belong to the receiving user**; 27/31 also share the target's `group_id`, `business_id`, or `sender_user_id`.
**Resolution:** Restrict the evidence pool to `message_history.csv`, filtered to the **same `user_id`** as the row being predicted. Do not cite other rows of `messages.csv` (they are unlabeled peers, not history). Prefer same-context evidence but do not require it — the 4/31 cross-context citations show it is allowed.
**Tag:** (a) STRUCTURAL for the same-user restriction (31/31 with zero counterexamples, and it follows from E7).
**Risk:** LOW.

### A5 — How many evidence ids should be emitted?
**The ambiguity:** No cap, and the scoring rubric ("point to relevant historical messages") does not say whether it rewards recall or punishes noise.
**Measured, n=30:** 1 id → 25 rows; 2 ids → 3 rows; `none` → 2 rows. Max observed = 2.
**Resolution:** Emit 1–2 ids, ranked by relevance, and emit `none` freely rather than padding. Hard cap at 2.
**Tag:** (b) EMPIRICAL, n=30.
**Risk:** MEDIUM — if the grader uses recall-oriented set overlap, capping at 2 leaves points on the table. Capping is still correct under the more likely precision-or-F1 reading, and padding with weak ids actively damages the `reason`↔evidence consistency the rubric also scores.

### A6 — What does "reasonable confidence calibration" mean?
**The ambiguity:** Undefined. Could be scored as correlation between confidence and correctness, as a Brier/log score, or as a rough sanity check.
**Measured, n=30:** every sample confidence lies in **[0.78, 0.91]**. There is not a single low-confidence example anywhere in the labeled data.
**Resolution:** Confidence must be a *function of named signal strength*, computed in code from the observation record — not a number the model invents. Emit the sample-like band (~0.80–0.92) for clean, well-evidenced decisions, and genuinely lower values (down to ~0.45) for abstention-ish rows. Never emit 0.99.
**Rationale:** A system that returns 0.85 for everything trivially matches the sample distribution but demonstrates no calibration. A monotone relationship between evidence strength and confidence is what the rubric is plausibly probing.
**Risk:** MEDIUM. Deviating from the observed band is deliberate and should be flagged for sign-off in Phase 7.

### A7 — What is the abstention value, given `action` has no "unknown"?
**The ambiguity:** `message_type` has `unknown`, but `action` does not. So a maximally-uncertain row still has to pick an interrupt/defer/suppress verdict.
**Resolution:** The universal safe default is **`action=digest`, `message_type=unknown`, `evidence_message_ids=none`, low confidence**. `digest` is the only action whose failure mode is symmetric-benign: it neither interrupts the user wrongly nor suppresses something important.
**Tag:** (a) STRUCTURAL — derived from the definitions in §1.4, not from the sample.
**Corroboration:** (b) EMPIRICAL, n=1 — the single `unknown` sample row is indeed `digest`.
**Risk:** LOW.

### A8 — `message_type` is single-label, but real messages are multi-faceted.
**The ambiguity:** A bank payment reminder from an unverified lookalike domain is simultaneously `payment` and `scam`. A society notice about a meeting is both `event` and `business_update`. The spec says "best-fit" and gives no precedence.
**Resolution:** Define an explicit, ordered precedence in Phase 7, risk-first: `scam` > `spam` > `urgent` > `payment` > `event` > `forward` > `business_update` > `promotion` > `personal` > `greeting` > `unknown`. Risk labels dominate by construction, which is the only reading consistent with E10.
**Tag:** (a) STRUCTURAL for the risk-first prefix (mandated by E10); (b) design choice for the tail ordering.
**Risk:** MEDIUM — the tail ordering is asserted, not evidenced.

### A9 — Are `scam`/`spam` ever paired with a non-`mute` action, or vice versa?
**Measured, n=30:** 15 distinct (action, type) pairs occur. `scam` (4) and `spam` (1) and `forward` (1) appear **only** with `mute`. `urgent` (4) appears **only** with `notify`. `payment` **never appears at all** in the labeled sample.
**Resolution:** Encode `type ∈ {scam, spam} ⇒ action = mute` as a hard invariant (this is E10/E11 restated, so it is structural, not sample-fitting). Do **not** encode `urgent ⇒ notify` as a hard rule — it is (b) EMPIRICAL, n=4, and A2's DND question could legitimately break it.
**Risk:** LOW for the encoded half.

### A10 — `payment` is a legal label with zero labeled examples.
**The ambiguity:** One of 11 allowed types never appears in the style reference. Either the hidden set contains payment messages the sample happens to miss, or `payment` is rare.
**Resolution:** Support `payment` fully and let it be selected on content (dues, invoices, reminders, transaction confirmations), while ensuring A8 precedence sends fraudulent payment lures to `scam` instead. Do not suppress the label merely because the sample lacks it.
**Risk:** MEDIUM — this is precisely where over-fitting to n=30 would cause silent, systematic loss.

### A11 — Is `reason` scored by content, by style, or by consistency with the other columns?
**Measured, n=30:** reasons are a single sentence, **10–20 words** (median 14), third person, referring to roles and behavior ("A trusted group admin sent a time-sensitive update…"), and never quoting message ids or brand names.
**Resolution:** Generate `reason` from the same structured facts that produced the verdict, constrained to one sentence of ≤ 25 words, matching the observed register. Critically: `reason` must be *derived from the decision*, never authored independently — the rubric scores "consistency", so a mismatch between reason and action is a scored defect.
**Tag:** (b) EMPIRICAL, n=30 for the style envelope; (a) STRUCTURAL for the derive-from-decision rule.
**Risk:** LOW.

### A12 — Timezone and DND boundary semantics.
**The ambiguity:** `created_at` carries no timezone. DND windows wrap midnight (e.g. `22:00-07:00`). Inclusivity of endpoints is unspecified.
**Resolution:** Treat all timestamps as naive local time in a single implicit timezone; compare wall-clock only. Window is `[start, end)` with wrap-around. Given A2 caps DND's influence to a demotion, boundary errors are bounded to at most a `notify`→`digest` shift on a handful of rows.
**Risk:** LOW — contained by A2's conservative resolution.

### A13 — The same image appears in multiple messages.
**Measured:** `img_008` is used by 3 messages, `img_010` by 2, `img_003` by 2 (15 image rows, 11 distinct images).
**Resolution:** This is a gift, not a problem: it is direct proof that media content **cannot** determine the action by itself, since the same poster routes to different users with different histories. Media understanding is therefore an *observation* stage feeding the decision, never a decision stage. Cache media observations by `media_id` — 23 media rows collapse to 19 distinct media analyses.
**Tag:** (a) STRUCTURAL.
**Risk:** NONE. This resolution also cuts multimodal cost by ~17%.

### A14 — "Deterministic where possible" (E15) with a model in the loop.
**The ambiguity:** An LLM call is not deterministic, but E15 asks for determinism.
**Resolution:** Scope the claim honestly and structurally: the decision layer is pure and deterministic *given the logged observations*; the observation layer is not. Persist observations so the decision layer can be re-run for free and byte-identically. Never claim end-to-end determinism anywhere in the README.
**Tag:** (a) STRUCTURAL.
**Risk:** NONE — this is an honesty constraint, not a gamble.

---

## 5. Hidden Assumptions (made explicit so they can be attacked)

| # | Assumption | If wrong |
|---|---|---|
| H1 | The hidden ground truth was produced by a process broadly consistent with the 30 sample labels. | Every empirical resolution above is void. |
| H2 | The 110 hidden labels have a class balance not wildly unlike the sample (roughly even thirds across actions). | A system tuned for balance under-performs on a skewed set. |
| H3 | `message_history.csv` + `message_events.csv` describe the *same* user population and are joinable on (`user_id`, `message_id`). Verified: 412/412 coverage. | Evidence selection loses its behavioral signal. |
| H4 | Behavioral counts (opened / dismissed / reported / muted_after) are honest proxies for preference. | Personalization becomes noise. |
| H5 | `verified`, `official_domain` vs `domain_used_by_sender`, and account age in `business_accounts.csv` are the intended scam signals. | The scam detector is looking in the wrong place. |
| H6 | Images are legible enough for OCR and voice notes are intelligible enough for ASR. | 23/110 rows (21%) degrade to the safe default. **Must be verified in Phase 0/2 before committing to a multimodal design.** |
| H7 | Media files are not adversarial to the *pipeline* (no decompression bombs, no malformed containers). | Crash → violates I4. |
| H8 | The grader compares on `message_id`, not on row position. | I2 (preserve order) already neutralizes this. |
| H9 | The reason text is graded by a human or an LLM judge, not by exact string match. | Free-text optimization is moot. |
| H10 | `created_at` on incoming messages is later than the history it should be judged against. Incoming spans 2026-07-18 → 2026-07-31. | Evidence retrieval could cite "future" messages, which would be incoherent. Worth a Phase 2 check. |

---

## 6. Expected Evaluation Challenges

| # | Challenge | Mitigation |
|---|---|---|
| C1 | **n=30 is the only labeled data.** Every accuracy number computed on it carries roughly ±9pp at 1σ. | State the sample-size caveat next to every number, every time. Never report a single best run. |
| C2 | Two of the five scored dimensions (`reason`, `confidence`) have **no objective local metric**. | Score what can be scored (action, type, evidence-hit); for the rest, enforce structural properties — reason derived from the verdict, confidence a pure function of signal strength. |
| C3 | The sample is a **style reference by the spec's own words** (E16); optimizing against it is explicitly discouraged. | Use it as a regression gate ("did I get worse?"), not as an optimization target. Freeze it early; do not iterate to a number on it. |
| C4 | Model non-determinism means metric movement may be noise. | Measure variance across ≥2 independent runs before believing any delta. Report "X% across two runs". |
| C5 | Media understanding cannot be validated — there are no media ground-truth labels. | Grounding tests: blank the media (verdict must collapse to the safe default) and swap the media (output must change). Without these, a "multimodal" system may be silently reading only the surrounding metadata. |
| C6 | Errors will span two very different cost classes: rule bugs vs. model misreads. | Persist per-row observations to JSONL so rule fixes can be re-graded across all 110 rows at zero API cost; batch model re-runs and run them rarely. |
| C7 | `payment` (and any other unobserved class) may appear in the hidden set. | Support all 11 types on their definitions, not on their sample frequency. |

---

## 7. Overfitting Risks Against the Labeled Sample

| # | Risk | Mitigation |
|---|---|---|
| O1 | Memorizing the 30 rows, or any near-duplicate keying on ids/exact strings. | Explicitly banned by E13 ("no hardcoded labels"). No branch may test a `message_id`. |
| O2 | Emitting confidence only in [0.78, 0.91] because that is all the sample shows — producing a system that *appears* calibrated but carries no information. | A6: confidence is a computed function of signal strength, deliberately allowed to exit the observed band. |
| O3 | Collapsing the 11 types to the 10 seen in the sample and never emitting `payment`. | A10: all 11 supported on definition. |
| O4 | Hard-coding `urgent ⇒ notify` from n=4. | A9: left as a strong prior, not an invariant. Only the E10-mandated `scam`/`spam ⇒ mute` is hard. |
| O5 | Inferring a DND rule from the sample — impossible, since **0/30** samples fall in a DND window. Any DND rule justified by "the samples show…" is fabricated. | A2: conservative demotion only; logged as an open question rather than resolved by invention. |
| O6 | Tuning thresholds until the n=30 number peaks, then reporting that peak. | Report both runs and the variance. Treat any gain under ~2 rows (≈7pp) as noise. |
| O7 | Copying sample `reason` sentences as templates keyed to (action, type) pairs. | Reason is generated from the row's own facts; the sample only fixes register and length. |

---

## 8. Open Questions Carried Forward

These must be resolved (or consciously accepted) before the Stage A gate opens:

1. **A2 / DND** — no labeled precedent, 8 rows exposed. Accept the conservative demotion, or research further? *Recommend: accept, and document the reasoning as a known limitation.*
2. **A5 / evidence cardinality** — cap at 2, or emit more under a suspected recall-oriented grader? *Recommend: cap at 2.*
3. **A6 / confidence band** — deliberately exiting the observed [0.78, 0.91] band needs sign-off, since it is a visible deviation from the style reference.
4. **H6 / media legibility** — must be empirically checked in Phase 0/2. If the images are not legible, the entire multimodal branch of the architecture changes.
5. **H10 / temporal coherence** — confirm no evidence candidate postdates the message it explains.

---

## 9. What This Phase Deliberately Did Not Do

- No implementation code, no schema code, no prompts.
- No architecture choice (Phase 5/6).
- No decision tree (Phase 7) — the precedence order in A8 is a *stated resolution to an ambiguity*, and will be specified properly there.
- No full data analysis (Phase 2). The measurements quoted here are only those needed to resolve a specific ambiguity, and each is tagged with its n.
