// Recipient-list helpers for --reply. Run the tests: node --test recipients.test.js

// Split an address-list header on the commas that separate addresses, leaving commas inside a quoted display name alone.
function splitAddresses(list) {
  return (list || '').split(/,(?=(?:[^"]*"[^"]*")*[^"]*$)/).map(s => s.trim()).filter(Boolean);
}

// The bare, lowercased address inside a "Name <addr>" form (or the entry itself when it has no angle brackets).
function bareAddress(entry) {
  const m = entry.match(/<([^>]*)>\s*$/);
  return (m ? m[1] : entry).trim().toLowerCase();
}

// Remove every address in `exclude` (a comma-separated string) from `list` (an address-list string), case-insensitively.
function excludeAddresses(list, exclude) {
  const drop = new Set(splitAddresses(exclude).map(bareAddress));
  if (!drop.size) return list || '';
  return splitAddresses(list).filter(e => !drop.has(bareAddress(e))).join(', ');
}

module.exports = { splitAddresses, bareAddress, excludeAddresses };
