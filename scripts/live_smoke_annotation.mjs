import { spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { chromium } from "@playwright/test";

const repoRoot = "C:/projects/sites/blueprint-rec-2";
const smokeDir = path.join(repoRoot, ".codex-smoke", "live-smoke");
const logsDir = path.join(smokeDir, "logs");
let backendPort = 8012;
let webPort = 3003;
let backendBaseUrl = `http://127.0.0.1:${backendPort}`;
let webBaseUrl = `http://127.0.0.1:${webPort}`;
const blueprintFiles = [
  {
    filePath: path.join(repoRoot, "blueprints-test", "image001.png"),
    expect: {
      minMarkers: 3,
      minCandidates: 3,
      minAiReview: 0,
      maxPipelineConflicts: 0,
      exportOk: true,
      exportStatus: 200,
      exportContentTypeIncludes: "application/zip",
      requireContentDisposition: true,
      requireHistoryEntry: true,
      requireReviewSummary: true,
      requireAiReviewPanel: false,
    },
  },
  {
    filePath: path.join(repoRoot, "blueprints-test", "test1.jpg"),
    expect: {
      minMarkers: 20,
      minCandidates: 20,
      minAiReview: 3,
      minPipelineConflicts: 3,
      exportOk: false,
      exportStatus: 400,
      exportBodyIncludes: "Export blocked until pipeline conflicts are resolved",
      exportBodyIncludesAny: ["association_ambiguity", "missing_vocab_label"],
      requireHistoryEntry: true,
      requireReviewSummary: true,
      requireAiReviewPanel: true,
    },
  },
];

fs.mkdirSync(smokeDir, { recursive: true });
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

async function getAvailablePortInRange(start, end) {
  for (let port = start; port <= end; port += 1) {
    try {
      await new Promise((resolve, reject) => {
        const server = net.createServer();
        server.unref();
        server.on("error", reject);
        server.listen(port, "127.0.0.1", () => {
          server.close((closeError) => {
            if (closeError) {
              reject(closeError);
              return;
            }
            resolve(undefined);
          });
        });
      });
      return port;
    } catch {}
  }
  throw new Error(`Could not find a free port in range ${start}-${end}`);
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
    return (await page.locator("body").textContent({ timeout: 5_000 })) || "";
  } catch {
    try {
      return (await page.textContent("body", { timeout: 5_000 })) || "";
    } catch {
      return "";
    }
  }
}

async function createLiveSession(filePath) {
  let createResponse;
  let lastError = null;
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    try {
      createResponse = await fetch(`${backendBaseUrl}/api/sessions`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          title: `Codex live smoke ${path.basename(filePath)} ${new Date().toISOString()}`,
        }),
      });
      break;
    } catch (error) {
      lastError = error;
      await delay(1500 * attempt);
    }
  }
  if (!createResponse) {
    throw new Error(`Failed to create session for ${filePath}: ${String(lastError?.message || lastError || "unknown fetch error")}`);
  }
  if (!createResponse.ok) {
    throw new Error(`Failed to create session for ${filePath}`);
  }
  const created = await createResponse.json();
  const sessionId = created.session.sessionId;

  const form = new FormData();
  const body = fs.readFileSync(filePath);
  form.set("file", new Blob([body]), path.basename(filePath));
  const uploadResponse = await fetch(`${backendBaseUrl}/api/sessions/${sessionId}/document`, {
    method: "POST",
    body: form,
  });
  if (!uploadResponse.ok) {
    throw new Error(`Failed to upload document for ${filePath}`);
  }

  const started = Date.now();
  const autoResponse = await fetch(`${backendBaseUrl}/api/sessions/${sessionId}/auto-annotate`, {
    method: "POST",
  });
  if (!autoResponse.ok) {
    const detail = await autoResponse.text();
    throw new Error(`Auto-annotate failed for ${filePath}: ${detail}`);
  }
  const autoAnnotated = await autoResponse.json();
  const elapsedSeconds = Number(((Date.now() - started) / 1000).toFixed(2));

  return {
    sessionId,
    elapsedSeconds,
    session: autoAnnotated.session,
  };
}

