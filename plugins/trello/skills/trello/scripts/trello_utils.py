"""
Shared Trello API utilities.

Board-specific settings (board ID, label names, colors, status-to-list mappings)
live in CLAUDE.local.md. This module provides the generic API operations.
"""

import ctypes
import json
import os
import re
import urllib.request
import urllib.parse
from datetime import datetime, timezone

BASE_URL = 'https://api.trello.com/1'

# Every call goes through trello_request(), so this is the single point of failure for a stalled
# connection: urlopen with no timeout blocks forever on a connection that stays open but never
# responds (observed against a Trello CloudFront edge), which hangs the calling process indefinitely.
# The drainer's scheduled poller task then never returns, and Task Scheduler's default policy skips
# every subsequent trigger while it thinks that run is still in progress -- silently, since a poller
# tab runs headless (pythonw) with no console to report to. A bounded timeout turns that into an
# ordinary, per-cycle request failure the poller already catches and retries next cycle.
REQUEST_TIMEOUT = 30

# The lists a card may be archived from: the end of every funnel, plus a board's template parking
# list. The drainer parks the same lists, so a card here is already out of the queue.
TERMINAL_LIST_SUBSTRS = ('finished', 'abandoned', 'adopted', 'done', 'template')

# Archiving a card that still sits in an active list hides its task: the drainer only reads open
# cards, nothing on the board shows it, and an archived card also counts as a finished blocker,
# freeing whatever was waiting on it. So every archive write is checked here, at the one request
# choke point, and an unfinished card is refused; retire_card() is the auditable alternative.
# A delete hides the card for good, so it gets the same check.
_CARD_ARCHIVE_RE = re.compile(r'^/cards/([^/?]+)(/closed)?$')
_LIST_ARCHIVE_ALL_RE = re.compile(r'^/lists/([^/?]+)/archiveAllCards$')


class ArchiveRefused(RuntimeError):
    """Raised instead of archiving or deleting a card outside a terminal list."""


def _is_terminal_list(name):
    return any(t in (name or '').lower() for t in TERMINAL_LIST_SUBSTRS)


def _archive_target(method, path, params, body):
    """Return ('card', id) or ('list', id) when this request would archive or delete cards, else None."""
    if method == 'DELETE':
        m = _CARD_ARCHIVE_RE.match(path)
        return ('card', m.group(1)) if m and not m.group(2) else None
    if method == 'POST':
        m = _LIST_ARCHIVE_ALL_RE.match(path)
        return ('list', m.group(1)) if m else None
    if method != 'PUT':
        return None
    m = _CARD_ARCHIVE_RE.match(path)
    if not m:
        return None
    fields = {**(params or {}), **(body or {})}
    value = fields.get('value' if m.group(2) else 'closed')
    return ('card', m.group(1)) if str(value).lower() == 'true' else None


def _refuse_unfinished_archive(target, session):
    kind, target_id = target
    if kind == 'card':
        list_name = trello_request('GET', f'/cards/{target_id}/list', session, params={'fields': 'name'})['name']
    else:
        list_name = trello_request('GET', f'/lists/{target_id}', session, params={'fields': 'name'})['name']
    if _is_terminal_list(list_name):
        return
    raise ArchiveRefused(
        f"Refusing to archive or delete {kind} {target_id}: it sits in '{list_name}', not a terminal "
        f"list, so this would hide unfinished work from the board and the drainer. To finish a card, move "
        f"it to Finished; to drop one (a duplicate, or work that moved elsewhere), call "
        f"retire_card(card_id, reason, session) - it moves the card to Abandoned with a comment saying "
        f"why and where the work went, and leaves it visible."
    )


def _read_windows_credential(target):
    """Read a credential from Windows Credential Manager via ctypes."""
    try:
        import ctypes.wintypes

        class _CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ('Flags', ctypes.wintypes.DWORD),
                ('Type', ctypes.wintypes.DWORD),
                ('TargetName', ctypes.c_wchar_p),
                ('Comment', ctypes.c_wchar_p),
                ('LastWritten', ctypes.c_ulonglong),
                ('CredentialBlobSize', ctypes.wintypes.DWORD),
                ('CredentialBlob', ctypes.c_void_p),
                ('Persist', ctypes.wintypes.DWORD),
                ('AttributeCount', ctypes.wintypes.DWORD),
                ('Attributes', ctypes.c_void_p),
                ('TargetAlias', ctypes.c_wchar_p),
                ('UserName', ctypes.c_wchar_p),
            ]

        advapi32 = ctypes.WinDLL('advapi32')
        cred_ptr = ctypes.c_void_p()
        if advapi32.CredReadW(target, 1, 0, ctypes.byref(cred_ptr)):
            cred = ctypes.cast(cred_ptr, ctypes.POINTER(_CREDENTIAL)).contents
            blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
            advapi32.CredFree(cred_ptr)
            return blob.decode('utf-16-le')
    except Exception:
        pass
    return None


