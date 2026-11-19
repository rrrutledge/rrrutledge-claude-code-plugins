// captureEvidence - a screenshot that shows the user where a problem is: the
// live page, cropped to the problem, with a red box around the exact element
// or text.
//
//   await captureEvidence(page, { text: 'Sumit 2026' }, '.tmp/evidence-typo.png');
//   await captureEvidence(page, { selector: 'video[data-id="42"]' }, '.tmp/evidence-video.png');
//
// The box is a CSS outline, which takes no layout space, so marking never moves
// the content it marks. A text target is split out of its text node and wrapped
// in an outlined <span>, so the box follows the text through line wraps and
// reflow. A selector target is the first matching element that is rendered. The
// target is scrolled to the center of the viewport, and the capture waits until
// its position holds steady (fonts loaded, reflow done) so the box lands on what
// it marks.
//
// The mark is removed after the capture, and before each capture in case an
// interrupted one left a mark behind. Removal restores the exact nodes the page
// had: the split text goes back into the page's own original text node, which a
// framework (React, Vue) may hold a reference to, and the nodes the split created
// are removed.
//
// Works on a Playwright page or a DirectTab (direct-tab.js): it uses only
// evaluate(fn, arg) and screenshot({ path, clip }). The marked nodes are kept on
// window.__bcEvidence between those calls.

// Runs in the page. Returns false when the target is not found.
function markTarget({ selector, text }) {
  const rendered = el => el.getClientRects().length > 0;
  let state = null;
  if (selector) {
    const el = [...document.querySelectorAll(selector)].find(rendered);
    if (el) state = { el, style: el.getAttribute('style') };
  } else {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const i = node.data.indexOf(text);
      if (i === -1 || !node.parentElement || !rendered(node.parentElement)) continue;
      const match = node.splitText(i);
      const rest = match.splitText(text.length);
      const el = document.createElement('span');
      match.parentNode.insertBefore(el, match);
      el.appendChild(match);
      state = { el, node, match, rest };
      break;
    }
  }
  if (!state) return false;
  window.__bcEvidence = state;
  // !important, so a page rule like `* { outline: none !important }` can't hide it.
  state.el.style.setProperty('outline', '3px solid red', 'important');
  state.el.style.setProperty('outline-offset', '2px', 'important');
  state.el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
  return true;
}

// Runs in the page. Resolves the padded, viewport-clamped clip once the marked
// element's box has held still across consecutive reads, or null when the page
// re-rendered or navigated the mark away. setTimeout rather than requestAnimationFrame, which
// stalls in a background tab.
async function settledClip({ padding }) {
  const el = window.__bcEvidence && window.__bcEvidence.el;
  if (!el || !el.isConnected) return null;
  await document.fonts.ready;
  const read = () => JSON.stringify(el.getBoundingClientRect());
  const deadline = Date.now() + 3000;
  let last = read();
  let steady = 0;
  while (steady < 2 && Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 100));
    if (!el.isConnected) return null;
    const now = read();
    steady = now === last ? steady + 1 : 0;
    last = now;
  }
  const r = el.getBoundingClientRect();
  const x = Math.min(Math.max(0, r.left - padding), window.innerWidth - 1);
  const y = Math.min(Math.max(0, r.top - padding), window.innerHeight - 1);
  return {
    x, y,
    width: Math.max(1, Math.min(window.innerWidth, r.right + padding) - x),
    height: Math.max(1, Math.min(window.innerHeight, r.bottom + padding) - y),
  };
}

// Runs in the page. Restores the marked element's style, or puts split text
// back into its original node.
function unmark() {
  const state = window.__bcEvidence;
  if (!state) return;
  delete window.__bcEvidence;
  if (state.node) {
    state.node.data += state.match.data + state.rest.data;
    state.el.remove();
    state.rest.remove();
  } else if (state.style == null) {
    state.el.removeAttribute('style');
  } else {
    state.el.setAttribute('style', state.style);
  }
}

async function captureEvidence(page, target, outPath, { padding = 80 } = {}) {
  const args = { selector: target.selector, text: target.text, padding };
  if (!args.selector && !args.text) throw new Error('captureEvidence: target needs a selector or text');
  await page.evaluate(unmark);
  if (!await page.evaluate(markTarget, args)) {
    throw new Error(`captureEvidence: ${args.selector ? `no rendered element matches ${args.selector}` : `no visible text "${args.text}"`}`);
  }
  try {
    const clip = await page.evaluate(settledClip, args);
    if (!clip) throw new Error('captureEvidence: the page re-rendered or navigated away from the target while it was being measured');
    await page.screenshot({ path: outPath, clip });
  } finally {
    await page.evaluate(unmark).catch(() => {});
  }
  return outPath;
}

module.exports = { captureEvidence };