function assertScenario(result, expect) {
  if ((result.totalMarkers ?? 0) < (expect.minMarkers ?? 0)) {
    throw new Error(`Live smoke expected at least ${expect.minMarkers} markers for ${result.filePath}, got ${result.totalMarkers}`);
  }
  if ((result.totalCandidates ?? 0) < (expect.minCandidates ?? 0)) {
    throw new Error(`Live smoke expected at least ${expect.minCandidates} candidates for ${result.filePath}, got ${result.totalCandidates}`);
  }
  if ((result.aiReview ?? 0) < (expect.minAiReview ?? 0)) {
    throw new Error(`Live smoke expected at least ${expect.minAiReview} ai_review markers for ${result.filePath}, got ${result.aiReview}`);
  }
  if (expect.maxPipelineConflicts !== undefined && (result.pipelineConflicts ?? 0) > expect.maxPipelineConflicts) {
    throw new Error(`Live smoke expected at most ${expect.maxPipelineConflicts} pipeline conflicts for ${result.filePath}, got ${result.pipelineConflicts}`);
  }
  if (expect.minPipelineConflicts !== undefined && (result.pipelineConflicts ?? 0) < expect.minPipelineConflicts) {
    throw new Error(`Live smoke expected at least ${expect.minPipelineConflicts} pipeline conflicts for ${result.filePath}, got ${result.pipelineConflicts}`);
  }
  if (result.export.ok !== expect.exportOk) {
    throw new Error(`Live smoke expected export ok=${expect.exportOk} for ${result.filePath}, got ${result.export.ok}`);
  }
  if (result.export.status !== expect.exportStatus) {
    throw new Error(`Live smoke expected export status ${expect.exportStatus} for ${result.filePath}, got ${result.export.status}`);
  }
  if (expect.exportContentTypeIncludes && !(result.export.contentType || "").includes(expect.exportContentTypeIncludes)) {
    throw new Error(`Live smoke expected export content-type to include "${expect.exportContentTypeIncludes}" for ${result.filePath}`);
  }
  if (expect.requireContentDisposition && !result.export.contentDisposition) {
    throw new Error(`Live smoke expected content-disposition header for ${result.filePath}`);
  }
  if (expect.exportBodyIncludes && !(result.export.body || "").includes(expect.exportBodyIncludes)) {
    throw new Error(`Live smoke expected export body to include "${expect.exportBodyIncludes}" for ${result.filePath}`);
  }
  if (expect.exportBodyIncludesAny?.length) {
    const body = result.export.body || "";
    const matched = expect.exportBodyIncludesAny.some((item) => body.includes(item));
    if (!matched) {
      throw new Error(`Live smoke expected export body to include one of ${expect.exportBodyIncludesAny.join(", ")} for ${result.filePath}`);
    }
  }
  if (expect.requireHistoryEntry && !result.ui.historyHasAutoAnnotateEntry) {
    throw new Error(`Live smoke expected history auto-annotate entry for ${result.filePath}`);
  }
  if (expect.requireReviewSummary && !result.ui.hasReviewSummary) {
    throw new Error(`Live smoke expected review summary for ${result.filePath}`);
  }
  if (expect.requireAiReviewPanel && !result.ui.hasAiReviewPanel && !result.ui.hasConflictReviewSurface) {
    throw new Error(`Live smoke expected AI review or candidate-conflict review surface for ${result.filePath}`);
  }
  if (expect.requireAiReviewPanel === false && (result.ui.hasAiReviewPanel || result.ui.hasConflictReviewSurface)) {
    throw new Error(`Live smoke expected no review conflict surface for ${result.filePath}`);
  }
}

async function probeExport(sessionId) {
  const response = await fetch(`${backendBaseUrl}/api/sessions/${sessionId}/export`);
  return {
    ok: response.ok,
    status: response.status,
    contentType: response.headers.get("content-type"),
    contentDisposition: response.headers.get("content-disposition"),
    body: response.ok ? null : await response.text(),
  };
}

