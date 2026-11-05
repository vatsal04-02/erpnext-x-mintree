// Desk screenshots for visual review. Usage:
//   node ui_check_desk.js <outdir> [user] [password] [route ...]
// Needs a running `bench --site test.localhost serve --port 8000`. Development only.
const { chromium } = require("playwright");
const BASE = process.env.BASE_URL || "http://test.localhost:8000";
(async () => {
  const [out, user = "Administrator", pw = "ci-admin", ...routes] = process.argv.slice(2);
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e).slice(0, 200)));
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.screenshot({ path: `${out}/login.png` });
  await page.fill("#login_email", user);
  await page.fill("#login_password", pw);
  await Promise.all([page.waitForURL(/\/desk/, { timeout: 30000 }), page.press("#login_password", "Enter")]);
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${out}/desk_home.png` });
  for (const r of routes) {
    await page.goto(`${BASE}/desk/${r}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(2500);
    await page.screenshot({ path: `${out}/${r.replace(/[^a-z0-9]+/gi, "_")}.png` });
  }
  console.log("page errors:", JSON.stringify(errors));
  await browser.close();
})();
