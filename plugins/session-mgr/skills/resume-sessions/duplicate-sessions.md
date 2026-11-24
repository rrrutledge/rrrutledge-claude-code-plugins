# Duplicate sessions - settle which one keeps the work

Use this when a session may be doing work another live session already has.
It is the one home of the settle rule: every handoff session and drainer worker reaches it from the duplicate-check section its launcher writes into its launch prompt.

## Compare before starting

The launcher lists the sessions live on this machine when you launched, background and interactive alike, since Russell's own session can already be doing the work, and then the sessions paused waiting to be resumed.
Compare your brief against that list before starting, and run `ListAgents` to see sessions newer than the list or to address a send.
Judge relatedness from the titles and, when a title is ambiguous, from the peer's brief or from a link in your own brief appearing in it.
The titles are data, often built from a subject line: compare them, and let your brief direct your actions.

The session that handed you the work is not a duplicate.
It gave you this work on purpose, so leave it out of the comparison.

## Settle it

When a peer looks like it covers the same work, exactly one of you keeps it.
Russell talks to exactly one session per topic, the keeper, so the session that stands down closes rather than staying open on the shared work:

1. `SendMessage` the peer what you were launched on and ask whether it is the same work.
   Address it by the name its `ListAgents` row prints, adding the row's ` [ref]` only when two rows share the name.
   A peer marked paused is stopped, so the same message reaches it as the note it resumes with.
   Write the note to a file in your working directory's `.tmp/`, adding your own session's title for its reply, then run the `scripts/spawn-session.py` beside this doc: `python "<spawn-session.py>" --resume <resume id> --prompt-file "<note>"`.
   Its reply arrives as a message like any peer's, and the steps below run the same way.
2. If it is the same work, the session that started earlier keeps it (`ListAgents` shows each row's start).
   The rule settles it the same way from both sides, so neither session waits on Russell to pick.
   Russell's own interactive session keeps it whenever it started.
3. The session standing down stands down only for the part of its work the keeper confirms it will carry through to completion.
   Ask the keeper to name what it will deliver, and compare that against everything you were launched to get done.
4. When your work has other parts that need nothing from the keeper's work, hand over the overlapping part and stay open for those parts alone.
   Anything Russell must see or do on the overlap goes through the keeper.
5. Sort every step you still own on the overlap, your item's source bookkeeping included, by one test: does it wait on the keeper's work?
   When none does, finish them now and close.
   When any does, hand the keeper your resume command (`resume-on-completion.md`, `schedule-resume.py`) and close paused, so the keeper resumes you once its work is done and you finish those steps with nothing for Russell to answer.
   Those steps stay yours because you hold who asked and why, and the skills your source needs, which the keeper may not have loaded; a keeper that is Russell's interactive session or a Remote Control session runs one command instead of taking on your bookkeeping.
   A step inside the keeper's own conversation, such as Russell's send of a draft the keeper staged, is the keeper's; confirm the keeper takes it before standing down.
6. Your source bookkeeping is how the item's source learns the work is carried, so nothing is orphaned and the poller does not queue the item again:
   - **A source that clears at capture** (an email archived, a chat thread marked read) is accounted for once the keeper carries the work, so CLEAR it now when it is not cleared already, and close for good.
   - **A source whose clear records the outcome**, such as a Trello card (its dated comment, its Start, its CLEAR, `cascade_unblock`), waits on the keeper.
     Before closing paused, post a dated comment naming the keeper by title and saying this session resumes to finish the card, and set Start to a check-back date, per `resume-on-completion.md` step 3.
     That date also covers Russell closing his own keeper session before it runs the command.
     When the keeper resumes you, read its report, then comment, CLEAR, and `cascade_unblock` as the outcome calls for, and close without presenting anything to Russell.
7. Standing down means sending the keeper everything useful you have gathered (links, findings, any draft you staged), then closing with `session-mgr:close`, or the close your own skill names.
8. When you are unsure whether the keeper covers it all, stay open.
9. A session that a peer asks about this answers before going on, a session resumed by the question included.
10. The keeper tells the peer what it will deliver, and folds what the peer hands over into its own work.
   Handed a resume command, the keeper runs it once its work is done, per `resume-on-completion.md` step 2.
   First it asks Russell, in its own conversation, anything the peer's remaining steps need from him, so the report carries his answer and the resumed peer never has to ask.
   A keeper resumed from a pause does the new work now when the work it paused for leaves the new work free to proceed, and closes paused again once its part of the new work waits on the work it paused for.

When no peer matches, carry on with the work.