def get_trello_session():
    api_key = os.environ.get('TRELLO_API_KEY') or _read_windows_credential('TRELLO_API_KEY')
    token = os.environ.get('TRELLO_TOKEN') or _read_windows_credential('TRELLO_TOKEN')
    if not api_key or not token:
        raise RuntimeError(
            'Trello credentials not found. Set TRELLO_API_KEY and TRELLO_TOKEN env vars, '
            'or store them in Windows Credential Manager under those names.'
        )
    return api_key, token


def trello_request(method, path, session, params=None, body=None):
    archive = _archive_target(method, path, params, body)
    if archive:
        _refuse_unfinished_archive(archive, session)
    api_key, token = session
    url = f'{BASE_URL}{path}'

    query = {'key': api_key, 'token': token}
    if params:
        query.update(params)

    if method == 'GET' or method == 'DELETE':
        url = f'{url}?{urllib.parse.urlencode(query)}'
        data = None
    else:
        url = f'{url}?{urllib.parse.urlencode(query)}'
        data = urllib.parse.urlencode(body).encode() if body else b''

    req = urllib.request.Request(url, data=data, method=method)
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
        raw = response.read().decode()
        if raw:
            return json.loads(raw)
        return None


def get_board_lists(board_id, session):
    return trello_request('GET', f'/boards/{board_id}/lists', session)


def resolve_board_id(board_id, session):
    """Return the full 24-char board id. Most read endpoints accept the short
    link (e.g. 'hpvdRw3G'), but some writes (POST /lists) reject it with
    400 'invalid value for idBoard'. Call this before such writes."""
    if len(board_id) == 24:
        return board_id
    return trello_request('GET', f'/boards/{board_id}', session, params={'fields': 'id'})['id']


def get_or_create_list(board_id, list_name, session):
    lists = get_board_lists(board_id, session)
    for lst in lists:
        if lst['name'] == list_name:
            return lst['id']
    result = trello_request('POST', '/lists', session, body={
        'name': list_name,
        'idBoard': resolve_board_id(board_id, session),
    })
    return result['id']


def reorder_lists(board_id, desired_order, session):
    lists = get_board_lists(board_id, session)
    lists_by_name = {lst['name']: lst for lst in lists}
    for i, name in enumerate(desired_order):
        if name in lists_by_name:
            trello_request('PUT', f'/lists/{lists_by_name[name]["id"]}', session,
                           params={'pos': str((i + 1) * 65536)})


_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _start_at_central_midnight(start):
    """Pin a bare YYYY-MM-DD Start to that calendar day, via a flat `06:00` UTC (about midnight
    Central - the exact hour doesn't matter), so the card surfaces that morning. A value carrying a
    time already (a full ISO timestamp) is returned unchanged, as is anything that isn't a bare date."""
    if _DATE_RE.match(str(start)):
        return f'{start}T06:00:00.000Z'
    return start


def create_card(list_id, card_data, session):
    body = {
        'idList': list_id,
        'name': card_data['title'],
        'desc': card_data.get('description', ''),
    }
    # `start` is the only date the queue reads (the startable-task model): the "work-on-it / go-live"
    # day. A card with no Start is startable at once. A bare YYYY-MM-DD is pinned to that morning.
    if card_data.get('start'):
        body['start'] = _start_at_central_midnight(card_data['start'])
    return trello_request('POST', '/cards', session, body=body)


def update_card(card_id, updates, session):
    if updates.get('start'):
        updates = {**updates, 'start': _start_at_central_midnight(updates['start'])}
    return trello_request('PUT', f'/cards/{card_id}', session, body=updates)


def retire_card(card_id, reason, session, outcome='Abandoned'):
    """Take an unfinished card out of the queue the auditable way.

    Posts `reason` as a comment (say why, and where the work went - another card, a calendar event, a
    sheet row), then moves the card to the board's `outcome` list (Abandoned by default, Finished when
    the work is actually done), creating that list if the board lacks one. The card stays open and
    visible, the drainer skips it like any parked card, and Russell can see at a glance what happened.
    """
    if not _is_terminal_list(outcome):
        raise ValueError(f"retire_card outcome must be a terminal list (e.g. Finished, Abandoned), not '{outcome}'")
    card = trello_request('GET', f'/cards/{card_id}', session, params={'fields': 'idBoard'})
    list_id = get_or_create_list(card['idBoard'], outcome, session)
    add_comment(card_id, reason, session)
    return trello_request('PUT', f'/cards/{card_id}', session, body={'idList': list_id})


def delete_card(card_id, session):
    return trello_request('DELETE', f'/cards/{card_id}', session)


