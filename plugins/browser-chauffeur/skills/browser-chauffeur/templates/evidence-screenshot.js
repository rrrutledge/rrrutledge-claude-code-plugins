// captureEvidence - a screenshot that shows the user where a problem is: the
// live page, cropped to the problem, with a red box around the exact element
// or text.
//
//   await captureEvidence(page, { text: 'Sumit 2026' }, '.tmp/evidence-typo.png');
//   await captureEvidence(page, { selector: 'video[data-id="42"]' }, '.tmp/evidence-video.png');
//
// The box is a CSS outline, which takes no layout space, so marking never moves
// the content it marks. A text target is wrapped in an outlined <span>, so the
// box follows the text through line wraps and reflow. The element is scrolled to
// the center of the viewport, and the capture waits until its position holds
// steady (fonts loaded, reflow done) so the box lands on what it marks. The mark
// is removed after the capture unless `keepMark` is set (to leave it for the user
// to see in the browser).
//
// Works on a Playwright page or a DirectTab (direct-tab.js): it uses only
// evaluate(fn, arg) and screenshot({ path, clip }).

// Runs in the page. Returns false when the target is not found.
function markTarget({ selector, text, attr }) {
  let el = null;
  if (selector) {
    el = document.querySelector(selector);
  } else {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const i = node.data.indexOf(text);
      if (i === -1 || !node.parentElement || !node.parentElement.getClientRects().length) continue;
      const range = document.createRange();
      range.setStart(node, i);
      range.setEnd(node, i + text.length);
      el = document.createElement('span');
      el.setAttribute(`${attr}-wrap`, '');
      range.surroundContents(el);
      break;
    }
  }
  if (!el) return false;
  el.setAttribute(attr, el.getAttribute('style') || '');
  el.style.outline = '3px solid red';
  el.style.outlineOffset = '2px';
  el.scrollIntoView({ block: 'center', inline: 'center' });
  return true;
}

// Runs in the page. Resolves the padded, viewport-clamped clip once the marked
// element's box has held still across consecutive reads. setTimeout rather than
// requestAnimationFrame, which stalls in a background tab.
async function settledClip({ attr, padding }) {
  const el = document.querySelector(`[${attr}]`);
  await document.fonts.ready;
  const read = () => JSON.stringify(el.getBoundingClientRect());
  const deadline = Date.now() + 3000;
  let last = read();
  let steady = 0;
  while (steady < 2 && Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 100));
    const now = read();
    steady = now === last ? steady + 1 : 0;
    last = now;
  }
  const r = el.getBoundingClientRect();
  const x = Math.max(0, r.left - padding);
  const y = Math.max(0, r.top - padding);
  return {
    x, y,
    width: Math.min(window.innerWidth, r.right + padding) - x,
    height: Math.min(window.innerHeight, r.bottom + padding) - y,
  };
}

// Runs in the page. Restores the element's style, and unwraps a text span.
function unmark({ attr }) {
  const el = document.querySelector(`[${attr}]`);
  if (!el) return;
  if (el.hasAttribute(`${attr}-wrap`)) {
    const parent = el.parentNode;
    el.replaceWith(...el.childNodes);
    parent.normalize();
    return;
  }
  const style = el.getAttribute(attr);
  if (style) el.setAttribute('style', style); else el.removeAttribute('style');
  el.removeAttribute(attr);
}

async function captureEvidence(page, target, outPath, { padding = 80, keepMark = false } = {}) {
  // A fresh attribute per capture, so a mark an earlier keepMark capture left
  // on the page is never mistaken for this one.
  const attr = `data-bc-evidence-${Date.now()}`;
  const args = { selector: target.selector, text: target.text, attr, padding };
  if (!args.selector && !args.text) throw new Error('captureEvidence: target needs a selector or text');
  if (!await page.evaluate(markTarget, args)) {
    throw new Error(`captureEvidence: ${args.selector ? `no element matches ${args.selector}` : `no visible text "${args.text}"`}`);
  }
  try {
    const clip = await page.evaluate(settledClip, args);
    await page.screenshot({ path: outPath, clip });
  } finally {
    if (!keepMark) await page.evaluate(unmark, args).catch(() => {});
  }
  return outPath;
}

module.exports = { captureEvidence };