async function waitForWorkspaceReady(page, sessionId) {
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    try {
      await page.getByRole("button", { name: "История" }).waitFor({ state: "visible", timeout: 90_000 });
      await page.getByRole("button", { name: "Экспорт ZIP" }).waitFor({ state: "visible", timeout: 30_000 });
      await page.getByText("Точки").first().waitFor({ state: "visible", timeout: 30_000 });
      return;
    } catch (error) {
      const bodyText = (await readBodyText(page)).replace(/\s+/g, " ").trim();
      const hasErrorState = bodyText.includes("Не удалось открыть рабочее поле");
      const hasRetryButton = await page.getByRole("button", { name: /Повторить загрузку|Обновить сессию/ }).count();
      const hasLoadingState = bodyText.includes("Загружаю рабочее поле");
      const hasMissingErrorComponents = bodyText.includes("missing required error components, refreshing...");
      const hasTransientNextError =
        bodyText.includes('"page":"/_error"') ||
        bodyText.includes("Internal Server Error") ||
        bodyText.includes("This page could not be found") ||
        bodyText.includes("Cannot find module './vendor-chunks/@swc.js'");
      const hasBlankReloadState = bodyText.length === 0;
      const canRetry = attempt < 2;

      if (hasRetryButton && canRetry) {
        const retryButton = page.getByRole("button", { name: /Повторить загрузку|Обновить сессию/ }).first();
        await retryButton.click();
        await page.waitForTimeout(5_000);
        continue;
      }

      if (hasMissingErrorComponents && attempt < 5) {
        await page.waitForTimeout(8_000);
        continue;
      }

      if ((hasLoadingState || hasErrorState || hasTransientNextError || hasBlankReloadState) && attempt < 5) {
        await page.reload({ waitUntil: "domcontentloaded", timeout: 120_000 });
        await page.waitForTimeout(5_000);
        continue;
      }

      const excerpt = bodyText.slice(0, 240) || "empty body";
      throw new Error(
        `Workspace did not become ready for ${sessionId} after ${attempt} attempt(s): ${excerpt}`,
        { cause: error },
      );
    }
  }
}

function hasTransientWorkspaceBody(bodyText) {
  const hasWorkspaceSummary =
    bodyText.includes("Кандидаты на проверку") ||
    bodyText.includes("Точки") ||
    bodyText.includes("Авторазметка");
  if (hasWorkspaceSummary) {
    return false;
  }

  return (
    bodyText.length === 0 ||
    bodyText.includes("Загружаю рабочее поле") ||
    bodyText.includes("Подтягиваю сессию и документ для разметки")
  );
}