def add_comment(card_id, text, session):
    """Post a comment on a card. This is the typed wrapper for the dated status
    comments every advance/nudge writes, so no caller has to hand-build the
    /actions/comments request."""
    return trello_request('POST', f'/cards/{card_id}/actions/comments', session,
                          body={'text': text})


def get_board_cards(board_id, session, fields=None):
    """List a board's open cards. Pass fields='all' (or a comma-separated list) to control which
    card fields come back — the default Trello card payload omits the newer `start` date, so callers
    that need the Start (the startable-task model's one date) should request fields='all'."""
    params = {'fields': fields} if fields else None
    return trello_request('GET', f'/boards/{board_id}/cards', session, params=params)


def find_card_by_name(board_id, name, session):
    """Return the first card on the board whose name matches exactly, else None.

    Template cards (isTemplate=true) are included in this listing, so this also
    finds template/source cards by name.
    """
    for card in get_board_cards(board_id, session):
        if card['name'] == name:
            return card
    return None


def create_card_from_template(list_id, template_card_id, name, session, keep='checklists'):
    """Create a card copying content from a template/source card.

    Uses Trello's idCardSource + keepFromSource. `keep` is 'all', 'none', or a
    comma-separated list e.g. 'checklists,labels,attachments,start'. Defaults to
    copying checklists only.
    """
    return trello_request('POST', '/cards', session, body={
        'idList': list_id,
        'name': name,
        'idCardSource': template_card_id,
        'keepFromSource': keep,
    })


def get_board_labels(board_id, session):
    return trello_request('GET', f'/boards/{board_id}/labels', session, params={'limit': '1000'})


def create_label(board_id, name, color, session):
    return trello_request('POST', '/labels', session, body={
        'name': name,
        'color': color,
        'idBoard': board_id,
    })


def add_label_to_card(card_id, label_id, session):
    return trello_request('POST', f'/cards/{card_id}/idLabels', session,
                          body={'value': label_id})


def remove_label_from_card(card_id, label_id, session):
    return trello_request('DELETE', f'/cards/{card_id}/idLabels/{label_id}', session)


def ensure_labels_exist(board_id, label_definitions, session):
    existing = get_board_labels(board_id, session)
    existing_names = {label['name'] for label in existing}
    created = []
    for name, info in label_definitions.items():
        if name not in existing_names:
            create_label(board_id, name, info['color'], session)
            created.append(name)
    return created


def extract_github_field_value(field_values, field_name):
    for fv in field_values:
        if 'field' in fv and fv['field'] and fv['field']['name'] == field_name:
            return fv.get('text') or fv.get('date') or fv.get('name')
    return None


# --------------------------------------------------------------------- startable-task dependency model
# A blocked card advertises its blockers in a `Blocked-by: <shortlink>, ...` line in its description
# (shortlinks are the 8-char code in a card's shortUrl, https://trello.com/c/<shortlink>). The unblock
# is a PUSH: finishing an upstream card runs cascade_unblock, which strips the blocked label and sets
# Start = today on every card whose LAST remaining blocker just cleared. Nothing polls the blocked card.

# Card descriptions in this project write every field in bold markdown (`**Blocked-by:**`), so the
# label and colon tolerate up to two leading/trailing `*` — a bare `Blocked-by:` still matches too.
_BLOCKED_BY_RE = re.compile(r'^\s*\*{0,2}\s*blocked-by\s*:\s*\*{0,2}(.*)$', re.IGNORECASE | re.MULTILINE)
# A shortlink is the 8-char code after /c/ in a card URL, or a bare 8-char token delimited by
# whitespace/commas — the delimiter guard keeps 8-letter slug words (…/12-ping-kristine) from matching.
_URL_SHORTLINK_RE = re.compile(r'trello\.com/c/([A-Za-z0-9]{8})')
_BARE_SHORTLINK_RE = re.compile(r'(?:^|[\s,])([A-Za-z0-9]{8})(?=[\s,]|$)')


def parse_blocked_by(desc):
    """Return the list of upstream card shortlinks named on a card's `Blocked-by:` line(s)."""
    out = []
    for m in _BLOCKED_BY_RE.finditer(desc or ''):
        seg = m.group(1)
        out.extend(_URL_SHORTLINK_RE.findall(seg))
        # Bare 8-char tokens are a fallback for a shortlink pasted without its full URL, but an 8-letter
        # plain-English word (e.g. "finalize" in a human-written parenthetical aside on the same line)
        # matches the same shape and would become an unresolvable phantom blocker that never clears.
        # Real Trello shortlinks are drawn from a mixed-case+digit alphabet and are essentially never
        # all-lowercase, so require at least one non-lowercase char to filter plain words out.
        bare = _BARE_SHORTLINK_RE.findall(_URL_SHORTLINK_RE.sub(' ', seg))
        out.extend(b for b in bare if not b.islower())
    seen, res = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            res.append(x)
    return res


