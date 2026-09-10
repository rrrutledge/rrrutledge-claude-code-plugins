---
name: mail-filters
description: Russell's calibrated strategy for mail filters/rules that auto-archive never-process mail at the mail server, before it ever reaches the drainer or the inbox. Use when deciding whether a piece of junk deserves a filter and, if so, what phrase that filter should match and how to create it — in Gmail (filters) or Outlook (rules). Teaches the phrase-selection craft (broad enough to recur across senders, strict enough to never bury wanted mail) and the per-platform create/delete mechanics.
---

# Mail Filters — auto-archive the never-process mail before it reaches you

This skill captures a strategy Russell has tuned by hand for over two decades, most heavily in his
personal Outlook. Its job is narrow and high-leverage: decide when a piece of mail belongs to a whole
**category that never needs processing**, and encode that as a server-side filter so mail of that type
is archived automatically — never surfacing in the inbox, never reaching the drainer.

Archiving is not deleting. An archived message stays fully searchable; the filter only means "don't
actively show me this or notify me about it." So a filter is safe, reversible config — you can always
find an archived message later, and you can always delete a filter.

## The one decision this skill owns

In the age of a drainer that processes mail immediately, incoming mail has three states:

1. **Auto-archive at the mail server** — never needs processing; filtered out *before* the drainer
   sees it. **This skill owns exactly this line.**
2. **FYI** — reaches the drainer, surfaced as informational, no action needed.
3. **Needs work / act now** — reaches the drainer, requires action.

The split between (2) and (3) is the **drainer's triage** layer, not this skill's. This skill answers
only one question: **does this mail ever need to reach the drainer at all?** When the answer is no for
a whole category, a filter removes that work entirely — which beats processing it quickly, because the
best per-message cost is zero.

## The craft: choosing the phrase (the heart of the skill)

The whole strategy turns on one skill: **never build a filter for a single sender's single message.**
If one company sent this type of mail, others will too, now and in the future. So the filter should
catch the **type**, across every sender — and the way it does that is by matching a **phrase**, chosen
with judgment. It follows that **the phrase never contains a company, bank, product, brand, or person
name** - a name identifies one sender, so a rule built on it is exactly the single-sender filter this
rule forbids.
When the only distinctive text a piece of junk offers is its brand name, the type isn't filterable: stop
it at the source (unsubscribe, or turn the notification off at the sender), never a brand-named rule.

### The two-sided test

A candidate phrase has to pass both sides at once:

- **Recurrence** — would this exact phrase plausibly appear in the *same type* of notification from
  **other** companies, or in future mail of this type? If it is specific to this one company, pick a
  more generic fragment; if none generalizes, this type isn't filterable and stays in the inbox.
- **Safety** — could this phrase **ever** appear in a message you *would* want to see? Because every
  rule is fenced to corporate mail (the master fence below), you only have to picture a plausible
  good-mail example from **another corporate sender** — a wanted transactional or business mail. If you
  can construct one, the phrase is too broad.

When the two sides conflict, **tighten**:

1. **Lengthen the fragment** until the safety side passes - usually to a distinctive multi-word phrase,
   and to the body when the subject can't carry one (see the escalation below).
2. **If no subject-or-body phrase is safe on its own, don't filter that type** - leave it in the inbox
   for the drainer to triage. A filter only ever matches a subject phrase or a body phrase, both fenced
   by the master domain exclusion; it is never scoped to an individual sender. Scoping to one sender lets
   the same junk from every other sender slip through, which defeats the whole point of catching the type.

Watch for the inverse trap: a subject can *read* like a clean, self-sufficient phrase yet bury exactly
the mail you most want to see. A person's name (`Russell`), `Invoice`, `Document shared`, `receipt` all
look like tidy standalone subject matches - but they'd bury recruiter and personal mail, a real bill, a
genuine shared file. There's no safe way to catch these as a type, so don't: leave them in the inbox.

Erring strict is deliberate: a too-narrow phrase that misses a variant costs you one more sibling
phrase later; a too-broad phrase buries real mail. This is why the real rule sets contain near-duplicate
phrasings — variants captured over time, rather than one phrase broadened past the safety line.

### The method, from a single junk sample

1. **Name the type, not the sender.** Ask "what *kind* of machine notification is this?" — delivery
   confirmation, policy-change announcement, receipt, one-time passcode, auto-reply, statement-ready.
2. **Find the intrinsic boilerplate — subject first, body on escalation.** Scan the subject first for
   the words the *sending system* emits by template — the words that are there because a machine
   generated this notification, not because of this particular company — since a subject phrase makes
   the simplest single-mechanism rule. Escalate to the body when the subject can't deliver one: either
   it's too generic to mean this type at all, or the only distinctive words are welded to the company's
   own name (see the body-escalation worked example below). The body holds far more text and usually
   carries a distinctive automated-boilerplate phrase (a "you're receiving this because…" line, a
   bulk-sender footer marker, a mailing-platform signature) that marks the type regardless of who sent
   it. Wherever it's found, prefer a distinctive multi-word fragment over a single common word.
