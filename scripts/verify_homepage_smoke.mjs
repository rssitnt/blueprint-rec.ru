import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";

const DEFAULT_URL = "http://127.0.0.1:3010/";
const DEFAULT_OUT_DIR = "C:/projects/sites/blueprint-rec-2/.codex-smoke/startup-home-smoke";

function parseArgs(argv) {
  const result = {
    url: DEFAULT_URL,
    outDir: DEFAULT_OUT_DIR,
  };
  for (let i = 2; i < argv.length; i += 1) {
    const current = argv[i];
    if (current === "--url" && argv[i + 1]) {
      result.url = argv[i + 1];
      i += 1;
      continue;
    }
    if (current === "--out-dir" && argv[i + 1]) {
      result.outDir = argv[i + 1];
      i += 1;
    }
  }
  return result;
}

async function main() {
  const { url, outDir } = parseArgs(process.argv);
  fs.mkdirSync(outDir, { recursive: true });

  const screenshotPath = path.join(outDir, "homepage.png");
  const reportPath = path.join(outDir, "homepage-smoke.json");

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1200 } });
  const consoleErrors = [];
  const pageErrors = [];

  page.on("console", (msg) => {
    if (msg.type() === "error") {
      consoleErrors.push(msg.text());
    }
  });
  page.on("pageerror", (err) => {
    pageErrors.push(String(err));
  });

  let ok = false;
  let failureReason = null;

  try {
    await page.goto(url, { waitUntil: "networkidle", timeout: 120_000 });
    await page.screenshot({ path: screenshotPath, fullPage: true });

    const title = await page.title();
    const bodyText = ((await page.locator("body").innerText()).replace(/\s+/g, " ").trim());
    const computed = await page.evaluate(() => {
      const bodyStyle = window.getComputedStyle(document.body);
      const rootStyle = window.getComputedStyle(document.documentElement);
      const cssHrefs = Array.from(document.querySelectorAll('link[rel="stylesheet"]'))
        .map((node) => node.getAttribute("href"))
        .filter(Boolean);
      const heading = document.querySelector("h1");
      const headingStyle = heading ? window.getComputedStyle(heading) : null;
      return {
        bodyBackground: bodyStyle.backgroundColor,
        rootBackground: rootStyle.backgroundColor,
        bodyColor: bodyStyle.color,
        styleSheetCount: document.styleSheets.length,
        cssHrefs,
        headingFontSize: headingStyle?.fontSize ?? null,
        headingFontFamily: headingStyle?.fontFamily ?? null,
      };
    });

    const hasHeading = bodyText.includes("Новая задача");
    const hasAction = bodyText.includes("Запустить распознавание");
    const hasNextCss = computed.cssHrefs.some((href) => href.includes("/_next/static/css/"));
    const hasStyledHeading = Number.parseFloat(computed.headingFontSize || "0") >= 28;

    ok =
      title.includes("Blueprint Annotation Desk") &&
      hasHeading &&
      hasAction &&
      computed.styleSheetCount > 0 &&
      hasNextCss &&
      hasStyledHeading &&
      consoleErrors.length === 0 &&
      pageErrors.length === 0;

    if (!ok) {
      failureReason = {
        title,
        hasHeading,
        hasAction,
        bodyBackground: computed.bodyBackground,
        rootBackground: computed.rootBackground,
        styleSheetCount: computed.styleSheetCount,
        cssHrefs: computed.cssHrefs,
        headingFontSize: computed.headingFontSize,
        headingFontFamily: computed.headingFontFamily,
        consoleErrors,
        pageErrors,
      };
    }

    fs.writeFileSync(
      reportPath,
      JSON.stringify(
        {
          ok,
          url,
          title,
          hasHeading,
          hasAction,
          bodyBackground: computed.bodyBackground,
          rootBackground: computed.rootBackground,
          styleSheetCount: computed.styleSheetCount,
          cssHrefs: computed.cssHrefs,
          headingFontSize: computed.headingFontSize,
          headingFontFamily: computed.headingFontFamily,
          consoleErrors,
          pageErrors,
          screenshotPath,
          failureReason,
        },
        null,
        2,
      ),
      "utf8",
    );
  } finally {
    await browser.close();
  }

  if (!ok) {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  const { outDir } = parseArgs(process.argv);
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(
    path.join(outDir, "homepage-smoke.json"),
    JSON.stringify(
      {
        ok: false,
        error: String(error),
      },
      null,
      2,
    ),
    "utf8",
  );
  process.exitCode = 1;
});
