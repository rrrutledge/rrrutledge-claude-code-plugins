// Tab registry — one small file per open tab, in a directory every Claude
// session shares, so a later launch can reclaim tabs whose owning session has
// ended.
//
// WHY IT EXISTS: connectOverCDP auto-attaches to every open target. When a
// session ends before its tabs are closed, they stay open forever. Over many
// sessions these pile up and eventually wedge connectOverCDP. Recording each tab
// against the SESSION that opened it lets the sweep in chauffeur.py keep a tab
// alive exactly as long as that session's window is open and reclaim it when the
// window closes — never an active session's tab, and never a tab the user opened
// (those are never recorded here). The owner is the session, NOT the ephemeral
// node script: one session fires many short-lived scripts (act, screenshot,
// retry), so the tab must outlive any single script.
//
// WHY ONE FILE PER TAB: every writer touches only the file for the tab it is
// acting on, so concurrent sessions never contend and no writer can drop
// another's tab. Keeping all tabs in one shared file cannot offer that —
// recording a tab there means reading the whole list, appending, and writing it
// all back, so two sessions doing that at the same moment both start from the
// same list and the second write silently discards the first one's tab. Making
// that safe takes a lock, and a lock brings both contention and an awkward
// question: what should a writer do when it cannot get one? Splitting the state
// removes the question instead of answering it. A file holds exactly one tab, so
// there is nothing of anyone else's in it to lose, and even two writers racing
// on the same tab's file are both writing that same tab's current state. Reads
// pay a directory scan rather than a single open, which at a couple of dozen
// tabs is not measurable.
//
// LAYOUT: ~/.claude/browser-chauffeur/tabs/<ownerPid>-<targetId>.json
//   filename — the owner (a PID, or `unowned` for a tab nobody claimed) and the
//              CDP targetId. Both live in the name so the ownership questions
//              ("which tabs are mine", "whose session has ended") are answered
//              by listing the directory, with no file read at all.
//   mtime    — last activity. Marking a tab active is a utimes call, which
//              changes no content, so any observer can do it without reading or
//              rewriting the file.
//   content  — {targetId, ownerPid, url, title}, for the sweep's URL/title
//              change check and for reading the state by hand.
//
// REQUIRED USAGE — always use the bundled openTab/closeTab so opening and
// closing a tab are mechanically inseparable from recording and removing it.
// Never open a tab with bare context.newPage(): an unrecorded tab is invisible
// to the chauffeur.py orphan sweep, so it leaks until the age/count backstop
// reaps it or the browser crashes under the accumulation.
//
//   const { openTab, closeTab } = require('browser-chauffeur-helpers');
//   const page = await openTab(context, 'https://example.com');  // creates + records (+ optional goto)
//   try { /* work with page */ } finally { await closeTab(page); } // closes + removes
//
// closeTab parks (about:blank) instead of closing when it's the browser's last
// tab, so it never accidentally exits the persistent browser.
//
// Tabs a page spawns by itself (a click that opens window.open / target=_blank /
// ctrl-click) are captured too: openTab/findTab attach a page-scoped 'popup'
// listener that records the new tab under the same session. That listener is on
// the specific page, so the popup is by definition a child of a tab this session
// owns — unlike a context-level 'page' listener, which fires for every new tab
// in the shared browser with no way to tell which one you opened.
//
// OWNER-SCOPED REUSE — the only way to get a tab is openTab (a fresh tab you
// own) or findTab (one you ALREADY own). findTab returns a tab only when this
// session owns its file; a tab another session opened, or one the user opened by
// hand (never recorded), is never returned — findTab yields null so the caller
// opens its own. So a session can never navigate a tab it doesn't own and
// clobber another session's work. Only openTab and popup registration ever
// create ownership; findTab never adopts a tab it finds.
//
// registerTab/unregisterTab remain exported as the lower-level primitives, but
// prefer openTab/closeTab.
//
// Every write is atomic (write-temp-then-rename) and every read tolerates a
// missing or corrupt file.

const fs = require('fs');
const os = require('os');
const path = require('path');

const TABS_DIR = path.join(os.homedir(), '.claude', 'browser-chauffeur', 'tabs');

// A CDP targetId is hex, so it is always a safe filename component.
const NAME_RE = /^(unowned|\d+)-([0-9A-Fa-f]+)\.json$/;

function tabFile(ownerPid, targetId) {
  return path.join(TABS_DIR, `${ownerPid == null ? 'unowned' : ownerPid}-${targetId}.json`);
}

function parseName(name) {
  const m = NAME_RE.exec(name);
  if (!m) return null;
  return { ownerPid: m[1] === 'unowned' ? null : Number(m[1]), targetId: m[2] };
}

function listNames() {
  try {
    return fs.readdirSync(TABS_DIR);
  } catch {
    return [];  // directory not created yet
  }
}

