import { spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { chromium } from "@playwright/test";

const repoRoot = "C:/projects/sites/blueprint-rec-2";
const artifactDir = path.join(repoRoot, ".codex-smoke", "headless-manual-qa");
const logsDir = path.join(artifactDir, "logs");

fs.mkdirSync(artifactDir, { recursive: true });
fs.mkdirSync(logsDir, { recursive: true });

async function killTree(pid) {
  if (!pid) {
    return;
  }
  await new Promise((resolve) => {
    const killer = spawn("cmd.exe", ["/c", `taskkill /PID ${pid} /T /F`], {
      cwd: repoRoot,
      windowsHide: true,
      stdio: "ignore",
    });
    killer.on("exit", () => resolve());
    killer.on("error", () => resolve());
  });
}

function startProcess(command, args, options = {}) {
  const label = options.label || path.basename(command);
  const stdoutPath = path.join(logsDir, `${label}.stdout.log`);
  const stderrPath = path.join(logsDir, `${label}.stderr.log`);
  fs.writeFileSync(stdoutPath, "");
  fs.writeFileSync(stderrPath, "");
  return spawn(command, args, {
    cwd: repoRoot,
    windowsHide: true,
    stdio: [
      "ignore",
      fs.openSync(stdoutPath, "a"),
      fs.openSync(stderrPath, "a"),
    ],
    ...options,
  });
}

function ensureProcessAlive(processRef, label) {
  if (processRef.exitCode !== null) {
    throw new Error(`${label} exited early with code ${processRef.exitCode}`);
  }
}

async function getAvailablePort() {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close(() => reject(new Error("Could not resolve dynamic port")));
        return;
      }
      const { port } = address;
      server.close((closeError) => {
        if (closeError) {
          reject(closeError);
          return;
        }
        resolve(port);
      });
    });
  });
}

async function waitForJson(url, timeoutMs = 120_000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        return await response.json();
      }
    } catch {}
    await delay(1000);
  }
  throw new Error(`Timed out waiting for ${url}`);
}

async function waitForHttpOk(url, timeoutMs = 120_000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const response = await fetch(url);
      if (response.status > 0) {
        return;
      }
    } catch {}
    await delay(1000);
  }
  throw new Error(`Timed out waiting for ${url}`);
}

async function readBodyText(page) {
  try {
    return (((await page.locator("body").textContent({ timeout: 5_000 })) || "").replace(/\s+/g, " ").trim());
  } catch {
    return "";
  }
}

async function createLiveSession(backendBaseUrl, filePath, titlePrefix) {
  const createResponse = await fetch(`${backendBaseUrl}/api/sessions`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      title: `${titlePrefix} ${path.basename(filePath)} ${new Date().toISOString()}`,
    }),
  });
  if (!createResponse.ok) {
    throw new Error(`Failed to create session for ${filePath}`);
  }

  const created = await createResponse.json();
  const sessionId = created.session.sessionId;

  const form = new FormData();
  form.set("file", new Blob([fs.readFileSync(filePath)]), path.basename(filePath));
  const uploadResponse = await fetch(`${backendBaseUrl}/api/sessions/${sessionId}/document`, {
    method: "POST",
    body: form,
  });
  if (!uploadResponse.ok) {
    throw new Error(`Failed to upload document for ${filePath}`);
  }

  const autoStartedAt = Date.now();
  const autoResponse = await fetch(`${backendBaseUrl}/api/sessions/${sessionId}/auto-annotate`, {
    method: "POST",
  });
  if (!autoResponse.ok) {
    throw new Error(`Auto-annotate failed for ${filePath}: ${await autoResponse.text()}`);
  }

  const autoAnnotated = await autoResponse.json();
  return {
    sessionId,
    elapsedSeconds: Number(((Date.now() - autoStartedAt) / 1000).toFixed(2)),
    session: autoAnnotated.session,
  };
}