3. **Run the two-sided test** on the candidate.
4. **Tighten until safe**, accept that variants may slip through, and plan to add sibling phrases as
   they surface.
5. **Pick the mechanism that fences it** for the platform (next section).

### Worked examples

The canonical lesson is the phrase that started this skill. A rule matching **`privacy policy`**
matched almost every message in the mailbox, because "Privacy Policy" sits in nearly every marketing
and transactional footer. The fix targets the boilerplate of the *announcement itself* — the words a
human never writes and only the notification carries:

| Junk type | ❌ Too broad — buries good mail | ✅ Calibrated — intrinsic system boilerplate |
| --- | --- | --- |
| Policy / terms change | `privacy policy` (every footer) | `updated our privacy` · `we're updating our` |
| Delivery | `delivered` (a person says it) | `Your shipment was delivered` · `out for delivery` |
| Receipts / orders | `receipt` · `order` | `Your … receipt` (subject shape) · `Thanks for your order` |
| Sign-in codes | `code` | `One Time Passcode` · `Code for signing in` · `verification code` |
| Calendar responses | `meeting` | `Accepted:` (subject-prefix convention) |
| Auto-replies | `out` | `Automatic reply:` · `I am out of the office` · `OOO Re:` |

The through-line: match the phrase the sending machine puts there by template, as a distinctive
fragment, scoped to where it is reliable.

