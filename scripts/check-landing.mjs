// Does the page ever show two panels highlighted at once?
//
// A behaviour test rather than a screenshot, because the failure is invisible in a still: two
// highlighted panels look exactly like one highlighted panel plus one the reader scrolled past.
// The console draws `:target` and `[data-landed]` alike deliberately, so the invariant worth
// pinning is that at most one panel is drawn as *where the reader is*, however they got there.
//
// Usage: node scripts/check-landing.mjs <base-url>

import { chromium } from "playwright";

const [baseUrl] = process.argv.slice(2);
const page_url = `${baseUrl}/session.html`;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });

const highlighted = () =>
  page.evaluate(() =>
    [...document.querySelectorAll(".panel")]
      .filter((panel) => panel.matches(":target") || panel.hasAttribute("data-landed"))
      .map((panel) => panel.id),
  );

const failures = [];
const check = async (what, expected) => {
  const found = await highlighted();
  const ok = found.length === expected.length && found.every((id, at) => id === expected[at]);
  console.log(`  ${ok ? "ok  " : "FAIL"}  ${what}: [${found.join(", ")}]`);
  if (!ok) failures.push(`${what}: expected [${expected.join(", ")}], got [${found.join(", ")}]`);
};

console.log("the sequence from the report: click, reload, click another");
await page.goto(page_url, { waitUntil: "load" });
await page.waitForTimeout(120);
await check("on a fresh load, nothing is landed on", []);

await page.click('a.panel__anchor[href="#panel-0-1"]');
await page.waitForTimeout(120);
await check("after clicking the first link", ["panel-0-1"]);

await page.reload({ waitUntil: "load" });
await page.waitForTimeout(150);
await check("after reloading on that hash", ["panel-0-1"]);

await page.click('a.panel__anchor[href="#panel-0-4"]');
await page.waitForTimeout(150);
await check("after clicking a second link", ["panel-0-4"]);

console.log();
console.log("and the dock, which lands without navigating");
await page.goto(page_url, { waitUntil: "load" });
await page.waitForTimeout(120);
await page.click('button[data-step="1"]:not([data-side])');
await page.waitForTimeout(150);
const afterDock = await highlighted();
console.log(`  ${afterDock.length === 1 ? "ok  " : "FAIL"}  stepping lands on exactly one: [${afterDock.join(", ")}]`);
if (afterDock.length !== 1) failures.push(`dock step: expected one panel, got [${afterDock.join(", ")}]`);

await page.click('a.panel__anchor[href="#panel-0-2"]');
await page.waitForTimeout(150);
await check("then following a link away from it", ["panel-0-2"]);

await browser.close();

if (failures.length) {
  console.error(`\n${failures.length} failure(s):`);
  for (const each of failures) console.error(`  ${each}`);
  process.exit(1);
}
console.log("\nat most one panel is ever drawn as where the reader is");
