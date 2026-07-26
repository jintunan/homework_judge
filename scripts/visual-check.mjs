import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright-core";

const [taskId, submissionId] = process.argv.slice(2);
if (!taskId || !submissionId) {
  throw new Error("Usage: node scripts/visual-check.mjs <taskId> <submissionId>");
}

const outputDir = path.resolve(process.cwd(), ".visual-shots");
fs.mkdirSync(outputDir, { recursive: true });

const browser = await chromium.launch({
  headless: true,
  executablePath:
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
});
const page = await browser.newPage({
  viewport: { width: 1280, height: 1000 },
  deviceScaleFactor: 1,
});

const consoleErrors = [];
page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
page.on("pageerror", (error) => consoleErrors.push(error.message));

const checks = [
  { name: "dashboard", url: "/" },
  { name: "create", url: "/tasks/new" },
  { name: "answers", url: `/tasks/${taskId}/answers` },
  { name: "upload", url: `/tasks/${taskId}/upload` },
  {
    name: "review",
    url: `/tasks/${taskId}/review/${submissionId}`,
  },
  { name: "reports", url: `/tasks/${taskId}/reports` },
  { name: "student-report", url: `/tasks/${taskId}/reports` },
];

const results = [];
for (const check of checks) {
  await page.goto(`http://127.0.0.1:8790${check.url}`, {
    waitUntil: "domcontentloaded",
  });
  await page.waitForTimeout(
    check.name === "review" || check.name === "answers" ? 1200 : 500,
  );
  if (check.name === "student-report") {
    await page.getByRole("button", { name: "学生报告" }).click();
    await page.waitForTimeout(500);
  }
  const bodyText = await page.locator("body").innerText();
  const h1 = await page.locator("h1").first().textContent().catch(() => null);
  const dimensions = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
    scrollHeight: document.documentElement.scrollHeight,
  }));
  const file = path.join(outputDir, `${check.name}.png`);
  await page.screenshot({ path: file, fullPage: check.name !== "review" });
  results.push({
    ...check,
    h1,
    hasHorizontalOverflow: dimensions.scrollWidth > dimensions.clientWidth,
    bodyExcerpt: bodyText.slice(0, 260).replace(/\s+/g, " "),
    screenshot: file,
  });
}

await browser.close();
console.log(JSON.stringify({ results, consoleErrors }, null, 2));