async function waitForWorkspaceReady(page, sessionId) {
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    try {
      await page.getByRole("button", { name: "История" }).waitFor({ state: "visible", timeout: 90_000 });
      await page.getByRole("button", { name: "Экспорт ZIP" }).waitFor({ state: "visible", timeout: 30_000 });
      await page.getByText("Сводка").first().waitFor({ state: "visible", timeout: 30_000 });
      return;
    } catch (error) {
      const bodyText = await readBodyText(page);
      const retryButton = page.getByRole("button", { name: /Повторить загрузку|Обновить сессию/ }).first();
      const hasMissingErrorComponents = bodyText.includes("missing required error components, refreshing...");
      const hasTransientNextError =
        bodyText.includes('"page":"/_error"') ||
        bodyText.includes("Internal Server Error") ||
        bodyText.includes("This page could not be found") ||
        bodyText.includes("Cannot find module './vendor-chunks/@swc.js'");
      const hasBlankReloadState = bodyText.length === 0;

      if (attempt < 2 && await retryButton.count()) {
        await retryButton.click();
        await page.waitForTimeout(5_000);
        continue;
      }

      if (attempt < 5 && hasMissingErrorComponents) {
        await page.waitForTimeout(8_000);
        continue;
      }

      if (attempt < 5 && (bodyText.includes("Загружаю рабочее поле") || hasTransientNextError || hasBlankReloadState)) {
        await page.reload({ waitUntil: "domcontentloaded", timeout: 120_000 });
        await page.waitForTimeout(5_000);
        continue;
      }

      throw new Error(`Workspace did not become ready for ${sessionId}: ${bodyText.slice(0, 240)}`, { cause: error });
    }
  }
}

async function probeExport(backendBaseUrl, sessionId) {
  const response = await fetch(`${backendBaseUrl}/api/sessions/${sessionId}/export`);
  return {
    ok: response.ok,
    status: response.status,
    contentType: response.headers.get("content-type"),
    contentDisposition: response.headers.get("content-disposition"),
    body: response.ok ? null : await response.text(),
  };
}

async function openHistory(page) {
  await page.getByRole("button", { name: "История" }).click();
  const dialog = page.getByRole("dialog", { name: "История" });
  await dialog.waitFor({ state: "visible", timeout: 30_000 });
  return dialog;
}

async function closeHistory(page, dialog) {
  const closeButton = dialog.getByRole("button", { name: /закрыть|close/i }).first();
  if (await closeButton.count()) {
    await closeButton.click();
  } else {
    await page.getByRole("button", { name: "История" }).click();
  }
  await dialog.waitFor({ state: "hidden", timeout: 15_000 });
}