**The body-escalation case, worked.** A subject like "Your Account with TPWD Has been Updated" tempts
you to lift the surrounding words as the phrase — but `Has been Updated` alone buries good mail ("Your
job application status has been updated"), and the only way to keep the fragment safe is to keep the
company name in it, which fails the recurrence side: the next company's version won't say "TPWD." No
subject fragment threads that needle, so escalate per step 2 above: drop to the **body**, where
account-update notifications carry a template sentence the subject doesn't show - `account has been
updated with your requested changes`. That full template sentence recurs across companies (the safety
net for the next one) and is distinctive enough to stay safe on its own in the bodies bucket, fenced by
the master domain exclusion - no human ever writes it to you.

## Organizing rule: one bucket per mechanism, not per topic

Group filters by the **mechanism that catches the mail — the field you matched and the action you take
— not by the topic of the mail.** Whether a batch of subjects is "shipping" or "payments" is
irrelevant; what they share is that they are *subjects you archive*. So the whole strategy collapses to
a handful of rules keyed by mechanism:

- all **subjects to archive** → one rule
- all **body phrases to archive** → one rule (fenced — see the safety mechanisms below)
- the parallel **subjects to delete** / **bodies to delete**, if you delete rather than archive some

Fewer rules is the goal, for one concrete reason: when you find a new junk phrase you already know the
two things that place it — which field it lives in (subject or body) and what you want done (archive or
delete) — so you know the exact rule to open and append to, with no "which category is this?" decision.
A new rule is created only for a genuinely new *mechanism*, never a new topic.

A rule grows until it hits the platform's query/condition length limit, then spills into a numbered
sibling of the **same** mechanism (`… Subjects 2`, `3`). The number is pure length overflow, never a
category.

## The master fence: only corporate mail is ever archived

Every archive rule is implicitly scoped to **corporate mail**, and *corporate mail is any message whose
sender domain is not one of your known personal-mail domains* — `gmail.com`, `outlook.com`,
`hotmail.com`, `yahoo.com`, `icloud.com`, `live.com`, `comcast.net`, `aol.com`, plus your family and
school domains. Mail from a personal domain is **never** archived, no matter what its subject or body
says. This is the guarantee that lets every phrase match be broad: the fence catches the case where a
real person happens to write a junk-looking phrase.

Carry this fence on every broad rule, so no per-phrase distinctiveness judgment is load-bearing — the
fence is. Each platform expresses it below (Gmail's `-from:(…)` negation, Outlook's "except when the
sender's address contains…" exclusion).

## Platform mechanics — how each platform fences a broad match

Both platforms solve the same tension — broad enough to not need a filter per company, narrow enough
to never archive good mail — with the master fence plus the tools below. Match the shape to whichever
mailbox the junk hit.

### Gmail — subject-scoped buckets, consolidated with OR-lists

Gmail's fence is **subject-scoping**. A Gmail filter matches a search query; scope the query to
`subject:(…)` so a phrase in a footer or quoted body can't trigger it. Reserve a body match
(`has the words`, i.e. an unscoped query) for a phrase so distinctive it could not appear anywhere you
care.

A single Gmail filter can hold **many phrases**, OR'd together inside the parentheses — so a whole
mechanism lives in one filter:

- **Subjects-to-archive rule** — every archive-worthy subject phrase in one filter, closed with the
  master fence:
  `subject:("One Time Passcode" OR "Your shipment was delivered" OR "Automatic reply:" OR "Accepted:" OR
  …) -from:(gmail.com OR outlook.com OR icloud.com OR <your family and school domains>)` → Skip Inbox,
  Mark as read. A new phrase, whatever its topic, is appended with ` OR "…"` — the German, Dutch,
  French, and Spanish forms of an auto-reply or delivery notice are just more siblings in the same list.
  Spill into a second filter only when the query hits Gmail's length limit.
- **Bodies-to-archive rule** — the parallel filter for phrases matched anywhere (an unscoped query),
  carrying the same `-from:` fence:
  `("<distinctive body phrase>" OR …) -from:(gmail.com OR outlook.com OR icloud.com OR <your family and
  school domains>)`.
- **Action:** for pure noise, **Skip the Inbox (Archive it)** plus **Mark as read**. For mail you want
  archived but not marked read, just Skip the Inbox.

### Outlook — consolidated buckets fenced by a sender-domain exclusion whitelist

Outlook rules can hold many conditions and run top-to-bottom, which enables a richer shape:

- **One rule per mechanism, numbered only for overflow.** All archive-worthy subjects live in the
  subjects rule ("Corporate Subjects"), all archive-worthy body phrases in the bodies rule ("Corporate
  Bodies") — the names mark the *mechanism* (subject match vs. body match), not a topic. When a rule
  hits Outlook's condition-length limit, spill into the next-numbered sibling of the same mechanism
  ("Corporate Subjects 2", "3", …). Adding a new junk phrase means appending a string to the matching
  rule, not creating a new one.
- **The master fence, in Outlook form.** Every broad subject-or-body bucket ends with *"…except when
  the sender's address contains:"* the known personal-mail domains — this is how Outlook expresses the
  corporate-mail-only fence above. Keep the exclusion list on every broad bucket.
- **Body phrases go in the bodies bucket, unpinned.** A distinctive body phrase - one no wanted mail
  could plausibly carry, like `Sign in with this one-time passcode` - goes into the general bodies bucket
  ("Corporate Bodies") with the master fence, exactly as a subject phrase goes into the subjects bucket.
  A body phrase that isn't safe on its own doesn't get pinned to a sender to rescue it - it just isn't
  filtered, the same as an unsafe subject phrase.
- **Native message-type conditions.** Outlook can match *"the message is a Meeting Response"* or
  *"…Meeting Request"* directly — archive calendar accept/decline noise without matching any subject.
- **Positive keep-in-inbox overrides, placed first.** A short list of rules that force wanted mail to
  stay — a known human sender, a specific subject you always want to see — placed **above** the broad
  archive buckets and set to *stop processing more rules*, so an allow beats a later broad archive.
  **Ordering matters:** the allow rules and the most specific rules sit at the top; the broad buckets
  sit below them.
- **Homoglyph catch.** Spam that disguises words with lookalike Unicode letters gets a dedicated
  subject rule matching those confusable characters.

## The deterministic checks

The two-sided test is a judgment call, but three of the rules the craft rests on are decidable from the
phrase and its rule text alone. **Apply them as you choose the phrase - they are guidance for writing the
rule, the same as everything above** - tightening the phrase whenever one is broken. The reviewer applies
the same three cold before the rule is shown to Russell, the way `document-authoring` pairs its
`authoring-rules` rubric with the `writing-review` reviewer.

- **No single-sender token.** The phrase contains no company, bank, brand, product, or person name (see
  "The craft"). This is the highest-value check, the one the writer's blindness most often lets slip.
  **Check:** a capitalized proper noun mid-phrase, an all-caps or mixed-case brand or acronym (KBB, IMDb,
  TPWD), or a known company / product / person name. Innocent: a generic type-word that happens to be
  capitalized - a subject-prefix convention (`Accepted:`, `Automatic reply:`) or a template word
  (`Passcode`, `Receipt`) that names the *kind* of notification rather than the sender, and
  sentence-initial capitalization.
- **Phrase scoped to the field it lives in.** The phrase matches where it actually occurs (see "The
  two-sided test" and "Platform mechanics").
  **Check:** a template sentence lifted from the body but proposed as a subject phrase, or a single common
  word proposed as an unscoped body match.
- **Master fence on every broad bucket.** The rule text carries the personal-domain exclusion (see "The
  master fence").
  **Check:** a broad subject-or-body archive bucket whose rule text has no `-from:(…)` negation (Gmail) or
  "except when the sender's address contains…" exclusion (Outlook).

The cold reviewer that applies these is the **`mail-filter-review`** skill. It runs once, at the
show-literal-rule gate (see "Wiring the drainer" below) - the single checkpoint every rule passes through,
whether the phrase started here, in the drainer's junk-learning, or in the digest. Nothing else has to
invoke it: reaching the gate is what runs it.

## Creating and deleting a filter

Both platforms are managed programmatically, each through its own skill's REST wrapper — Gmail through
the **`gmail`** skill's `filters.js` (the Gmail settings API, OAuth), Outlook through the **`ms-graph`**
skill's `mail.js` (the Graph rules API). Each returns and writes the full, untruncated filter, so a whole
mechanism's OR-list stays intact.

### Gmail (filters)

Manage Gmail filters through the **`gmail`** skill's `filters.js`, which wraps
`users.settings.filters`. It uses the gmail skill's OAuth token (the `gmail.settings.basic` scope granted
at sign-in) that must be signed in once — see the gmail skill's **Filter management** section.
The token is account-specific, so the mailbox it touches is whichever account the sign-in used (for ISC,
`russ@innersourcecommons.org`).

- **Read all filters:** `node filters.js --list-filters` (add `--json` for the raw objects, each with its
  `id`). Do this first when consolidating, so you're editing the current truth.
- **Create a bucket:** `node filters.js --create-filter --query='subject:("A" OR "B") -from:(gmail.com OR outlook.com OR icloud.com OR <family/school domains>)' --archive [--mark-read]` — `--query` is the raw
  Gmail search, so the OR-list and the master `-from:` fence go in verbatim; `--archive` is Skip the
  Inbox, `--mark-read` also marks read.
- **Append a phrase to a bucket:** `node filters.js --append-filter=<id> --add-subject='"D"'` (splices
  inside `subject:(…)`), or `--add-body='"E"'` (splices inside the leading `(…)` group) — the everyday
  "add to the right bucket" op. Gmail can't edit a filter in place, so this reads the query, splices, then
  deletes and recreates the filter, and the **id changes** (the command reports the new one).
- **Delete a filter:** `node filters.js --delete-filter=<id>`.

### Outlook (rules)

Manage personal-Outlook rules through the **`ms-graph`** skill's `mail.js`, which wraps
`/me/mailFolders/inbox/messageRules` and returns and writes the full, untruncated rule.

- **Read every rule:** `node mail.js --list-rules` (add `--json` for the raw objects).
- **Create a bucket:** `node mail.js --create-rule --name="Corporate Subjects" --subject-contains="A||B||C" --except-from="gmail.com||outlook.com||icloud.com||<family/school domains>" --move-to=archive [--mark-read]` — `--except-from` is the master fence; multi-value flags split on `||`; the run-order sequence is auto-assigned to the end.
- **Append a phrase to a bucket:** `node mail.js --append-rule="Corporate Subjects" --subject-contains="D"` — the everyday "add to the right bucket" operation.
- **Delete a rule:** `node mail.js --delete-rule="<id or name>"`.

## Wiring the drainer

The drainer's per-provider `JUNK-LEARNING` step defers here for the breadth decision instead of
re-deriving it each time. When the drainer reaches the filter step for a piece of junk:

1. **Generalize on sight.** Run the phrase-selection method above on the single sample — name the type,
   find the intrinsic boilerplate, apply the two-sided test, tighten until safe. Propose the *type*
   phrase, not a filter for this one sender.
2. **Append by mechanism.** Decide the field (subject or body) and action (archive or delete), then
   append the phrase to the one existing rule for that mechanism — the subjects-to-archive filter, the
   bodies-to-archive rule — rather than spawning a new single-phrase filter. Create a new rule only for
   a genuinely new mechanism, or a numbered spillover when the current one is full.
3. **Review, then show the exact rule, and create only on his explicit OK.** First run the cold review:
   load the **`mail-filter-review`** skill (call the Skill tool) on the proposed phrase(s), the field
   each is matched in, the action, and the rule text they land in, and revise until it comes back clean
   (generalize a flagged company/brand token to a type-level phrase, move a phrase to its real field, add
   the fence; a token you cannot generalize away means the type isn't filterable - stop it at the source
   instead of a brand-named rule). Then show Russell the **literal rule** he is approving: the exact
   phrase(s) it will match, which existing bucket/rule it lands in (or that it's a new one), and the
   action it takes (archive / mark read). Wait for his OK **on that shown text** — seeing it is what
   lets him strip a company name down to a type-level phrase, or catch that the item shouldn't be a rule
   at all (anything he *acts on*, like an autopay re-enrollment reminder, is a needs-you, not
   archive-fodder). Only once he approves the rule as shown do you run the create/append command. A
   filter is reversible, searchable config, so there's no need to leave the approved rule as a manual
   to-do — but the approval must be on the concrete rule, not the idea of one. (This is distinct from
   outward messages, which are always staged for Russell to send himself; a filter is inbound config he
   can undo.)
