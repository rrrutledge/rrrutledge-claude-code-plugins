// Full-page screenshot capture that works past Chromium's screenshot limit.
// Chromium's screenshot capture — both `page.screenshot({ fullPage: true })`
// and a plain screenshot after resizing the viewport to the full page height —
// breaks down on pages taller than roughly 16384px (a GPU/texture-size
// ceiling). It doesn't fail loudly: the resulting image just has content past
// that point appear to repeat/restart rather than truncating or erroring. A
// long article, a chat thread, or an event page with many entries can all
// cross this without warning.
//
// RECOMMENDED USAGE: Import instead of copying:
//
//   const { captureFullPageScreenshot } = require('browser-chauffeur-helpers');
//
//   const { width, height } = await captureFullPageScreenshot(page, '.tmp/page.png');
//
// Below `threshold` this is just `page.screenshot({ fullPage: true })`. Above
// it, this scrolls in chunks and stitches the results into one composite PNG
// using the PNG encoder bundled with playwright-core (no extra dependency
// needed). The first chunk is captured normally, so a sticky header/nav shows
// up once at the top exactly as it would in an ordinary screenshot; every
// chunk after that has `position: fixed`/`sticky` elements hidden first,
// since otherwise the same header/nav would get captured again at every
// scroll position and appear repeated at regular intervals down the stitched
// image — the same "page restarting" symptom as the 16384px bug, but from an
// unrelated cause.
//
// Options:
//   threshold   (default 14000) — page height in px below which a plain
//               fullPage screenshot is used. Kept under the ~16384px
//               breakdown point to leave margin.
//   chunkHeight (default 2000)  — scroll-chunk height in px for the stitched
//               path. Each chunk is captured as its own screenshot.
//   width       — capture width in CSS px (default: current viewport width).

const fs = require('fs');
const path = require('path');
const os = require('os');

function loadPNG() {
  const attempts = [
    () => require('playwright-core/lib/utilsBundle').PNG,
    () => require(path.join(os.homedir(), '.claude', 'browser-chauffeur', 'node_modules', 'playwright-core', 'lib', 'utilsBundle')).PNG,
  ];
  for (const attempt of attempts) {
    try {
      const PNG = attempt();
      if (PNG) return PNG;
    } catch {
      // try the next candidate
    }
  }
  throw new Error(
    'captureFullPageScreenshot: could not resolve the PNG encoder from ' +
    'playwright-core/lib/utilsBundle. Do not add a bare pngjs dependency — ' +
    'playwright-core already bundles a pngjs-compatible one.'
  );
}

// Hides (rather than repositions) every fixed/sticky element so neutralizing
// it can't itself shift page layout, then returns a function that restores
// each element's original inline visibility exactly as found.
async function hideFixedAndStickyElements(page) {
  const handle = await page.evaluateHandle(() => {
    const hidden = [];
    document.querySelectorAll('body *').forEach(el => {
      const position = getComputedStyle(el).position;
      if (position === 'fixed' || position === 'sticky') {
        hidden.push([el, el.style.getPropertyValue('visibility'), el.style.getPropertyPriority('visibility')]);
        el.style.setProperty('visibility', 'hidden', 'important');
      }
    });
    return hidden;
  });

  return async () => {
    await page.evaluate((entries) => {
      for (const [el, value, priority] of entries) {
        if (value) el.style.setProperty('visibility', value, priority);
        else el.style.removeProperty('visibility');
      }
    }, handle);
    await handle.dispose();
  };
}

async function settle(page) {
  await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
}

async function captureFullPageScreenshot(page, outPath, opts = {}) {
  const { threshold = 14000, chunkHeight = 2000 } = opts;

  const dims = await page.evaluate(() => ({
    height: document.documentElement.scrollHeight,
    width: document.documentElement.scrollWidth,
    scrollX: window.scrollX,
    scrollY: window.scrollY,
  }));

  if (dims.height <= threshold) {
    await page.screenshot({ path: outPath, fullPage: true });
    return { width: dims.width, height: dims.height };
  }

  const originalViewport = page.viewportSize();
  const width = opts.width || (originalViewport && originalViewport.width) || dims.width;
  const height = dims.height;

  await page.setViewportSize({ width, height: chunkHeight });
  let restoreFixed = null;
  try {
    const PNG = loadPNG();
    const composite = new PNG({ width, height });

    let y = 0;
    while (y < height) {
      await page.evaluate((scrollY) => window.scrollTo(0, scrollY), y);
      await settle(page);

      // Leave fixed/sticky elements alone for the first chunk (y === 0), so a
      // sticky header/nav is captured once at the top like a normal
      // screenshot. Hide them starting with the second chunk, before that
      // chunk's screenshot — otherwise the header/nav (still pinned to the
      // same on-screen position) gets captured again at every scroll
      // position and appears repeated down the composite.
      if (y > 0 && !restoreFixed) {
        restoreFixed = await hideFixedAndStickyElements(page);
        await settle(page);
      }

      // The browser clamps scrollTo to the max scrollable position, so the
      // final chunk (when height isn't an exact multiple of chunkHeight) may
      // land short of the requested y — read back where it actually landed
      // rather than assuming the request was honored, or the last chunk's
      // content gets stitched in at the wrong offset.
      const actualScrollY = await page.evaluate(() => window.scrollY);
      const chunkBuffer = await page.screenshot();
      const chunkPng = PNG.sync.read(chunkBuffer);

      const srcY = y - actualScrollY;
      const rowsToCopy = Math.min(chunkPng.height - srcY, height - y);
      PNG.bitblt(chunkPng, composite, 0, srcY, width, rowsToCopy, 0, y);
      y += rowsToCopy;
    }

    fs.writeFileSync(outPath, PNG.sync.write(composite));
  } finally {
    if (originalViewport) await page.setViewportSize(originalViewport);
    if (restoreFixed) await restoreFixed();
    await page.evaluate(({ x, y: sy }) => window.scrollTo(x, sy), { x: dims.scrollX, y: dims.scrollY });
  }

  return { width, height };
}

module.exports = { captureFullPageScreenshot };