async function runHeavySessionQa(page, sessionId, artifactPrefix) {
  await page.goto(`/sessions/${sessionId}`, { waitUntil: "domcontentloaded", timeout: 120_000 });
  await page.waitForTimeout(5_000);
  await waitForWorkspaceReady(page, sessionId);
  await page.waitForTimeout(5_000);

  const initialBody = await readBodyText(page);
  const initialReviewCount =
    /Кандидаты на проверку\s*(\d+)/i.exec(initialBody)?.[1] ??
    /На review\s*(\d+)/i.exec(initialBody)?.[1] ??
    null;
  const hasConflictSurface =
    initialBody.includes("Конфликт кандидатов") ||
    initialBody.includes("Неоднозначное место");

  if (!hasConflictSurface) {
    throw new Error("Heavy live session did not show candidate/ambiguity conflict surface.");
  }

  const historyDialog = await openHistory(page);
  const historyBody = ((await historyDialog.textContent()) || "").replace(/\s+/g, " ").trim();
  const historyHasStickyControls =
    historyBody.includes("компактно") &&
    (historyBody.includes("только ambiguity") || historyBody.includes("журнал")) &&
    (historyBody.includes("full") || historyBody.includes("compact") || historyBody.includes("все"));
  await closeHistory(page, historyDialog);

  const nonConflictCandidate = page.locator("button").filter({ hasText: /^№ \d+/ }).filter({ hasNotText: "конфликт" }).first();
  if (await nonConflictCandidate.count() === 0) {
    throw new Error("Heavy live session did not expose a non-conflict pending candidate button.");
  }

  await nonConflictCandidate.click();
  await page.waitForTimeout(1_500);
  let bodyText = await readBodyText(page);
  const candidatePanelAvailable =
    bodyText.includes("Кандидат") &&
    bodyText.includes("Создать точку") &&
    bodyText.includes("Ложный");

  if (!candidatePanelAvailable) {
    throw new Error("Heavy live session did not open candidate review controls.");
  }

  await page.getByRole("button", { name: /^Ложный$/ }).click();
  await page.waitForTimeout(1_500);
  bodyText = await readBodyText(page);
  const rejectStayedInCandidateMode =
    bodyText.includes("Кандидат") &&
    bodyText.includes("Создать точку") &&
    !bodyText.includes("Review-проход");

  if (!rejectStayedInCandidateMode) {
    throw new Error("Rejecting a live candidate unexpectedly left candidate review mode.");
  }

  const createCandidate = page.locator("button").filter({ hasText: /^№ \d+/ }).filter({ hasNotText: "конфликт" }).first();
  await createCandidate.click();
  await page.waitForTimeout(1_000);
  await page.getByRole("button", { name: "Создать точку" }).click();
  await page.waitForTimeout(1_500);

  const labelInput = page.locator("label", { hasText: "ярлык -" }).locator("input");
  let labelEditorVisible = false;
  let labelUpdated = false;
  if (await labelInput.count()) {
    try {
      await labelInput.waitFor({ state: "visible", timeout: 5_000 });
      labelEditorVisible = true;
      await labelInput.fill("39A");
      await labelInput.press("Enter");
      labelUpdated = true;
    } catch {
      labelEditorVisible = false;
      labelUpdated = false;
    }
  }
  await page.getByRole("button", { name: "Подтвердить по лупе" }).click();
  await page.waitForTimeout(1_500);

  const historyAfterCandidate = await openHistory(page);
  const historyAfterCandidateText = ((await historyAfterCandidate.textContent()) || "").replace(/\s+/g, " ").trim();
  const candidateCreatedAndConfirmed =
    historyAfterCandidateText.includes("Точка добавлена") &&
    historyAfterCandidateText.includes("Точка подтверждена") &&
    (!labelUpdated || historyAfterCandidateText.includes("39A"));

  if (!candidateCreatedAndConfirmed) {
    throw new Error("Live candidate -> draft -> confirm flow did not show expected history entries.");
  }

  const heavyScreenshotPath = path.join(artifactDir, `${artifactPrefix}-heavy.png`);
  await page.screenshot({ path: heavyScreenshotPath, fullPage: true });

  return {
    initialReviewCount,
    hasConflictSurface,
    historyHasStickyControls,
    candidatePanelAvailable,
    rejectStayedInCandidateMode,
    labelEditorVisible,
    labelUpdated,
    candidateCreatedAndConfirmed,
    screenshotPath: heavyScreenshotPath,
  };
}

function buildReport({ heavySession, lightSession, heavyExport, lightExport, heavyUi }) {
  const lines = [
    "# Headless Manual QA Report",
    "",
    `Generated: ${new Date().toISOString()}`,
    "",
    "## Live Sessions",
    "",
    `- Heavy file: \`${heavySession.filePath}\``,
    `- Heavy session: \`${heavySession.sessionId}\``,
    `- Heavy auto-annotate: \`${heavySession.elapsedSeconds}s\``,
    `- Light file: \`${lightSession.filePath}\``,
    `- Light session: \`${lightSession.sessionId}\``,
    `- Light auto-annotate: \`${lightSession.elapsedSeconds}s\``,
    "",
    "## Checks",
    "",
    `- Heavy conflict surface visible: ${heavyUi.hasConflictSurface ? "yes" : "no"}`,
    `- Heavy history sticky controls visible: ${heavyUi.historyHasStickyControls ? "yes" : "no"}`,
    `- Heavy candidate panel available: ${heavyUi.candidatePanelAvailable ? "yes" : "no"}`,
    `- Live reject stays in candidate mode: ${heavyUi.rejectStayedInCandidateMode ? "yes" : "no"}`,
    `- Live label editor surfaced in candidate -> draft flow: ${heavyUi.labelEditorVisible ? "yes" : "no"}`,
    `- Live label updated before confirm: ${heavyUi.labelUpdated ? "yes" : "no"}`,
    `- Live candidate -> draft -> confirm recorded in history: ${heavyUi.candidateCreatedAndConfirmed ? "yes" : "no"}`,
    `- Heavy export blocked: ${!heavyExport.ok && heavyExport.status === 400 ? "yes" : "no"}`,
    `- Light export succeeds: ${lightExport.ok && lightExport.status === 200 ? "yes" : "no"}`,
    "",
    "## Notes",
    "",
    `- Heavy live session still opens on candidate/conflict review first; marker-based ambiguity flow remains covered more strongly by mocked e2e than by this real-data runner.`,
    `- Artifacts: \`${path.join(artifactDir, "latest-results.json")}\`, \`${path.join(artifactDir, "latest-report.md")}\``,
  ];

  return lines.join("\n");
}