def _card_is_done(card, lists_by_id, done_substrs):
    """A blocker counts as resolved when its card is archived (closed) or sits in a terminal list
    (Finished / Abandoned / Adopted / Done) — the same lists the drainer already treats as parked."""
    if card.get('closed'):
        return True
    name = (lists_by_id.get(card.get('idList')) or '').lower()
    return any(t in name for t in done_substrs)


def cascade_unblock(board_id, finished_card_id, session,
                    blocked_label_substr='blocked',
                    done_substrs=('finished', 'abandoned', 'adopted', 'done')):
    """Push-unblock downstream cards after `finished_card_id` is completed.

    Scans the board once, finds cards whose `Blocked-by:` names the finished card, and for each — if
    ALL of its blockers are now done — strips its blocked label and sets Start = today so it surfaces
    on the next drain. Multi-blocker cards and chains fall out of the all-blockers-done check for free.
    Returns the list of unblocked card ids. Read-heavy but runs only at completion time.
    """
    cards = get_board_cards(board_id, session, fields='all')
    lists_by_id = {l['id']: l['name'] for l in get_board_lists(board_id, session)}
    by_shortlink = {c.get('shortLink'): c for c in cards if c.get('shortLink')}
    finished = next((c for c in cards if c.get('id') == finished_card_id
                     or c.get('shortLink') == finished_card_id), None)
    if not finished:
        return []
    finished_keys = {finished.get('id'), finished.get('shortLink')}
    today = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    unblocked = []
    for card in cards:
        blockers = parse_blocked_by(card.get('desc'))
        if not blockers or finished_keys.isdisjoint(blockers):
            continue
        # Only unblock once every blocker this card names is resolved (handles multi-blocker + chains).
        all_done = True
        for sl in blockers:
            blk = by_shortlink.get(sl)
            if blk is None or not _card_is_done(blk, lists_by_id, done_substrs):
                all_done = False
                break
        if not all_done:
            continue
        for label in card.get('labels', []):
            if blocked_label_substr in (label.get('name') or '').lower():
                remove_label_from_card(card['id'], label['id'], session)
        update_card(card['id'], {'start': today}, session)
        unblocked.append(card['id'])
    return unblocked


def sweep_unblock(board_ids, session,
                   blocked_label_substr='blocked',
                   done_substrs=('finished', 'abandoned', 'adopted', 'done')):
    """Pull-unblock backstop across MULTIPLE boards: a blocker and the card it blocks are often on
    different boards (e.g. a resume-prep task on Personal Follow-Up blocking an application card on Job
    Search Outreach) — cascade_unblock and a single-board sweep are both blind to that, since they only
    look up shortlinks within one board's own card list. This does one combined pass over every board in
    `board_ids` (2 read calls per board, not per blocker) and frees any card whose named blockers are ALL
    already done, regardless of which board they live on or how/when they finished.

    cascade_unblock is a PUSH triggered at the moment a worker finishes a specific card; it's blind to an
    upstream card finished any other way (Russell moving it by hand in the Trello UI, a card closed
    outside any worker session). This is the PULL half: safe to call every poll cycle since it's a no-op
    scan when nothing needs freeing, so a blocked card resurfaces on its own next drain no matter who or
    how its blocker actually finished. `board_ids` should be every board in play (e.g. the drainer's full
    registry) so a cross-board blocker is always resolvable. Returns the list of unblocked card ids.
    """
    if isinstance(board_ids, str):
        board_ids = [board_ids]
    all_cards, lists_by_id = [], {}
    for board_id in board_ids:
        all_cards.extend(get_board_cards(board_id, session, fields='all'))
        lists_by_id.update({l['id']: l['name'] for l in get_board_lists(board_id, session)})
    by_shortlink = {c.get('shortLink'): c for c in all_cards if c.get('shortLink')}
    today = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    unblocked = []
    for card in all_cards:
        blockers = parse_blocked_by(card.get('desc'))
        if not blockers:
            continue
        all_done = all(
            by_shortlink.get(sl) is not None and _card_is_done(by_shortlink[sl], lists_by_id, done_substrs)
            for sl in blockers
        )
        if not all_done:
            continue
        blocked_labels = [l for l in card.get('labels', [])
                          if blocked_label_substr in (l.get('name') or '').lower()]
        if not blocked_labels:
            continue  # already unblocked (label previously stripped) - nothing to do
        for label in blocked_labels:
            remove_label_from_card(card['id'], label['id'], session)
        update_card(card['id'], {'start': today}, session)
        unblocked.append(card['id'])
    return unblocked