// Merge updates into a tab's own file. Reading it first preserves fields this
// writer doesn't know (the sweep records `title`, the JS side doesn't), and is
// safe without a lock because the file describes exactly one tab: a racing
// writer is writing that same tab's current state, not a list it could truncate.
function writeTab(ownerPid, targetId, updates) {
  fs.mkdirSync(TABS_DIR, { recursive: true });
  const file = tabFile(ownerPid, targetId);
  let existing = {};
  try {
    existing = JSON.parse(fs.readFileSync(file, 'utf8')) || {};
  } catch {
    // new tab, or unreadable — start clean
  }
  // Unique temp name per process so concurrent writers don't clobber the temp.
  const tmp = `${file}.${process.pid}.tmp`;
  try {
    fs.writeFileSync(tmp, JSON.stringify({ ...existing, targetId, ownerPid, ...updates }));
    renameWithRetry(tmp, file);
  } finally {
    // A rename that never happened would otherwise strand the temp on disk.
    try { fs.unlinkSync(tmp); } catch { /* renamed away, as expected */ }
  }
  return file;
}

// Windows refuses a rename onto a file another process currently has open. Left
// at a single attempt that surfaces as a silently dropped write, since callers
// treat recording as best-effort — so retry across the moment a concurrent
// reader holds the file.
function renameWithRetry(tmp, file, attempts = 5) {
  for (let i = 0; ; i++) {
    try {
      fs.renameSync(tmp, file);
      return;
    } catch (e) {
      if (i >= attempts - 1) throw e;
      sleepSync(20 * (i + 1));
    }
  }
}

