# **Message Notification Routing**

Build an AI routing agent for WhatsApp that decides which messages deserve immediate attention, which should wait, which should be muted, and which are unsafe.

The agent must reason over **multimodal messages**, including text messages, image posters/screenshots, and voice notes.

WhatsApp is noisy. A user can receive family chats, society notices, school updates, co-worker messages, business account promotions, image posters, voice notes, and scams in the same message stream. Treating every message the same creates two bad outcomes: important messages get missed, and risky messages look normal.

Your task is to build a **message routing agent** for this platform. For every incoming WhatsApp message, the agent must decide whether the user should be interrupted now, whether the message can be batched into a digest, whether it is repetitive enough to mute, or whether it should trigger a safety warning.

The routing decision must be personalized to the receiving user. A sale poster may be useful for one user and unwanted noise for another. A payment reminder may be legitimate from a trusted admin but risky from a new sender. A muted family group can still contain an urgent direct mention. At the same time, clear scam or safety risk should be warned regardless of the user's usual engagement.

## What You Need to Build

Build an agent that reviews each incoming message and decides how it should be handled for that user:

- `notify`: important enough to interrupt now
- `digest`: useful, but can be shown later
- `mute`: low-value or repetitive
- `warn`: potentially risky, suspicious, or unsafe

Your agent should use the provided message, user, group, business, media, and historical interaction data to make personalized routing decisions.

## Files provided

All participant-facing files are inside `dataset/`.

You will receive exactly one prediction input file:

1. `dataset/input.csv` - Incoming messages that your system must route.
2. `dataset/sample_messages.csv` - Example messages with the expected `action`, `message_type`, `reason`, `confidence`, and `evidence_message_ids` columns filled in. Use this only to understand the expected output format and style.
3. `dataset/users.csv` - Basic user notification behavior, such as quiet hours and recent opens, replies, dismissals, and reports.
4. `dataset/groups.csv` - Basic information about each group chat, such as group type, size, admins, and recent activity.
5. `dataset/group_members.csv` - How each user relates to each group: role, activity, read/reply behavior, dismissals, and mute state.
6. `dataset/business_accounts.csv` - Information about business senders, including brand identity, verification, sender domain, account age, and reports.
7. `dataset/user_business_history.csv` - Whether a user has a recent relationship with a business, such as orders, bookings, payments, opt-ins, or opt-outs.
8. `dataset/messages.csv` - Past messages received by users. These help identify repeated patterns, ignored messages, useful updates, and risky content.
9. `dataset/message_events.csv` - How users reacted to those past messages: opened, replied, dismissed, muted, or reported.
10. `dataset/images.csv` - Details for image messages.
11. `dataset/voice_notes.csv` - Details for voice notes.
12. `dataset/daily_notification_summary.csv`  - Daily notification load for each user.
13. `dataset/output.csv` - Blank submission template. Fill this file with your predictions.

Media files referenced by `images.csv` and `voice_notes.csv` are available under `dataset/media/`.

## Input schema

Each row in `dataset/input.csv` represents one incoming message.

Input fields:

- `message_id`: unique incoming message ID
- `user_id`: user receiving the message
- `conversation_type`: `personal`, `group`, or `business`
- `group_id`: group ID if the message is from a group
- `business_id`: business ID if the message is from a business account
- `sender_user_id`: sender user ID if the message is from a user
- `created_at`: message timestamp
- `message_text`: text, caption, or placeholder text for media messages
- `media_type`: `none`, `image`, `voice_note`, or `document`
- `media_id`: linked image or voice-note ID, if present
- `forwarded_count`: forwarding signal



## Required output

For every row in `dataset/input.csv`, generate one row in `output.csv`.

Required columns, in order:

- `message_id`
- `action`
- `message_type`
- `reason`
- `confidence`
- `evidence_message_ids`



## Output meaning

- `action`: final routing decision
- `message_type`: best-fit message category
- `reason`: short human-readable explanation for the decision
- `confidence`: number from `0` to `1`
- `evidence_message_ids`: semicolon-separated historical message IDs used as evidence; use `none` if no useful historical message exists



## Allowed values

`action`:

- `notify`: interrupt the user now
- `digest`: safe but low priority; show later
- `mute`: repetitive, unwanted, or low-value for this user
- `warn`: suspicious, unsafe, scam-like, or impersonation risk

`message_type`:

- `personal`
- `urgent`
- `event`
- `payment`
- `business_update`
- `promotion`
- `greeting`
- `forward`
- `spam`
- `scam`
- `unknown`



## Important Behavior

Your system should make personalized decisions using the full context provided. Similar-looking messages may need different actions depending on the user, sender, conversation, business relationship, and media content.

The final decision should balance usefulness, urgency, repetition, and risk.

## Evaluation

Your `output.csv` will be compared against hidden ground-truth labels.

The scoring will consider:

- correctness of `action`
- correctness of `message_type`
- usefulness and consistency of `reason`
- whether `evidence_message_ids` point to relevant historical messages
- reasonable confidence calibration

## Submission

Submit:


| File              | Description                                                          |
| ----------------- | -------------------------------------------------------------------- |
| `code.zip`        | Full runnable solution, prompts/configs, and README                  |
| `output.csv`      | Predictions for all rows in `dataset/input.csv`                      |
| `chat_transcript` | Conversation transcript showing how you developed or used the system |


These are the must-haves. Beyond that, participants are encouraged to improve retrieval, prompting, evidence selection, confidence handling, batching, caching, or safety logic.
