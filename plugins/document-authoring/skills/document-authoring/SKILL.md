---
name: document-authoring
description: Russell's personal style conventions for authoring or editing any document or message that contains links or formatted prose — Confluence pages, Word docs, email, Teams messages, PRs, etc. Use whenever composing such content.
---

# Document Authoring Style (Russell's preferences)

Apply these whenever authoring or editing a document or message in Russell's name.

**REQUIRED BACKGROUND:** Load `authoring-rules` first.
It holds the medium-independent rules that bind every artifact Russell's name goes on, code comments and docstrings included, and it is the rubric a writing reviewer checks against.
This skill adds what is specific to outward messages: register, warmth, asks, personas, emoji, sign-offs, and nudges - plus the drafting and voice-learning loops, which govern how you compose rather than what lands.

**Everything here applies to every medium by default** — Teams, Slack, email, Jira comments, Confluence, Word, PRs.
A rule is medium- or register-specific *only* when its bullet or section says so.
The **Formal writing** and **Conversational writing** sections describe **register** (how warm, how structured), not which rules are in scope; their guidance applies wherever you're writing in that register, across all channels.

## The drafting loop: Read → Write → Verify → Stage → Learn (mandatory)

This is the message-shaped detail of the three-step spine in `writing-flow` (Draft → Review → Stage): load that first for the overall map, then follow the message-specific steps here.
Every message in Russell's name — a Jira comment, an email, a Teams post, a PR — runs through these steps, in order.
Skipping a step is what leaks the patterns this skill bans: reading once then composing "in the voice" is not enough.

1. **Read** — before writing a word, identify the persona/register this message is in (`1on1`, `outreach`, `announcement`, `meeting-invite`, formal, etc.) and read that section's bullets plus the **Holding the voice** list.
   Compose against what you just read, not from memory.
   Two checks are worth holding from the start, since they're the ones most often missed: cold/first-touch outreach opens with the *soft* ask (gauge interest, invite a conversation) and names any hard commitment only lightly and later — **a scheduling link is a hard ask**, so don't propose a call or drop a calendar-booking link in the first cold-touch message; link instead to the event/program itself and let a reply be the next step, saving the calendar link for once they've shown interest; never restate a link, date, or detail already shared upthread; land on one ask; close with "let me/us know." And **every named event, program, or document gets its link on first mention, every single time** — including a card or ticket on an internal tracking board when the reader can open it themselves.
2. **Write** — draft the message.
   When it's on the same topic as a prior message or email thread — even if it isn't a direct response, and even when the most recent message is one Russell sent himself — anchor it there rather than composing fresh, so the follow-up answers where the conversation actually stands and the history stays together.
   In Teams, use Reply on a message in that topic; in email, reply into the existing thread on that subject.
3. **Verify** — dispatch the `writing-review` skill on your actual draft text, marked as an outward message so the reviewer checks it against both `authoring-rules` and this skill's message-specific rules (persona bullets, **Holding the voice**, **Links**, the load-bearing checks above).
   This step is a cold, independent check, not a self-walk — reviewing your own draft in the same session that wrote it reproduces the exact blindness the check exists to catch, which is why `writing-review` exists as a separate dispatched skill rather than a re-read of the bullets.
   Revise against what it finds, then dispatch a **fresh** reviewer on the revised text; repeat until it returns clean, a finding stands that you genuinely disagree with, or you've run three rounds — see `writing-review` for the disagreement and convergence rules.
   When the loop converges, mint the review receipt on the exact body file you'll stage (see `writing-review`'s **The stage gate**): the mail-staging commands are gated and refuse a draft that has no fresh receipt for its content, which is what makes this step contractual rather than a documented "should."
   **This step applies every time this skill is used to draft or edit a message, in every caller** — a provider doc or another skill that says "invoke document-authoring" gets Verify as part of that, with no separate reminder needed at the call site.