async function inspectUi(sessionId, expectedReview) {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  try {
    await page.goto(`${webBaseUrl}/sessions/${sessionId}`, {
      waitUntil: "domcontentloaded",
      timeout: 120_000,
    });
    let bodyText = "";
    for (let attempt = 1; attempt <= 5; attempt += 1) {
      await page.waitForTimeout(5_000);
      await waitForWorkspaceReady(page, sessionId);
      await page.waitForTimeout(3_000);
      bodyText = await readBodyText(page);
      if (!hasTransientWorkspaceBody(bodyText)) {
        break;
      }
      if (attempt === 5) {
        throw new Error(`Workspace never stabilized for ${sessionId}`);
      }
      await page.reload({ waitUntil: "domcontentloaded", timeout: 120_000 });
    }

    const hasReviewSummary =
      bodyText.includes("Кандидаты на проверку") ||
      bodyText.includes("На review") ||
      /^review \d+/im.test(bodyText);
    const hasWorkspaceSummary =
      bodyText.includes("Кандидаты на проверку") ||
      bodyText.includes("Точки") ||
      bodyText.includes("Авторазметка");

    if (expectedReview > 0 && !hasReviewSummary) {
      throw new Error(`Expected review surface for ${sessionId}, but it did not appear`);
    }
    if (!hasWorkspaceSummary) {
      throw new Error(`Expected workspace summary surface for ${sessionId}, but it did not appear`);
    }

    let hasAiReviewPanel = bodyText.includes("AI review");
    let hasConflictReviewSurface =
      bodyText.includes("Конфликт кандидатов") ||
      bodyText.includes("КОНФЛИКТ РЯДОМ");
    if (expectedReview > 0 && !hasAiReviewPanel) {
      const nextCaseButton = page.getByRole("button", { name: /следующий кейс/i }).first();
      if (await nextCaseButton.count()) {
        await nextCaseButton.click();
        await page.waitForTimeout(1_000);
        bodyText = await readBodyText(page);
        hasAiReviewPanel = bodyText.includes("AI review");
        hasConflictReviewSurface =
          bodyText.includes("Конфликт кандидатов") ||
          bodyText.includes("КОНФЛИКТ РЯДОМ");
      }
    }
    if (expectedReview > 0 && !hasAiReviewPanel) {
      const reviewQueueButton = page.getByRole("button", { name: /^review \d+/i }).first();
      if (await reviewQueueButton.count()) {
        await reviewQueueButton.click();
        await page.waitForTimeout(1_000);
        bodyText = await readBodyText(page);
        hasAiReviewPanel = bodyText.includes("AI review");
        hasConflictReviewSurface =
          bodyText.includes("Конфликт кандидатов") ||
          bodyText.includes("КОНФЛИКТ РЯДОМ");
      }
    }
    if (expectedReview > 0 && !hasAiReviewPanel) {
      const aiReviewMarkerButton = page.getByRole("button", { name: /AI review/i }).first();
      if (await aiReviewMarkerButton.count()) {
        await aiReviewMarkerButton.click();
        await page.waitForTimeout(1_000);
        bodyText = await readBodyText(page);
        hasAiReviewPanel = bodyText.includes("AI review");
        hasConflictReviewSurface =
          bodyText.includes("Конфликт кандидатов") ||
          bodyText.includes("КОНФЛИКТ РЯДОМ");
      }
    }

    await page.getByRole("button", { name: "История" }).click();
    const historyDialog = page.getByRole("dialog", { name: "История" });
    await historyDialog.waitFor({ state: "visible", timeout: 30_000 });
    const historyText = (await historyDialog.textContent()) || "";

    const screenshotPath = path.join(smokeDir, `${sessionId}.png`);
    await page.screenshot({ path: screenshotPath, fullPage: true });

    return {
      hasReviewSummary,
      hasWorkspaceSummary,
      hasAiReviewPanel,
      hasConflictReviewSurface,
      hasHistoryDialog: true,
      historyHasAutoAnnotateEntry: historyText.includes("Авторазметка завершена"),
      screenshotPath,
    };
  } catch (error) {
    const debugTextPath = path.join(smokeDir, `${sessionId}.debug.txt`);
    const debugScreenshotPath = path.join(smokeDir, `${sessionId}.debug.png`);
    fs.writeFileSync(debugTextPath, await readBodyText(page));
    await page.screenshot({ path: debugScreenshotPath, fullPage: true }).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
}

async function main() {
  backendPort = await getAvailablePort();
  webPort = await getAvailablePortInRange(3000, 3010);
  backendBaseUrl = `http://127.0.0.1:${backendPort}`;
  webBaseUrl = `http://127.0.0.1:${webPort}`;

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
      label: "live-smoke-backend",
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
    { label: "live-smoke-web" },
  );

  try {
    await waitForJson(`${backendBaseUrl}/api/health`);
    ensureProcessAlive(backend, "Live smoke backend");
    await waitForHttpOk(webBaseUrl);
    ensureProcessAlive(web, "Live smoke web");
    await delay(2_000);

    const results = [];
    for (const scenario of blueprintFiles) {
      ensureProcessAlive(backend, "Live smoke backend");
      ensureProcessAlive(web, "Live smoke web");
      const live = await createLiveSession(scenario.filePath);
      const exportProbe = await probeExport(live.sessionId);
      const ui = await inspectUi(live.sessionId, live.session.summary.aiReview ?? 0);
      const result = {
        filePath: scenario.filePath,
        sessionId: live.sessionId,
        elapsedSeconds: live.elapsedSeconds,
        totalCandidates: live.session.candidates.length,
        totalMarkers: live.session.markers.length,
        aiDetected: live.session.summary.aiDetected,
        aiReview: live.session.summary.aiReview,
        pipelineConflicts: live.session.pipelineConflicts.length,
        missingLabels: live.session.missingLabels.length,
        export: exportProbe,
        ui,
      };
      assertScenario(result, scenario.expect);
      results.push(result);
    }

    const resultsPath = path.join(smokeDir, "latest-results.json");
    fs.writeFileSync(resultsPath, JSON.stringify(results, null, 2));
    console.log(JSON.stringify({ resultsPath, results }, null, 2));
  } finally {
    await killTree(web.pid);
    await killTree(backend.pid);
  }
}

main().catch(async (error) => {
  console.error(String(error?.stack || error));
  process.exitCode = 1;
});