async function main() {
  const backendPort = await getAvailablePort();
  const webPort = await getAvailablePort();
  const backendBaseUrl = `http://127.0.0.1:${backendPort}`;
  const webBaseUrl = `http://127.0.0.1:${webPort}`;

  const backend = startProcess(
    "py",
    [
      "-3.11",
      "-m",
      "uvicorn",
      "app.main:app",
      "--app-dir",
      "C:/projects/sites/blueprint-rec-2/services/inference",
      "--env-file",
      "C:/projects/sites/blueprint-rec-2/services/inference/.env.local",
      "--host",
      "127.0.0.1",
      "--port",
      String(backendPort),
    ],
    {
      label: "headless-manual-backend",
      env: {
        ...process.env,
        INFERENCE_CORS_ORIGINS: `http://127.0.0.1:${webPort},http://localhost:${webPort}`,
      },
    },
  );

  const web = startProcess(
    "cmd.exe",
    [
      "/c",
      `set NEXT_PUBLIC_ANNOTATION_API_BASE_URL=${backendBaseUrl}&& npm.cmd run dev --workspace @blueprint-rec/web -- --hostname 127.0.0.1 --port ${webPort}`,
    ],
    { label: "headless-manual-web" },
  );

  try {
    await waitForJson(`${backendBaseUrl}/api/health`);
    ensureProcessAlive(backend, "Headless manual backend");
    await waitForHttpOk(webBaseUrl);
    ensureProcessAlive(web, "Headless manual web");
    await delay(2_000);

    const lightFilePath = path.join(repoRoot, "blueprints-test", "image001.png");
    const heavyFilePath = path.join(repoRoot, "blueprints-test", "test1.jpg");

    const lightLive = await createLiveSession(backendBaseUrl, lightFilePath, "Headless manual QA");
    const heavyLive = await createLiveSession(backendBaseUrl, heavyFilePath, "Headless manual QA");

    const lightSession = {
      filePath: lightFilePath,
      sessionId: lightLive.sessionId,
      elapsedSeconds: lightLive.elapsedSeconds,
      summary: lightLive.session.summary,
    };
    const heavySession = {
      filePath: heavyFilePath,
      sessionId: heavyLive.sessionId,
      elapsedSeconds: heavyLive.elapsedSeconds,
      summary: heavyLive.session.summary,
    };

    const browser = await chromium.launch({ headless: true });
    try {
      const page = await browser.newPage({ baseURL: webBaseUrl });
      const heavyUi = await runHeavySessionQa(page, heavyLive.sessionId, heavyLive.sessionId);
      const heavyExport = await probeExport(backendBaseUrl, heavyLive.sessionId);
      const lightExport = await probeExport(backendBaseUrl, lightLive.sessionId);

      if (heavyExport.ok || heavyExport.status !== 400) {
        throw new Error("Heavy live session export was expected to stay blocked.");
      }
      if (!lightExport.ok || lightExport.status !== 200) {
        throw new Error("Light live session export was expected to succeed.");
      }

      const results = {
        generatedAt: new Date().toISOString(),
        heavySession,
        lightSession,
        heavyUi,
        heavyExport,
        lightExport,
      };

      const report = buildReport({ heavySession, lightSession, heavyExport, lightExport, heavyUi });
      fs.writeFileSync(path.join(artifactDir, "latest-results.json"), JSON.stringify(results, null, 2));
      fs.writeFileSync(path.join(artifactDir, "latest-report.md"), report);
      console.log(JSON.stringify({
        resultsPath: path.join(artifactDir, "latest-results.json"),
        reportPath: path.join(artifactDir, "latest-report.md"),
        results,
      }, null, 2));
    } finally {
      await browser.close();
    }
  } finally {
    await killTree(web.pid);
    await killTree(backend.pid);
  }
}

main().catch((error) => {
  console.error(String(error?.stack || error));
  process.exitCode = 1;
});
