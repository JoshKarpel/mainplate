// Screenshot the gallery, so a styling change can be looked at rather than asserted about.
//
// Playwright drives a real Chromium over a real static server, which is what makes the shot worth
// reading: the stylesheet, the script, and htmx are the ones the console ships, loaded the way the
// console loads them. What it is not is a test. Nothing here asserts; it produces PNGs for a person
// (or an agent that can read one) to look at.
//
// Usage: node scripts/shoot.mjs <base-url> <out-dir> [page.html:name ...]

import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";

// Wide enough that the rail stands beside the conversation rather than sliding off, and tall
// enough that a whole turn fits in one shot. The phone viewport is the other case worth seeing,
// because the rail's breakpoint is the thing most likely to be wrong.
const VIEWPORTS = {
  wide: { width: 1400, height: 1600 },
  phone: { width: 390, height: 844 },
};

const [baseUrl, outDir, ...requested] = process.argv.slice(2);

const targets = (requested.length ? requested : [
  "start.html",
  "start-unreferenced.html",
  "session.html",
  "waiting.html",
  "answering.html",
  "handed-off.html",
  "stalled.html",
  "refused.html",
  "forking.html",
  "forking-attach.html",
]).map((each) => {
  const [file, fragment] = each.split("#");
  return { file, fragment, name: fragment ? `${file.replace(/\.html$/, "")}-${fragment}` : file.replace(/\.html$/, "") };
});

await mkdir(outDir, { recursive: true });

const browser = await chromium.launch();
const shots = [];

for (const [label, viewport] of Object.entries(VIEWPORTS)) {
  const page = await browser.newPage({ viewport });
  // Surfacing these is the whole reason to drive a real browser: a stylesheet that 404s or a
  // script that throws is invisible in a screenshot and obvious here.
  page.on("console", (message) => {
    if (message.type() === "error") console.error(`  [${label}] console: ${message.text()}`);
  });
  page.on("requestfailed", (request) => {
    console.error(`  [${label}] failed: ${request.url()} ${request.failure()?.errorText ?? ""}`);
  });
  page.on("response", (response) => {
    if (response.status() >= 400) console.error(`  [${label}] ${response.status()} ${response.url()}`);
  });

  for (const target of targets) {
    const url = `${baseUrl}/${target.file}${target.fragment ? `#${target.fragment}` : ""}`;
    await page.goto(url, { waitUntil: "load" });
    // The console's script pins the theme and reapplies the reader's projections after load, so a
    // shot taken before it settles is of a page nobody sees.
    await page.waitForTimeout(150);

    // The property that must hold on every page at every width: the transcript may scroll its own
    // wide blocks, but the document must never scroll sideways.
    const overflow = await page.evaluate(() => ({
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: document.documentElement.clientWidth,
    }));
    if (overflow.documentWidth > overflow.viewportWidth) {
      console.error(
        `  [${label}] ${target.name}: HORIZONTAL OVERFLOW ` +
          `(document ${overflow.documentWidth}px > viewport ${overflow.viewportWidth}px)`,
      );
    }

    const path = `${outDir}/${target.name}-${label}.png`;
    await page.screenshot({ path, fullPage: label === "wide" });
    shots.push(path);
  }
  await page.close();
}

await browser.close();
console.log(shots.join("\n"));