function sleepSync(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

// Mark a tab active without touching its content.
function bumpMtime(file) {
  const now = new Date();
  try {
    fs.utimesSync(file, now, now);
  } catch {
    // file gone — the tab was reclaimed under us, nothing to mark
  }
}

// The owner of a tab is the Claude session that opened it — recorded so the
// sweep keeps the tab alive exactly as long as that session runs and reclaims it
// when the session ends. The owner is CLAUDE_PID: the session's own claude
// process, which Claude Code injects into every tool subprocess. Its key property
// is that it is the SAME pid across all of a session's tool calls — each tool call
// runs in its own short-lived process, so this script's `process.pid` would differ
// call to call and make tabs look reclaimable the instant the opening script
// exits; CLAUDE_PID is the one pid stable for the whole session, and it lives
// exactly as long as the session, so a tab is reclaimed precisely when the session
// ends. This owns tabs uniformly for every kind of session — a launched tab, a
// headless `--bg` drainer worker, or a `claude` you started in a terminal yourself
// — with no environment to set up. Only when CLAUDE_PID is absent (not running
// under a Claude session at all) does ownership fall back to this short-lived node
// process, so the tab is then reclaimed soon after this script finishes.
function ownerInfo() {
  const claudePid = Number(process.env.CLAUDE_PID);
  if (Number.isInteger(claudePid) && claudePid > 0) return { ownerPid: claudePid };
  return { ownerPid: process.pid };
}

// Every tab this session owns, as targetId -> { file, mtimeMs }. Built from
// filenames, so the only reads are the stats needed to order by activity.
function ownedTabs() {
  const { ownerPid } = ownerInfo();
  const owned = new Map();
  for (const name of listNames()) {
    const parsed = parseName(name);
    if (!parsed || parsed.ownerPid !== ownerPid) continue;
    const file = path.join(TABS_DIR, name);
    try {
      owned.set(parsed.targetId, { file, mtimeMs: fs.statSync(file).mtimeMs });
    } catch {
      // vanished between readdir and stat — treat as not ours
    }
  }
  return owned;
}

// Resolve a page's CDP targetId (the registry's stable key across processes).
async function targetIdOf(context, page) {
  const session = await context.newCDPSession(page);
  try {
    const { targetInfo } = await session.send('Target.getTargetInfo');
    return targetInfo.targetId;
  } finally {
    await session.detach().catch(() => {});
  }
}

// Record a tab this session created. Returns its CDP targetId (also used to
// unregister). Returns null on failure — recording is best-effort and must never
// break the actual automation. The file's mtime starts as its creation time, so
// a tab is at the back of the eviction order the moment it exists.
async function registerTab(context, page) {
  try {
    const targetId = await targetIdOf(context, page);
    const { ownerPid } = ownerInfo();
    writeTab(ownerPid, targetId, { url: page.url() });
    return targetId;
  } catch {
    return null;
  }
}

// Mark a tab THIS SESSION OWNS active, so the sweep doesn't treat it as idle
// while you're using it. Only refreshes a tab already recorded to this session;
// it never adopts an unrecorded tab or claims one owned by another session (that
// would hijack another session's or the user's tab). findTab calls this for you
// on its already-owner-checked result, so you rarely call it directly. Returns
// the tab's CDP targetId, or null on failure. Best-effort.
async function touchTab(context, page) {
  try {
    const targetId = await targetIdOf(context, page);
    const { ownerPid } = ownerInfo();
    const file = tabFile(ownerPid, targetId);
    let recorded = null;
    try {
      recorded = JSON.parse(fs.readFileSync(file, 'utf8'));
    } catch {
      return targetId;  // not ours (or gone) — nothing to mark
    }
    const url = page.url();
    // Rewrite only when the recorded URL has gone stale; otherwise the activity
    // bump alone is enough and costs no content write.
    if (recorded.url !== url) writeTab(ownerPid, targetId, { url });
    else bumpMtime(file);
    return targetId;
  } catch {
    return null;
  }
}

// Remove a tab's file after it is cleanly closed. A targetId identifies exactly
// one tab, so this matches at most one file whatever the owner.
function unregisterTab(targetId) {
  if (!targetId) return;
  try {
    for (const name of listNames()) {
      const parsed = parseName(name);
      if (!parsed || parsed.targetId !== targetId) continue;
      try {
        fs.unlinkSync(path.join(TABS_DIR, name));
      } catch {
        // already reclaimed
      }
    }
  } catch {
    // best-effort
  }
}

// Associates each page with its registry targetId so closeTab(page) needs only
// the page. WeakMap so entries are GC'd with the page and the page object stays
// unpolluted.
const tabIds = new WeakMap();

// Pages we've already attached a popup listener to — so re-touching a tab (e.g.
// findTab called repeatedly in a loop) doesn't stack duplicate listeners.
const popupTracked = new WeakSet();

// Own the tabs a page spawns itself. A click that opens window.open /
// target=_blank / ctrl-click fires 'popup' ON THIS page, so the new tab is by
// definition a child of a tab this session owns — record it under the same
// session and chain the listener so a popup's own popups are caught too. This is
// page-scoped on purpose: a context-level 'page' listener would fire for every
// new tab in the shared browser (other sessions', the user's) with no way to
// attribute it. Best-effort — the launcher sweep still adopts anything missed.
function trackPopups(context, page) {
  if (popupTracked.has(page)) return;
  popupTracked.add(page);
  page.on('popup', async (popup) => {
    try {
      const id = await registerTab(context, popup);  // create ownership under this session
      if (id) tabIds.set(popup, id);
      trackPopups(context, popup);
    } catch {
      // best-effort
    }
  });
}

// Find a tab THIS SESSION OWNS that matches the predicate, and mark it active so
// a tab a worker keeps returning to holds its place in the eviction order and
// isn't reaped as idle. Returns that page, or null when this session owns no
// matching tab — the caller then opens its own with openTab.
//
// Owner-scoped so one session never grabs and navigates another session's (or
// the user's) tab that happens to match the same URL. Among predicate matches it
// keeps only tabs this session owns and returns the most-recently-active, since
// a session can own several matching tabs (repeated openTab, or a popup a click
// spawned). The URL predicate is just a coarse filter; the targetId is the true
// tab identity.
//
// Perf: resolve targetIds (one CDP session each) only for the predicate matches,
// and only when this session owns anything at all.
async function findTab(context, predicate) {
  const owned = ownedTabs();
  if (owned.size === 0) return null;
  const candidates = context.pages().filter(predicate);
  if (candidates.length === 0) return null;

  let best = null;  // { page, mtimeMs }
  for (const page of candidates) {
    let targetId;
    try {
      targetId = await targetIdOf(context, page);
    } catch {
      continue;
    }
    const entry = owned.get(targetId);
    if (!entry) continue;  // not ours — never adopt
    if (!best || entry.mtimeMs > best.mtimeMs) best = { page, mtimeMs: entry.mtimeMs };
  }
  if (!best) return null;

  await touchTab(context, best.page);
  trackPopups(context, best.page);
  return best.page;
}

// Open a new tab, record it, and (optionally) navigate to url. Returns the page.
// Use this instead of context.newPage() so recording can't be skipped.
async function openTab(context, url) {
  const page = await context.newPage();
  const tabId = await registerTab(context, page);
  tabIds.set(page, tabId);
  trackPopups(context, page);
  if (url) await page.goto(url);
  return page;
}

// Close a tab opened with openTab and remove its file. Parks on about:blank
// instead of closing when it is the browser's last tab (closing it would exit
// the persistent browser and lose all logins). Always removes the file —
// reaching here means the tab is not an orphan.
async function closeTab(page) {
  const context = page.context();
  try {
    if (context.pages().length > 1) {
      await page.close().catch(() => {});
    } else {
      await page.goto('about:blank').catch(() => {});
    }
  } finally {
    unregisterTab(tabIds.get(page));
    tabIds.delete(page);
  }
}

module.exports = { openTab, closeTab, findTab, touchTab, registerTab, unregisterTab, TABS_DIR };