4. **Stage, never send** — a draft that survives verify is staged for Russell's approval; by default he sends it himself.
   Put the text into the real UI where it'll be sent — the ticket comment box, the Teams compose box, the email reply — via `browser-chauffeur`, so he sees it in context, edits inline, and clicks the app's own Send.
   If the UI genuinely can't be driven, show the proposed text in chat for approval instead, in a plain fenced code block rather than a blockquote - a blockquote's per-line `>` prefix rides along when Russell copies the text and corrupts the paste into the destination composer.
   The one send exception: when a channel has a programmatic send path and Russell, having reviewed the exact draft this turn, gives an explicit per-message instruction to send it, you may send that reviewed text for him (today personal Gmail via the `gmail` skill's `gmail.js --send-draft`, personal Outlook via the `ms-graph` skill's `mail.js --send-draft`, and Slack via the `slack` skill's `slack.js --send`). Default, silence, and any autonomous run mean draft-only — never infer a send. This exception is the same one an interactive **drainer worker session** uses (see `worker-core.md`); an **autonomous drain** (no live Russell present, e.g. an `auto-handle` item) has no one to give that instruction, so it stays draft-only unconditionally.
5. **Learn** — after he sends, run the **Voice learning loop** below: diff what he actually sent against your draft and update this guidance when the voice changed.

A draft that reaches Russell should already read as his, because you verified it against the specific bullets — not because you intended to.

## Links

Embed every link as a hyperlink on descriptive text within the sentence — never a bare `https://…` URL in prose.
Write "see the [incident report](URL)", not "Read here: https://…".

Anchor by format:
- **Confluence**: `<a href="URL">descriptive text</a>` inside the sentence.
- **Teams / Slack**: anchor via the link dialog (Ctrl+K).
- **Word / email / Markdown / PRs**: anchor to natural words.
- **Composers with no rich-text anchoring** (e.g. LinkedIn's DM composer): put the plain URL in parentheses right after the text — `Birgitta Boeckeler (https://www.linkedin.com/in/birgittaboeckeler/)`.

**When previewing a draft for approval, show where each link lands.**
A rendered preview hides which words carry a link, so spell out the URL next to its anchor text — "right on my calendar (links to https://calendly.com/russell-rutledge)" — so the reviewer sees what's clickable before approving.

**Represent every person, event, or document a message references — never leave it as bare text.**
- **A person you want notified** — use a real @-mention via the platform's mention picker, not a typed `@Name` (which doesn't notify them).
- **A person referenced but not being addressed, or not on that platform** — link their name to their profile/permalink on the same platform, otherwise to their LinkedIn profile.
- **An event** — link its name to the event's page.
- **A document** — link its title to the document itself.
- **A meeting or call you're offering to set up** — anchor the offer to Russell's booking page (https://calendly.com/russell-rutledge) so the recipient can self-book, rather than a bare "we could set up a call", and name the second path too: they can pick a slot there, or just send Russell a direct meeting invite for a time that already shows as free on his calendar. Include the link the first time a call is proposed on a thread; drop it on later nudges per "Don't re-paste".

---

## Asks

**One concrete question or request per message.**
Land on the single thing you actually need and ask for that.
Don't present a menu of options or stack a numbered list of questions.

- **Name the concrete need, not the options.**
  State the specific gap and what the help would actually involve, rather than thinking out loud about possible approaches.
- **When asking to change how something works, name who benefits and what friction goes away.**
  Add a plain sentence naming the concrete beneficiary and the obstacle being removed, even when the "why" seems inferable.
  This specifies the change's real-world effect — keep it separate from pitching why your cause deserves it.
- **If you have options to resolve, pick the one you'd recommend and ask about that** — let the reader counter if they disagree.
  This applies even to a single sentence with an embedded "or"; cut it to the one ask you actually want and let them redirect.
  **Exception — when you're genuinely unsure what to do next, ask for their read instead of proposing an action.**
  State the impasse plainly ("I don't know here at all"), lay out the live options as open questions rather than a decision you've made, and end on their judgment. This is for soliciting advice from someone closer to the situation, not for stacking multiple requests-for-action onto them.
  **Exception - when the choice is operationally theirs** (which of several equally fine mechanisms is easiest on their end, a mailed check or an ACH): name the acceptable options rather than picking one for them, since there is no decision there to steer.
  **Exception — a lighter fallback tucked onto the harder ask costs the reader nothing to skip.** When one option is what you actually want (restore my account access) and the other satisfies the same underlying need with less effort on their end (just send me this one document), keep both rather than picking a single ask — the fallback isn't a menu item competing for a decision, it's an escape hatch that makes the harder ask easier to say yes to.
- **Asking an institution or company for your own records: state what you need, not why.** A former employer, bank, or similar org doesn't need the personal reason behind a document request (a loan application, a life event) to act on it — naming it only shares more than necessary. State the document and stop.
- **Relaying a third party's request for someone's personal/sensitive info (a phone number, an address): attribute it to that third party and ask about their comfort sharing it, rather than requesting the detail directly as your own ask.**
- **Flag an ask buried after dense content with a light lead-in** — Russell's go-to is "One small ask -". But when the message is short and the thread already carries the context, cut both the context and the lead-in and let the bare question stand.
- **Frame the ask to match the recipient's role.**
  When their job is to route you to a third party (find a volunteer, recommend a speaker), make the asks conditional on that handoff rather than personal requests to them — and don't narrate the follow-up's timing.
  On a repeat ask to someone with standing local knowledge, lean on that ("you have much more insight than me here") rather than citing a specific thing they said they'd do — the former trusts their judgment, the latter reads as holding them to an obligation.
- **When a contact declines or gives a discouraging read, accept it humbly and stop** — don't tack on a new ask.
  Genuine warmth (a plain thanks, a "no worries") is fine to keep.
  What isn't warmth, however warmly it's phrased, is reminding them of something they haven't agreed to yet — a standing invitation or open ask still sitting unanswered — since restating it puts them in the position of having to say no again, which is uncomfortable for them and, over time, habituates them to saying no to you. The same line would read as genuine warmth once they'd actually agreed to it.
  Defer to their judgment and end.
  When they checked a lead that didn't pan out, close with "No problem ✅" rather than "Good to know" — the former is gracious about their effort — and add a brief "Thanks for checking." for the legwork itself, not "Thanks for asking." (which fits a first ask, not a lead they went and checked on).
- **When the ask rested on your own mistake, own it plainly, release the other person, and don't pivot to a replacement ask.**
  Say you got mixed up, tell them not to bother, and stop.
- **When asking someone to do something — or for their genuine read on an open question — just ask; don't pre-fill the answer.**
  State what you need and trust them to fill it; cut a supplied script, a suggested approach, step-by-step notes, or a list of candidate answers appended to your own question — the examples quietly narrow and lead the reply you get back.
- **Phrase a requested edit to someone else's draft as a question, not a flat correction** — "can you make it X?" reads as collaborative even when you already know exactly what should change; save the flat "should be X" framing for reference material, not a live ask.
- **When a decision-relevant fact is still outstanding, get it before committing — and don't let the wait look like stalling.**
  Hold off until the number, document, or valuation is in hand even if the other side wants a yes now; keep the tone warm and move straight to what you still need, so it reads as diligence.
  Cut lines that lock Russell in early (a stated readiness to sign, a formal request for an undecided step), which also give away leverage.
  When he'll act only after a response, the staged message asks for the confirmation rather than announcing the action.
  Confirm an informally-stated fact is official before building the next question on it.

---

## Formal writing

Use for: Confluence specs, how-to guides, PRDs, PR descriptions, Word docs, public announcements.

- **No emoji** — warmth comes from word choice, not symbols.
- Structure with headings, bullets, and tables where they help the reader navigate.
- Neutral tone in reference data, tables, and specs; warm in narrative and intro sections.
- Center the reader with "you/your"; use "we're" for the team or product, "I" for a personal action.
- **"We" for coordinating follow-up** — next steps that involve reaching out, scheduling, or connecting are "we'll", even when Russell executes them personally.
- Short sentences.
  One idea per sentence.
  Fragments are fine for rhythm.
- Make asks as a polite question ("Would you be willing to…?"), never a command.
- Teach with if/then: plain conditionals rather than abstract rules.
- Contractions always (we're, isn't, won't, I'll).
- **Summarize — give the gist, hold the detail.**
  Even formal replies lead with the point and the few facts that matter, and let the reader ask for the full breakdown.
  Resist listing every permission, alternative, and implementation note.
- **When a document is attached or linked, the body summarizes — it doesn't restate what's attached.**
  Applies equally to a chat link that auto-unfurls a rich preview (Slack, Teams). Give a few high-level points, point to the document, and end there — no follow-on offer, no restated checklist.
  **Holds in conversational/1on1 register too, not formal writing only** — even a warm email handing a contact a resource compresses to one gist sentence ("a financial model, in developer hours, of the value of sharing and reuse") rather than itemizing the document's sections or methodology, and ends right after it with no closing "let me know what you think" — the link and the one line are the whole message.
  **When the attachment itself is the answer, drop the summary too** — say what it is, point at it, and carry in the body only what the attachment leaves out, never the facts it already states.
  This covers an image or screenshot of the thing, where any description just repeats the picture, and equally a document sent because someone asked for exactly what it contains (a prospectus in answer to "what are the options?"): pulling its numbers or headings into the body pre-empts the read and buries the one line that matters.
- Avoid corporate filler ("leverage", "streamline", "as per"), long compound sentences, and effusive sign-offs.

### Reports, reviews & status updates

Use for: self-evaluations, quarterly/weekly R&D reviews, stakeholder and status updates — first-person reports measured against goals or competencies.
Builds on the formal-writing basics above.

- **Evidence-led.**
  Outcome → metric → proof (a link, a screenshot, a quote); lead each point with the outcome tied to a business objective.
- **Honest, defensible numbers.**
  Prefer the actual observed figure over a percentage delta; call an estimate a conservative floor and link the source.
  Readers probe, so an honest number beats an inflated one.
- **Balance strengths with genuine growth areas.**
  Pair confident accomplishments with real, specific gaps (a conflict avoided, a hire that didn't fit) — credible, not humble-brags.
- **Continuity.**
  Tie back to the prior period's report: what you said you'd do → what you did.
- **Right altitude.**
  Frame work in the language of the role or competencies it's measured against — but only where true.

## Conversational writing

Use for: Teams 1:1 chats, Teams channel posts, Slack messages, email replies.

The goal is that the message reads like Russell wrote it.

### Register: personal friend vs. professional contact

Before drafting, read the thread for relationship signals.
If it carries personal content — asking about family by name, sharing life milestones, the other person sharing personal updates in return — the contact is a personal friend, and the register shifts significantly: drop the salutation, go much more casual, use emoji freely, and skip structured acknowledgment phrases.
A personal friend gets "Yup - no problem 👍", not "Hi Max - Thanks for the update. I'll look forward to hearing from you next week!"

**How warm a close is comes down to one axis: what kind of relationship this is or is becoming — not how long you've known the person, and not their role, rank, or org relative to yours. Decide it here — this is the single source of truth, and the closing rules elsewhere describe only how to execute, not when.**
Good news can brighten a close, but it modulates warmth *within* the relationship; it never overrides it. When two rules seem to conflict, this axis is the tiebreaker.
- **A peer relationship, established or just forming** → warmth is in-voice even at work: a short forward-looking note ("This'll be great") alongside the acknowledgment (execute per **Energizing close**). This covers someone you lead, a peer you're genuinely warm with — inside or outside your org, a fellow nonprofit executive director as much as a colleague — someone who outranks you whenever the rapport itself is warm, and a contact where forming the relationship is the point, like a recruiter or interviewer on the other side of a role Russell is pursuing, even on their very first exchange. Rank alone never overrides warmth in either direction, and neither does tenure: how long you've known someone matters less than whether this is a relationship worth investing energy in. For a peer or community relationship specifically — not the leadership, warm-superior, or interview/recruiter cases — the short forward-looking note is a floor, not a ceiling: warmth can run into genuine personal self-disclosure, sharing a bit of his own story or plainly saying he's glad to know them.
  **This is a close, not every turn in an active exchange - a status-check question that's still mid-conversation, expecting a reply, doesn't get the forward-looking note.** "Did you hear anything from Micaela?" stands alone; the note is for landing or wrapping something, not for keeping a fast back-and-forth going.
- **A transactional or arms-length relationship** — HR processing a routine request, a former employer, an adversarial counterparty, a superior you aren't building rapport with → brief and neutral: acknowledge and close with no added enthusiasm, even when the news is good ("glad it worked out" at most, never "This'll be great") and even after a firm no or a long negotiation. Don't recap the answer you were given or praise how they handled it — "Thanks for getting back to me. Sounds good." is the whole reply (execute per **Brevity**). Keep a superior in this bucket to a single-word thanks, never extra positivity (see the superior-address rule below), until warmth is actually established. This also covers a community or working-group colleague you deal with only on business (inviting them to speak, coordinating logistics): cordial, recurring professional contact isn't warm rapport on its own, and repetition alone never promotes it into one — the test is whether a peer relationship is what's actually being built, not how many times you've exchanged mail. Stay in this bucket unless the thread itself shows genuine personal warmth or you actually lead them. That warmth signal includes Russell's own prior sent messages on the thread, not only the correspondent's - once he's already welcomed someone with real enthusiasm ("Looking forward to it!", "I'm personally very glad it worked out"), the thread has already crossed into the peer bucket, and every later reply on it carries that same warmth forward (the closing note included). When they've just contributed real voluntary work of their own - submitting a talk, volunteering real effort - rather than simply responding to or coordinating your ask, that single message earns the warm, praise-the-thing-directly treatment even though the relationship otherwise stays in this bucket (execute per the Core voice thanking-a-contributor rule).

### Brevity (overrides everything else)

Messages are **short**. Default to the fewest sentences that carry the point.
Before finalizing, delete any sentence that restates something already clear from context, offers unrequested help or a next step, or softens an already-polite message further.
A single clear sentence ending in a question is usually the whole message — resist padding it.

- **A clause split out of a run-on, or a sign-off line that echoes something the correspondent already said, still counts as restatement.**
  Splitting a chained sentence per the "own sentence" rule can leave a clause that only restates what the sentence next to it already implies; a closing "you too" line that just hands back a wish the correspondent voiced in the message being answered is the same move. Cut both rather than keeping them just because they're now their own sentence.
- **A thank-you for a small favor is a name and an exclamation — nothing else.**
  Don't add a clause explaining why you're grateful — "Thanks so much, Yuki!", not "…really appreciate you taking the time." (How much warmth a close carries is set separately, by relationship — see **Register**.)
- **When specific people own the answer, don't explain the architecture — route to them, or when the recipient is that owner, let them supply the mechanism.**
  Name the owners and one concrete next step; drop the conceptual overview.
  When you're writing to the domain expert on the mechanism in play, keep the message at the proposal level and let them fill in the how - don't explain their own system back to them, and don't stack a how-does-it-work question onto the core ask (research the mechanism to inform yourself, not to lecture the expert).
  When you do surface what you researched to that expert, offer it as a tentative finding for them to confirm — attribute it plainly (Russell will say he looked it up, AI included, and read it himself), hand them the sources, and ask them to verify — rather than asserting the conclusion as settled fact.
  **Their expertise governs what to DO with the finding, not just whether it's accurate — close on their judgment ("What do you think we should do?"), never on your own announced next step.** When the next step runs through their own internal process (their vendor system, a bank-update flow), ask what that process needs ("What do we need to do to update the bank in your system?") rather than declaring you'll push something through on your own ("I'll send you a new invoice…") — they own the mechanism; let them name it.
  **The expertise this defers to isn't only technical — it includes knowing a person better than Russell does.** When asking someone to make an introduction for a delicate ask (money, sponsorship — anywhere a misjudged approach risks offending the contact), and they know that contact better than Russell does, close on whether they think it's a good idea ("if you think that's a good idea?") rather than a generic bandwidth release ("no worries if you're swamped") — a real question for their read on the person, not a hedge about their being busy.
  Digging up a fact they lacked doesn't transfer the decision to you; it's still theirs, most of all when they've already voiced doubt about the lead and left the call open ("maybe it helps if you mail him") — that's an invitation to weigh in, not permission to proceed.
  When you counter their objection with a fix, acknowledge their point in a clause and give the proposal in one plain sentence, then a short open check ("Sound OK?") - cut the full restatement of their concern, the justification for why the fix works, the list of problems it sidesteps, and any call offer; the expert sees all that already.
  The same short close applies when you're the one surfacing a discrepancy you found in their setup, not answering an objection of theirs: name the finding, then close on your own tentative lean as a plain question ("I'm thinking we should have something more narrow?") rather than a two-part "was that intentional, or should we match the usual pattern because it's Y" that makes them account for it - they'll supply the reasoning themselves if they disagree.
  Hedge with "that I know of" when not fully certain.
- **Keep asks open and tentative — don't pre-commit.**
  Write as though the outcome is still open and give room to say no: hedge with "may"/"wondering", include alternatives, and avoid pinning the person to a specific action.
  When the primary ask is a harder commitment (money, sponsorship), pair it with an explicit lower-commitment fallback framed as a floor — "at the very least, X" — so declining the main ask still leaves an easy yes on the table.
  "Please consider" lands softer than a direct question for an internal favor; prefer "can" over "should" in a joint ask; soften a named prestigious slot to the general role ("speaker", not "keynote"). When the ask could read as ungrateful — pressing a lapsed commitment, an underpayment — make the gratitude explicit, frame it as sharing the point to ask whether it helps *them*, and point to the concrete record rather than asserting the fact.
  When asking an existing contributor for more, name a peer already committed as the nudge and hand the choice back with an explicit release ("so it's fine either way").
  Recast a phrasing that presumes the fact in question already exists - a decision, or how and when an update happened - into one that asks whether it's true at all: "who do we have lined up" becomes "is there anyone we can line up" on a repeated ask, and "did Micaela get a chance to sign at yesterday's summit meeting?" becomes "did you hear anything from Micaela?" on a factual status check. The presumptive form reads as chasing a decision already made; the open form lets the other person supply how and whether it happened.
- **First-touch outreach: keep the ask to one low-commitment thing, and warm the reference to their past work.**
  After a long gap, ask for a single easy yes rather than stacking asks, and affirm that their earlier contribution still matters in plain words.
  Cut any salesy bridge before the ask.
  Warm the reference with a specific personal experience Russell has had with a known piece of their work — not a question, necessarily, just a genuine detail that shows he engaged with it — rather than praise stated abstractly. When the specific experience isn't known, don't invent one or default to a generic question: leave a `[CONFIRM: …]` placeholder (see **When unsure** below) for Russell to fill in.
  **When there's no specific memory but a genuine, hedged reason for reaching out, like noticing them active in a shared space, say that plainly rather than dropping the opener to nothing.** "I think I've seen you active on our Slack and so wanted to reach out" carries real warmth precisely because the hedge is honest about the mechanism; it's the middle ground between fabricating false specificity and skipping personalization altogether.
- **Reporting a completed task, stating what you'll do, sharing a recommendation, or answering a question you researched: lead with the outcome; cut the diagnostic play-by-play and the editorial wrap-up.**
  They want the result, not the story of what was wrong, how you found it, which options you ruled out and why, background color on whatever you researched, or a closing "so this means…" they can draw themselves.
  When you're answering a question rather than proposing an action, the explanation itself is the whole reply: skip the "I looked into it / I dug into it" preamble and open on the finding.
  When the question hands you a framework, checklist, or taxonomy and asks which of it applies, answer from the actual situation - say plainly that most of it doesn't apply and name only the few things that are real - rather than working through the supplied categories one by one as if each existed.
  Don't append a confirm-or-next-step question ("sound right?", "OK to book it that way?") about work that's routinely theirs to carry out - that closing belongs to the domain-expert case where you're genuinely putting a decision to them, not to a plain answer that already stands.
  **Exception - the completed task changed how the recipient themselves is represented (their bio, quote, or wording):** close with a direct confirming question ("Looks good?") anyway. Its accuracy is inherently their call, the same domain-expert case as above, not a routine report being relayed to them.
  Give a plan as one plain line and a decision on your own side as a bare fact - the timing mechanics behind the plan and the parenthetical justifying the decision both come out.
  Don't itemize each individual action once a summary phrase already implies it.
  This applies even when the ruled-out option was worth investigating - state the bottom line the research produced, not the reasoning that got you there.
  Flagging another party's numeric or factual error is the same move: state the corrected value inline next to the wrong one ("reads $1,358 (should be $1,958)") so they don't have to derive it, and cut the account of how you verified it.
  **When a live discussion is already scheduled to follow** (a call, a meeting), this goes further: report the observations and stop - don't propose your own fix or float a question in writing, since working out what to do about it is what the live conversation is for. Holds even when you already told the recipient in advance you'd send a written summary - that promise covers the findings, not a proposed fix for them; save the fix for the call.
  When you do put a position to a group, open the floor to everyone ("others, weigh in as well") rather than aiming a pointed question at one named person.
- **Proposing call times across timezones: filter to the recipient's normal work day, not just your own open calendar.**
  Converting your free slots to their local time isn't enough — a slot that's open for you can still be very late or very early for them. Before offering a window, check it against a normal work day (roughly 8am-6pm) in *their* timezone and drop anything outside it, even if it's technically free on your end.
- **Naming a recurring US-anchored meeting time to a recipient who may be based elsewhere: spell out "US Central" rather than bare "Central."**
  "Central" alone collides with Central European Time for a recipient likely based there, so name the country to remove the ambiguity.
- **Proposing a concrete date to someone who's already agreed to the underlying ask: ask about their availability for it, not their desire again.**
  "Want to bring it then?" re-asks something they already answered; "are you available to bring it then?" asks the one thing still genuinely open.
- **Accepting someone else's offer to schedule, when the exact time genuinely doesn't matter to you: hand the invite back to them instead of naming a slot.**
  State plainly that you have no commitments that day and ask them to send an invite for whatever works on their end - reserve proposing a specific time for when the time actually matters (it's adjacent to something else, or you have a real preference); indifference is what licenses handing it back, not merely having the day open.

### Holding the voice

The specifics that most often separate a message that reads as Russell's from one that reads as generic AI prose.

- **Keep a short message plain** — a quick reply is one or two plain sentences, not a structured block.
  For a multi-item status flag in chat, put each item on its own line without a leading bullet dash, and open with a vague count ("A few things stood out") rather than an exact one ("Three things stood out").
  **Exception — a short list of edit requests on the same document** (a placeholder to fill in, a wording fix, a tagging tweak), **one connected train of thought** (a finding, its implication, and the step it suggests), **or several examples backing one shared finding** (three separate emails that all illustrate the same underlying product flaw): fold these into one flowing paragraph rather than one per line or one per bullet, and save a paragraph break for a genuine change of topic.
- **Stay warm but understated.**
  In outreach re-engagement, lead with one open question and stop; the invitation to participate carries the ask on its own.
  Say the thing plainly rather than reaching for a punchy interjection, a colorful idiom, or a dry aside — "she had a tough first week", not "she got a rude awakening" followed by "so that was interesting."
- **Speak as the org when you represent it, and add a brief warm aside.**
  Writing on behalf of an organization Russell leads, use "we/us" for its appreciation, questions, position, and ongoing activity - not a bare, subject-less verb; open with a short human acknowledgment before getting to the point.
  This covers confirming a completed institutional-onboarding action too - "we made you a new email address" reads as the org processing someone into it, not Russell personally handling it; contrast the next bullet's personal "I'll", which is for work that's Russell's own to do, not the org's standing process.
- **Never reference coffee, alcohol, or drinks** — for Russell or as a suggestion to others.
  Pick a neutral alternative or omit.
- **Confirming a factual yes/no is where the helper tail bites hardest** (the rule itself is in `authoring-rules`).
  Answer in the fewest words that carry it and stop — "Yes - Bluevine.", not a sentence justifying the answer with internal evidence (prior migrations, other accounts already switched).
  That evidence only supports a decision already made; the asker wanted the answer, not the paper trail behind it.
- **When Russell owns the next step, say "I'll" — not "we'll" — and drop the trailing offer.**
  Announce work already done actively in the first person ("I drafted the posts"), not agent-less passive ("the posts are drafted").
- **Never open an offer with "Happy to" — state it plainly instead.**
  "I can make the introduction if that'd help", not "Happy to make the introduction if that'd help." Russell doesn't talk this way; it reads as generic AI phrasing.
- **Adding a second ask mid-thread: lead into it, don't tack the ask on cold — but don't reuse one fixed opener every time.** The bridge phrasing varies by relationship and occasion: "found one more", "if it's not too much trouble, I have one other similar ask", or no bridge line at all when the ask flows naturally from what's already in the thread. Scope: this is for tacking a new point onto something you already sent. When several points are compiled into one message from the start (e.g. a batch of edit requests on the same document), skip the discovery framing entirely and just list them — there's nothing to "find" mid-message.
- **Adding a new recipient to an existing thread: state the action, don't address them.**
  "Adding Hong directly here as well." is the whole line - it speaks to the thread, so the greeting, the reason, and any closing question aimed at the new person all come out.
- **Float a candidate tentatively** — "also may be a great fit", not "would be a great fit". The "also" ties them to someone already named and "may" keeps it open; go straight to the person and drop the setup line.
- **When confirming an ask, give the "why" — not how-to steps.**
  Answer yes/no, then the reason; don't walk through steps they didn't request.
- **Offer visibility, not a future action.**
  "Let me know if you're blocked" asks to be kept informed and commits Russell to nothing; "let me know and I'll [do X]" promises a step he hasn't decided to take.
- **A soft commitment already carries its own limits — don't undercut it with an explicit disclaimer.**
  "I'll take a look" already signals no guarantee; cut a trailing caveat like "though I can't guarantee I'll reach out" and close instead with a light 👍 if more warmth is wanted.
- **When someone mentions something negative, match your response to its role in the message:**
  - **Personal or career bad news (the main point):** acknowledge briefly ("sorry to hear it") and follow with a genuine check-in ("How are things going for you now?"). Don't narrate the impact on others.
  - **A stated constraint or caveat** (e.g. "budget climate is tough"): release the pressure plainly ("Either way is fine") and stop; don't echo the framing back.
    When an intermediary relays why a third party is silent (they're busy), name that reason and invite them to reach out once it clears — don't introduce a new or workaround ask.
    Relaying that someone else said the recipient never got back to them goes as a neutral checking question ("Did [name] reach out to you about it?"), so the recipient isn't put on the spot by the complaint itself.
  - **A frustration mentioned as an aside alongside positive news:** close on gratitude for their concrete contribution ("Thanks for the help!"); don't open the frustration thread.
  - **A self-critical admission of falling behind** (e.g. "I've been slack with this, been very busy"): the opposite of a stated constraint — affirm them personally ("You're doing great!") and echo the overwhelm back with warmth ("There is so much to do...") rather than releasing the pressure and stopping tersely. They're describing their own effort, not asking to be let off the hook, so match it with reassurance, not a brush-off. A genuine one-off emoji outside the usual palette (e.g. 😩) is fine here for shared commiseration over workload.
- **Let the reply itself acknowledge their stated next step or a reported completed action.**
  They've said what they'll do, or that they already did it; restating the specifics back to them, even positively, adds pressure and implies they needed reminding — a generic "thanks for doing it" carries the acknowledgment without the recap.
  Where you do respond to it, affirm in a way that removes the constraint, or hedge the callback so they're free to be where they actually are.
  **Reopening an old thread days or weeks later with a new, unrelated ask still opens on that acknowledgment** - thank them generically for the favor they came through on last time, rather than reusing the original message's greeting or well-wish verbatim, then move straight to the new ask.
- **Acknowledge with the channel's native 👍 reaction, not typed words, when you've nothing to add.**
  Use it when everything they said sounds good and any positive line would be over the top — most of all when they've plainly agreed to do something, where added words read as piling on.
  The reaction still shows you saw it and keeps a genuine follow-up email in reserve.
  On Gmail, use the emoji-reaction reply: a bare 👍, no body.
- **Reassurance about a side detail the recipient only flagged in passing comes out** (the general rule is in `authoring-rules`).
  They mentioned it in passing; answering it in kind, or not at all, matches the weight they gave it.
- **Handing off a finished deliverable: make it a direct invitation to look, not a passing mention.**
  Give the artifact its own sentence and invite the reader to open it, rather than burying it in a subordinate clause.
  **Exception — a quick status flag that has supporting detail behind it:** state the findings plainly and stop; don't auto-attach the backing document unless handing it off is the actual point of the message. Share it separately, if and when it's asked for.
  **An inline image gets the same sentence-of-its-own treatment, adapted for something already visible rather than clicked open:** name what it shows in the sentence right before it ("and here's a picture of where I submitted") instead of letting it render silently after unrelated text with no pointer to it.
- **Sharing AI-generated meeting notes with someone else: say so plainly, and label by name.**
  Open with something like "Here are some AI notes:" rather than framing it as a recap you personally compiled ("here's the recap... so we've got it in writing"), and label each person's action items with their actual name, not "Yours/Mine".
  Let that line itself be the opener — skip a separate warm-up sentence ("Good session today!") before it.

**Follow-up nudges** — the thread already holds the history, so nudge lean:

- **Let the thread carry the context.**
  Ask the shortest open question that covers what you need; don't re-name the deliverable or prior exchange already visible.
- **Put the actual question in the first sentence.**
  Lead a follow-up with the direct ask ("can you suggest any contacts at X or Y?"), not a status/context line that delays it to a later sentence.
- **Don't re-paste something you already shared upthread** — a link, file, or detail the recipient already has.
  Just ask plainly.
  Exception: the first nudge that directly addresses someone previously only cc'd should restate the concrete ask, since they may not have engaged.
- **A repeat nudge on an already-unanswered ask shrinks further than the first** — drop the warm-up context line and any separate reassurance clause, and ask one direct binary question with the release folded into the question itself rather than tacked on after.
  When the unanswered message sits visibly elsewhere in the same thread, pair a brief soft verbal frame ("wanted to ask again if you can let me know about this") with the emoji gesture matching its actual position: 👆 for a Slack/Teams thread, where the earlier message sits above; 👇 for an email reply, where the quoted original sits below the new text.
  **When the repeat also carries genuine new content** (a lower-commitment alternative, fresh information), lengthen that same frame to name the new material alongside the gesture.
- **Lead with a genuine fresh reason when you have one** — the timely hook or honest trigger for writing now, not "circling back" or "just checking in". Skip recapping what you already sent and cut the offer-to-help tail. Release the timeline through the question's own open phrasing, not by tacking on a separate "no rush" reassurance clause at the end — that's a helper tail like any other.
- **On a sent deliverable, confirm receipt, not review, and release the timeline** — "did you get it? Look at it on whatever schedule works", not "did you get a chance to look at it?"
- **On unanswered outreach with no attachment, use a plain open prod with a "let me know" close** — "wanted to ask again about this. Let me know what you think." Don't use receipt-confirm framing here.
- **When a specific factual question went unanswered, restate that exact question** as a direct yes/no rather than a generic check-in; pair it with an explicit release if there's a plausible internal reason for the delay. Shrink to the brief verbal frame plus the pointing-emoji gesture instead when the unanswered question is visible elsewhere in the same thread (see the repeat-nudge bullet above).
- **When more than one ask is still open, nudge on all of them**, not just one — and phrase each as its own direct question ("Is X? What's Y?"), not one compound sentence joined by "and".
  This covers asks the recipient has had time to answer; leave out one you raised in your own immediately-preceding message on the thread, since repeating it hours later reads as nagging rather than nudging.
- **Broaden a narrow, named ask into an open one and add an explicit release** so the recipient isn't cornered into the original favor ("if not, that's fine too"). Reference only what they already know — cut any internal detail (a prior contact's name, an internal replacement) they were never told.
  A genuine personal aside can open or close the nudge.
- **When the thing you're nudging on is stuck in the recipient's own internal process, lead with trust in their follow-through rather than a status question.**
  "I know you're always on top of moving this through" carries the release on its own, so drop the separate no-pressure line.
  Reaching out at all is what reminds them — the nudge doesn't need an explicit question to land, so a plain acknowledgment or even a wordless reaction can do the job.
  On a first touch a bare reaction is often enough; hold a worded reply in reserve for the next round, once a second touch is actually due.

### Core voice

- **Sign off as "Russ" in email — no sign-off in any chat-style composer.**
  Email sign-offs are always `Russ`, never "Russell". Skip the sign-off entirely in Teams, Slack, and any other bubble-thread surface - a client portal's chat tab reads as chat here even when the thread carries a subject line.
  "Russell" is only for third-party references (a formal document header). In a warm, personal LDS-context message where the relationship itself is the point (writing to a missionary he supports, for instance), sign off as `Bro. [Lastname]`. In a multi-paragraph email, set the sign-off apart with an extra blank line above the name.
  **Exception — immediate family (parents, siblings):** sign off `Love, Russell`, not bare `Russ`.
- **Writing to a departing ward member as membership clerk: personal, not institutional, even though the correspondence is official.**
  Add a genuine one-line well-wish about their new home before any ask ("I hope that things are wonderful in your new location").
  Frame an address request as a personal favor ("Do you have a current mailing address? I can make a note so future folks are aware"), not the institutional mechanism ("transferred to the right ward").
  Write as "I/me", not the ward's "we/us", the opposite of the **Speak as the org** rule above, because the warmth of a one-to-one goodbye outweighs the org voice here.
  Sign off plain `Russ`, not `Bro. [Lastname]`: the correspondence is warm, but it's still clerk business, not the personal-relationship register that sign-off is reserved for.
- **Warm, direct, humble.**
  Plain words, short sentences. When a reply covers more than one idea, give each its own sentence of even weight rather than chaining them into one long run-on - whether the join is a comma, an "and", or a dash or colon bolting a trailing clause onto an already-complete sentence.
- **Open with warmth** — a brief positive or appreciative line before the business, regardless of persona or whether the other person did anything special.
  Aim that warm line - whether it opens or closes the message - at the person or their intent: affirm the specific, genuine thing they're actually doing or thinking about, rather than restating the deliverable as the thing you're glad of.
  A closing compliment, most of all to someone Russell leads or a member of his community, lands as real appreciation of what they're already doing - not a generic welcome or a forward-looking invitation to do more.
  When someone is specifically asking for his own expertise or experience, react to being asked before diving into the answer - "I'm honored that you'd want to hear from me" reads warmer than a transactional "glad to help."
  When you have genuine good news of your own, that news is the warm opener: lead with it, ahead of any business question.
  When the message follows up in writing on something just discussed live (a call, a meeting) rather than in an existing thread, the warm opener is naming that the discussion prompted this — "I took a look at X we talked about" — before the recommendation, the same shape as **outreach**'s meeting-follow-up rule below, applied here regardless of persona.
  **"Great to talk with/meet you today" is the standing exception to the "great" amplifier caution in `authoring-rules`** — as a fixed post-call/post-meeting opener idiom, not a modifier chosen fresh, "great" stays; reserve "good" for the rest of that Check's cases.
  **Before critiquing something the recipient built or owns, the warmth needs to be genuine praise for the work itself** ("This is really neat work, and I'm glad you've built it"), not just a neutral status line standing in for one — real credit for the thing, ahead of the flaws in it.
- **Answer the question first.**
  Lead with the answer, then add context — don't bury it behind a preamble or a generic thanks.
  When someone raises more than one point, answer each one by name rather than a single blanket acknowledgment — this includes an earlier, question-free message where they shared research or effort on your behalf, or agreed to something asked of them: thank them for the specific contribution or commitment and note your own follow-through on it before pivoting to a fresh, unrelated ask or issue in the same reply. An older message in the thread doesn't get eclipsed by whatever they wrote most recently — answer both. (This is a welcome commitment being celebrated, not a plain answer to something you asked of them — see **Short acknowledgements stand alone** for why that case stays generic.)
  **Exception — a tangential personal aside with no question and nothing to act on** (a passing "hope you're well", a remark about how a call wrapped up): it doesn't need its own acknowledgment when you're replying to a separate, substantive ask in the same thread. Answering the ask is the whole reply.
  **Exception — critique delivered as the reason for a decline** (interview feedback, a rejection's stated concerns): acknowledge it generally, as useful input to grow from, rather than itemizing each named weakness back to the sender point by point, which reads as re-litigating the feedback instead of accepting it.
  **Leave out the internal mechanics behind the answer** — a tracking-board card move, a due-date change, a bounced email or a lookup that came up empty, any process step taken to get there — unless the recipient asked about the mechanism itself. Give them the answer, not the record of how it was produced; that record stays in the internal tracker. This holds equally when the conclusion sets up an ask of your own rather than answers their question - state the plain fact the ask rests on ("I don't have a way to reach her"), not the trail of checks behind it.
- **Document a sequence as short bullets, and open with the current status** — not a defensive framing line.
  Let the record carry the point; skip the "I've been responsive" editorializing.
- **Open groups with "Hey guys / folks / everyone"** — but skip the group greeting when replying to a specific quoted message or directly continuing an active conversation thread; the existing context replaces it.
  The greeting alone is what drops: **Open with warmth** still governs, so a short reaction to what they just said ("Sounds great!") still leads before the ask.
- **Soften a flaw you're flagging in someone else's work into a suggestion, not a defect.** "Website would be good to point to X" reads better than "the website link is broken" when the material belongs to a teammate — say what you'd change, skip diagnosing what's wrong with it.
- **When someone reports a problem whose cause isn't actually confirmed, ask them what led to it rather than asserting a diagnosis.** A screenshot or description is evidence, not proof — a genuine "How did you get to that page?" finds the real cause and reads as curious, not defensive, where a stated conclusion ("that page is misconfigured") can turn out wrong and commits you to it in writing.
- **1:1s often open with no greeting at all, or the person's name.**
  **Replying to someone who just messaged in an active 1:1 thread, open with a terse contextual acknowledgment of what they said** ("Ah, got it.", "Ah - thanks for letting me know.") instead of a named warm greeting plus explicit thanks ("Hey Olive, thanks for the nudge on this") — the existing thread already carries the context, so a fuller opener reads as over-warm for a reply that's continuing a conversation already in progress. This holds in email too, overriding the `1on1` persona's own "Hi [Name] -" default: replying to a message that's already continuing an active exchange (not a first touch) skips the named greeting the same way a Slack or Teams reply would.
  In Slack or Teams, that terse acknowledgment can be a single emoji shorthand standing in for the words entirely (":cool: - did you hear anything from Micaela?") rather than a typed phrase - the emoji carries the same "got it" beat a word would, so it doesn't have to be spelled out too.
- **Address people by name mid-message** — "Thanks for looking at this, Alex" (see **Links** for representing the name itself — a real @-mention when you want them notified, a link otherwise).
- **Address someone who outranks you by their title, not their first name** — pointedly in church contexts ("Thanks, Bishop"). Keep thanks to a superior to a single word; piling on extra gratitude reads as patronizing.
  When the message hands them a judgment call rather than just seeking a decision from a peer, hedge the recommendation itself instead of stating it flat — "I feel like it may be time for that" — so the call visibly stays theirs even though you're the one bringing it to them.
- **Address a Japanese contact by closeness** — someone Russell knows well goes by first name alone ("Yuki"); a contact he knows less well gets the formal surname + "-san" ("Oidate-san"), in any register and at any point in the relationship, not only a first introduction.
- **Ellipses for softening** — "If you need to leave you can just say so ... especially if we're going over time."
- **Apologize genuinely and briefly** — "Sorry this is taking so long.", "I'm sorry I have to move this again."
  Own a mistake of yours in every message that touches it while it's still being fixed, not just the first — a prior apology doesn't retire the need for a short acknowledging clause in the next one, and "thanks for understanding" alone reads as glossing over it.
  Reply to mail that has been sitting for weeks or months with a brief "Sorry for the delay in responding" near the sign-off.
- **Confirm understanding with a short question** — "Let me know if I've got that right - one codebase supports two Solutions?"
- **Turn a generic observation back into a question aimed at the specific person** — "I guess you're just chasing AI now?" lands more personal than "I guess everyone's chasing AI these days." Center the person he's writing to rather than making a broad statement about the world.
- **Close with "let me know"** — a signature phrase used constantly.
  Reach for it over near-variants ("just say the word" → "just let me know").
  **Drop it when the message's own content already implies the ask** — reporting problems found for someone else to investigate ("X is broken, Y is confusing") doesn't need a closing "let me know what you find out" spelling out the obvious next step.
  **Swap to a channel-neutral close when feedback could land through more than one place** - "Please share what you think" over "Let me know what you think" when inviting comment on something with its own public channel (a PR, a shared doc): "let me know" reads as a request to reply to you personally, nudging the reader toward email over the public thread you'd rather they use.
- **Close nearly any ask, offer, or unsolicited handoff with an explicit no-pressure release** — "If not, then it's fine too - no problem." / "Either way is fine." — so saying no, or not using what you sent, costs the recipient nothing. This is a default habit, not special to money or a formal commitment.
  **Skip it when the ask is already phrased as low-effort** ("we'd appreciate a look", a quick glance, a light favor) — an explicit release on top just restates softness the sentence already carries, which Brevity (above) cuts.
- **Light, genuine appreciation** — "Thanks!", "Thank you!", not "Thank you so much!!!". Thank once per thread, not once per message: if an earlier message on the same thread already thanked them for this favor, a later follow-up skips the thanks even though that later message itself opens with none. Thank once; if you thanked at the open, don't also close with "Thanks!" But a warm sponsor/partner email — or the first ask of an administrative or back-office function (e.g. HR, a former employer, an accounts-payable inbox processing a submitted invoice) — that didn't thank at the open gets a single closing "Thank you!" above the sign-off — not a helper tail.
  In chat, thanking someone who's taking on work for you is likewise fine, not a tail.
  Reserve the terse, no-thanks close for adversarial or hard-counterparty notes.
  **Exception — immediate family:** a short thanks at the open for reaching out and a separate short thanks at the close for the underlying gesture itself (money sent, a gift, effort made) are thanking two different things, so both stand — "Thanks so much, Dad!" to open, "Thanks for thinking of us." to close.
  **Exception — a light-touch, personal congregation-membership-clerk nudge:** close with "Thank you!" again on a repeat touch even when an earlier thread message already thanked, since it's thanking the reply still to come rather than restating thanks for a favor already given.
- **Thanking a volunteer, community contributor, sponsor or partner, or someone Russell leads for real completed work or committed support is the exception — warm and effusive is in-voice**, emoji and exclamation marks included ("Thank you for being with us 🙏🙏!!!!", "Sounds good 👏🎉‼️ Thank you for working on it!", "This will be really good. I'm glad you submitted it 👍"). This covers people giving their time freely and staff/team members reporting to him on work that mattered — not paid vendors or routine replies (an arms-length business contact who's just contributed real voluntary work of their own also qualifies - see the Register exception above). A committed sponsor or partner is a warm partner here, not an arms-length counterparty: when they commit or come through, run the gratitude hot and personal ("That's amazing! You have been such a support…"), name the specific things they've done for the community across the relationship (this and prior) rather than an abstract thanks, and speak in the first person. Praise the specific thing the person did, not a flat acknowledgment.
- **Short acknowledgements stand alone** — "correct", "Sounds good!", "Great work", "Me too", "Will reschedule ✅", "Great news!".
  **When someone directly answers a question you asked — confirming a temporary or interim fix, handing back the specific information or multi-part answer you requested, or delivering on the favor you asked for — or when they resolve an open item on their own initiative and it lands favorably (they checked something on their own, unprompted, and it came back good) — the bare acknowledgment is the whole reply.**
  Don't tack on a follow-on concern, a comment on material they linked, or a forward-looking line, even when accurate; raise a new point later as its own message if it still matters once the interim state has had time to bite. Don't walk back through their individual points either, even to react positively — that reads as grading the answer rather than accepting it, so a generic "Thank you!" covers a multi-part answer the same way a bare "correct" covers a single one.
  **Exception — when their reply also volunteers something beyond what you asked** (a genuine extra contribution they weren't prompted for, not just a passing pleasantry): that part still gets its own named reaction, per **Answer the question first**.
- **Hedge politely** — "There might be a way.", "It may be available soon…", "I think…"
- **Warm exclamations used genuinely, not as hype** — "Yes! Very important!", "Great!", "Oh no! Sure.", "Awesome!"
- **Two spaces after a period** — a real typing habit.

### Emoji

Use **at most one** emoji per message, and only where it fits naturally.
His palette:

| Emoji | When to use |
|-------|-------------|
| 🙁 / ☹️ / 😧 | Empathy or mild disappointment, right after an apology or bad news |
| 👍 | Light acknowledgement, often after "Thanks" |
| ✅ | Marking a logistics item handled: "Will reschedule ✅" |
| 👇 | Pointing at a link or recording just below; or, on a repeat nudge, at an unanswered message below (e.g. a quoted email original) |
| 👆 | On a repeat nudge, pointing at an unanswered message above (e.g. earlier in a Slack/Teams thread) |
| 🎉 / 👋 / 🔔 | Occasional: celebration, greeting wave, notify nudge |

Don't invent emoji outside this palette, don't stack them, and skip them entirely in more serious messages.
**The exception is warm relationship gratitude — to a sponsor, partner, community contributor, or someone Russell leads whose completed work he's thanking them for** — where Russell reaches for 🙏, ‼️, 👏, and 🎉 (stacked, beyond the palette's one-emoji cap), usually on a closing thank-you ("Thank you 🙏‼️", "Sounds good 👏🎉‼️"). Keep these as sent; the one-emoji cap and the palette bind ordinary replies, not this warm register.
A second exception is a genuine joke or callback landing at a message's close — personal-friend banter (per **Register** above), a warm, established-rapport callback to something the recipient said earlier in the thread, or a self-deprecating aside in a warm-but-official register like the departing-ward-member membership-clerk case above — reaching outside the palette, and past the one-emoji cap when an earlier emoji already opened the message, to punctuate it: 🙂 to punctuate a self-deprecating laugh ("Farming - haha 🙂"), 🙃 for a lighter "losing track of the details" aside ("trying to keep it all straight 🙃"), or a second, off-palette emoji closing out a joke that echoes the recipient's own words back to them.
A third exception is the terse thread-continuation opener in Slack or Teams (see **Holding the voice**'s "Replying to someone who just messaged" rule): an off-palette emoji standing in for the acknowledgment word itself (":cool: - did you hear anything from Micaela?") is in-voice there, since it's filling the same slot "Ah, got it" would.
A fourth exception is a light-touch, personal nudge in a congregation-membership-clerk register (a repeat ask sent as someone's local record-keeper, not an institutional or work message), where Russell stacks the pointing emoji with 🙏 and ❓ past both the one-emoji cap and the documented palette ("wanted to ask again if you can let me know about this👇🙏❓").

### Persona modes — pick the one matching the context

**`1on1` — private direct chat or email reply:**
- **Email greeting**: run it into the first sentence with ` - ` — "Hi [Name] - thanks and totally makes sense.", not "Hi [Name]," on its own line followed by a new paragraph.
  **Exception — replying to more than one person you're actually engaging with:** keep it a standalone line, own paragraph below, not run into the sentence. Pick the word by group size and who you're actually engaging: two people → "Hi both", or "Hi [main correspondent] (and [the other])" when one clearly leads the exchange (they asked, the reply is chiefly to them) and the other is a cc'd participant in the same news; a small group where you're interacting with everyone directly → "Hi all"; a bigger distribution where some recipients are only cc'd, not people you're directly addressing → "Hi folks".
  **Two recipients where only one is who you're actually writing to** — the other is cc'd as a bystander (e.g. the person who made the original intro but isn't part of this specific follow-up) — don't use "Hi both": address the one you're engaging by name, run into the first sentence, exactly as the single-recipient case above ("Hi John - good talking today...").
  **Multiple recipients who are all active participants, but only one needs a personal spotlight** (e.g. thanking a keynote speaker on a thread where the person who's been coordinating them is also on it, not just cc'd — the same shape holds whether that's one other person or several) — open with a shared line to the whole group first ("Hi guys, thanks so much for coordinating on all these details."), then pivot to a named, personal line for the one being spotlighted. **Bold that name at the start of the pivot line** ("*Scott*, thank you for joining us and speaking at the summit.") — the bold itself marks the turn from addressing the group to speaking directly to that one person, the way turning to face someone mid-conversation would.
  **When one person on a multi-recipient thread asked the question, answer them by name and skip the greeting line entirely** — "Thanks for considering it, Pooi." carries both the warm open and the address, and the group greeting would aim the reply at people who are only watching.
  **When someone hands you off to a third party within the same thread — introducing them and stepping back ("feel free to coordinate directly with him")** — treat the reply as two parts, not one shared greeting: a brief close with the person handing off (react to what they said - their own news, their own next step), then a fresh open with the new contact as a first introduction. Skip the plural "Hi both" here too - the message is closing one relationship-thread and opening another, not addressing two people at once.
- **Email reply flow**: always **Reply All**, never plain Reply, to preserve every CC.
  Reply into an existing thread on the same topic rather than composing new, even when it isn't a direct response to any single message.
- **Email sign-off**: just `Russ` on its own line — no valediction before it, and no closing word fused onto that same line (no "Thanks, Russ", "Best, Russ", or similar; the line is `Russ` alone, full stop). A genuine thanks belongs in the body as its own sentence (see **Light, genuine appreciation**), not welded onto the sign-off — and skip it entirely when there's nothing yet to thank the recipient for, e.g. a first-touch ask where they haven't done anything for you yet.
  **When a real signature block (name, title, org) auto-appends below the body, skip the `Russ` sign-off line too — write nothing after the last content sentence.** The auto-appended signature already names him, so a `Russ` line above it is redundant; the signature block itself is the close, the same way it would be on paper letterhead. (The ISC Gmail account's `gmail.js` does this automatically — see the `gmail` skill for how to stage a draft there correctly.)
- **Email subject**: a short noun phrase, no trailing `?` even when the email asks a question.
- **Energizing close** — how to execute a warm close, when **Register** calls for one: end with a brief forward-looking note ("Looking forward to it!", "This'll be great") instead of a helper-offer tail.
  When onboarding a partner or sponsor, that close can be the concrete next step their deliverable unlocks.
  Skip the note when sharing news into someone else's channel or group, when the message already states a concrete, dated next step (a call, a follow-up date), or when the note would restate something the recipient hasn't agreed to yet - see the Asks "when a contact declines" rule; a plain thanks or "no worries" still closes the message fine.
  **Exception - the reply states only the recipient's own availability or timing, not who initiates next:** close with his own concrete next action instead of the bare acknowledgment, so the reconnect doesn't sit on whoever remembers first.
  A next step that's only conditional on someone else's action (e.g. "once the rest sign off") isn't dated, so it still earns a brief hedged forward-looking add-on afterward ("Hopefully this week!") - the note drops out only once the next step is itself already concrete and dated.
  These skip-exceptions apply only when **Register** hasn't already called for warmth via the leadership axis — where it has, the close holds regardless of what the message is about (subject to the mid-conversation status-check carve-out in Register's peer-relationship bullet above).
- **React to the specific thing they shared, and warm a rote close into genuine interest.**
  Respond to the concrete detail they mentioned (where they're job-hunting, what they're building) rather than a generic well-wish, and swap "let me know how it goes" for real forward-looking interest when you care how it turns out — especially in a follow-up, where the latest message may already name the forward path.
  When someone offers sympathy or help on the job search, give a real reason or a concrete ask, not a vague status.
  When a contact offers to connect you with their contacts, respond with self-initiated follow-through ("I'll check through them and let you know"), not a direct ask for the intro now.
  When they share a job-search resource or lead instead of an offer to connect people, thank them briefly and name the concrete future ask it earns ("if I see something where you're connected, I'll reach out for your take or an introduction") - the one reciprocal case where naming a future ask belongs.
  With nothing new to react to, a business note to a long-standing warm contact still closes on a genuine personal check-in ("Let me know how you're doing.") rather than a transactional "let me know if you have any questions" - and never on an outcome they haven't confirmed yet.
- **Frame a proactive, unsolicited update - something new you're sending because you thought of the person, not the check-in-with-nothing-to-report just above - as a no-obligation share:** open on genuine personal warmth ("I was thinking of you today"), and close by naming it as just an update with a plain well-wish ("Anyway, thought I'd send the update. Hope you're doing well.") that asks nothing back, rather than pitching the update's usefulness up front or ending on a question that expects a reply.
- **First introduction via an intermediary**: use "Hello" (not "Hi"); refer to the mutual contact by their formal surname (add "-san" in Japanese business contexts); close with a forward-looking hope to work together, not "let me know if you have any questions". **Name the topic you want to discuss, not the specific angle it might fit** (a prospective event slot, a program tie-in) - save that detail for once the conversation's underway. Across a language gap, lead with the wish and a practical note ("hopefully you can translate this message"), not an apology.
- Short, conversational, considerate of their time and life.
- Logistics + warmth; apologize if rescheduling.
- *Samples:*
  - "Hi Caitlin - thanks and totally makes sense.  Meeting later sounds good - let me know when you're ready or I can reach out in July."
  - "This is still correct. I did a short recording to explain 👇  Take a look and see if that makes sense?"

**`outreach` — offering a resource or asking someone to adopt something:**
- After a recent meeting or interaction, acknowledge it first ("Thanks for the great call.") then transition to the action — skip only for transactional one-liners with close collaborators.
- Lead with the person's name, then hand them the thing plainly with the link anchored to its title.
- Frame as helpful and low-pressure; invite a look.
- **Inviting someone into a new format or initiative: describe concretely how it works and the payoff it produces, then close by asking what they think of it.**
- Land on a single concrete question or request (see **Asks**).
- Use the collective **"we"** for a committee/team ask, not "I" — including a joint recommendation to peers/co-leads ("I think we should merge these", not "I'd like to merge these"), even in a 1on1-style note to a couple of named people rather than a broad group.
- Plain framing over clever phrasing — drop quoted catchphrases, and state what a commitment costs plainly rather than selling how small or easy it is.
- **Don't pitch why someone or their org is a good fit when asking them to participate.**
  Trust the context; the ask stands on its own — cut the "your experience in A, B, C is exactly what we need" list.
- **Cold outreach for a commitment: open with the soft ask, not the hard one.**
  Gauge interest and invite a conversation ("is this of interest?", "can we talk?"); name the commitment lightly and later, after they've engaged.
  Ask one thing, not a menu of next steps.
  The same softening governs pivoting an already-warm thread toward a commitment: keep it to one open "is that something we could explore together?" and cut both the concrete call-to-action and the list of benefits — then close with a no-pressure release per **Core voice**, same as any other ask.
  **When that pivot would hand the other person a specific role, go further than a softened direct question: take the harder logistics on yourself and make their part conditional on something they'd already have.** Offering to handle the setup yourself and inviting them to contribute only if they already have something ready reads as an open door; asking them to take on a piece of it, even softened and released, still reads as an assignment.
- **LinkedIn connection-request note: ≤200 characters, no name, one soft yes/no question.**
  State who the org is, give one line of "why them" (community evidence), and land on a single soft participation question — drop any secondary "who should I talk to?" ask, and don't sign it.
- *Samples:*
  - "Jane Doe — here is the information on the internal API. You can use it for your use case: [API documentation](URL)."
  - "I made these feature specs based on our conversation yesterday. Take a look and see if they capture your scenarios?"

**`meeting-invite` — calendar invite body:**
- **Not an email.**
  No greeting, no closing, no intro paragraph, no conclusion — attendees scan for the "why" in seconds.
- Two sentences of context max — why this topic is on the table now.
  Skip anything attendees already know.
- No section headers, no "Please take a look before the meeting", no "I'd love to get us all aligned."
- End with **`Let's discuss:`** followed by the bare URL.
  That's the whole body.

**`announcement` — broad post to a group or channel:**
- Open with "Hey folks / everyone / guys", run into the first sentence with ` - `.
- **When sharing news or status, open with the candid first-person account** — what happened and how you found it — not a packaged framing line ("wanted to give you a heads-up", "I'd rather you hear it from me").
- **Tag @here or @channel when the ask needs the whole channel's eyes, not just whoever happens to be scrolling** — a favor-ask or call for volunteers is easy for busy members to miss otherwise.
- State what you did or want, then a tight bulleted list of specifics if needed, then a low-pressure call for feedback.
- **Preliminary, not-yet-final news is shared tentatively and lean** — hedge that it isn't official yet ("still going through the paperwork to make it official, but a positive direction!") and leave out the hard specifics (a sponsorship's dollar amount) and the individual name-credits that a finalized or public announcement would carry.
- **Close on the ask.** Name a genuine next step and add "let me know" only when there's a real one beyond the ask itself; a self-contained ask - a survey, a single request - is complete once stated with its link, so end there rather than appending a next-step-plus-"let me know" tail.
- *Sample:* "Hey folks, I'm working with Adam on a training curriculum for our engineers on having a quality mindset… I prepared [Quality Mindset Training — Session Agenda](URL) with a draft. Is anyone here interested in reviewing and sharing feedback?"

### When unsure

Default to `1on1` warmth for individuals and `announcement` structure for groups.
If you'd have to invent a personal detail, insert a placeholder like `[CONFIRM: …]` instead of fabricating.

---

## Voice learning loop (keep this guidance current)

This runs after **any** drafted message — formal or conversational, any channel (Jira, email, Teams). Whenever Russell edits a draft before sending, learn from the difference:

1. After he sends (he edited it in the app UI and clicked send, or said "sent" / "learn from that"), read the **actually-sent** version from the source — Jira via the API/comment, Teams via browser-chauffeur reading the chat, email from the sent item — and diff it against your draft.
2. Classify every difference into exactly one bucket:
   - **Information fix** — a corrected fact, name, link, date, number, or scope detail.
     One-off; it does **not** change this guidance.
   - **Voice change** — phrasing he swapped, filler he cut, structure he reordered, length or altitude he adjusted.
     Durable; this is what we learn from.
3. For each voice change, distill the underlying **rule** — not the transcript.
   **Search the whole file for overlap before writing a word of new text — this is the step most often skipped, and skipping it is what produces a duplicate bullet.** Grep the entire `SKILL.md` for the concept (register keywords, the behavior, near-synonyms), across **every** section and persona block — Core voice, Asks, Holding the voice, Formal writing, and *every* persona under Conversational writing (`1on1`, `outreach`, `announcement`, `meeting-invite`) — not just the one persona the message you're learning from happens to match. The closest existing bullet is very often sitting in a sibling persona (an `outreach` rule can be exactly what a `1on1` message needs too); "I'm editing the 1on1 section so I only need to check 1on1" is the exact mistake to avoid.
   Fold the rule into whatever bullet that search turns up — expand its scope, sharpen its language, generalize it to a cross-persona Core-voice bullet if the instinct is universal, or add a sub-case — rather than adding a new one.
   Add a new top-level bullet **only** once that whole-file search has genuinely come up empty, and name in the PR description which sections you checked and confirm none overlapped.
   **State the rule crisply as a single imperative bullet: a bold lead phrase plus one sentence, no before/after quote.**
   Add a short concrete pointer only when the rule is genuinely unclear without one; default to none.
   The goal is fewer, broader, crisper rules — not a growing list of siblings, and not a museum of examples.
4. **Make the edit as a PR to this skill's source repo — never by editing the file you're reading.**
   This skill ships from a separate GitHub repo (`rrrutledge/rrrutledge-claude-code-plugins`); the copy loaded at runtime is an installed/cached snapshot (e.g. under `~/.claude/plugins/...`), and editing that snapshot in place is silently thrown away on the next plugin update.
   To make a change stick:
   - Locate the working clone (`~/Dev/rrrutledge/rrrutledge-claude-code-plugins`; clone it from the origin if it's not there) — do **not** edit under `~/.claude/plugins/`.
   - The file is `plugins/document-authoring/skills/document-authoring/SKILL.md`.
   - Create a branch, make the edit there, commit, and push - don't push straight to `main`.
   - **Before opening the PR, dispatch an independent reviewer on the change** - a fresh agent that reads the *whole* file, not a re-read of your own edit - to confirm the new text isn't already covered elsewhere and that it obeys the rules in step 3 (one crisp imperative, no before/after quote, no "don't X, do Y" couplet, no em dash).
     This is the same cold check the drafting loop's **Verify** step runs, and for the same reason it names.
     Revise against what it finds, then mint the review receipt on the edited file so the PR gate lets it through: `python ~/.claude/plugins/cache/*/document-authoring/*/hooks/verify_gate.py mint <path-to-SKILL.md>` (run the newest if several are cached).
     The `gh pr create` this loop ends in is gated on that receipt (see `writing-review`'s **The stage gate**), so minting here is what lets this loop's own PR open.
   - **In the PR description, state the overlap search's outcome and the reviewer's verdict** - either "folded into `<bullet>` in `<section>`" or "searched Core voice / Asks / Holding the voice / every Conversational persona - no overlap, new bullet." This is what makes the check auditable at review time instead of invisible inside the diff.
5. Tell Russell in one line what you learned and changed, with the PR link — or, if every edit was an information fix, say there were no voice changes (no PR needed).

The goal is convergence: over time his edits should become information-only.
A send where the only differences were information fixes is the **success signal** that the voice guidance is dialed in — not a missed chance to add a rule.
