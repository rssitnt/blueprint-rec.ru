import { expect, test } from "@playwright/test";

const SESSION_ID = "test-ambiguity";
const SESSION_ROUTE = `**/api/sessions/${SESSION_ID}`;
const SESSION_COMMANDS_ROUTE = `**/api/sessions/${SESSION_ID}/commands`;
const SESSION_AUTO_ANNOTATE_ROUTE = `**/api/sessions/${SESSION_ID}/auto-annotate`;
const SESSION_REJECT_CANDIDATE_ROUTE = `**/api/sessions/${SESSION_ID}/candidates/*/reject`;
const SESSION_EXPORT_ROUTE = `**/api/sessions/${SESSION_ID}/export`;
const SESSION_ASSET_ROUTE = "**/mock-doc.png";
const DOC_DATA_URL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9sotmSsAAAAASUVORK5CYII=";

type MockSessionOptions = {
  labels?: string[];
  failInitialLoadCount?: number;
  failCommandCounts?: Record<string, number>;
  autoAnnotateDelayMs?: number;
  exportErrorMessage?: string | null;
};

function createMockSession(options: MockSessionOptions = {}) {
  const createdAt = "2026-04-09T10:00:00.000Z";
  const labels = options.labels ?? ["12"];
  const markers = labels.map((label, index) => {
    const markerId = `marker-${index + 1}`;
    const baseX = 420 + index * 220;
    const baseY = 260 + index * 120;

    return {
      markerId,
      label,
      x: baseX,
      y: baseY,
      pointType: "center" as const,
      status: "ai_review" as const,
      confidence: 0.93 - index * 0.03,
      createdBy: "ai" as const,
      updatedBy: "ai" as const,
      createdAt,
      updatedAt: createdAt
    };
  });

  const candidates = labels.flatMap((label, index) => {
    const baseX = 420 + index * 220;
    const baseY = 260 + index * 120;

    return [
      {
        candidateId: `cand-shape-${index + 1}`,
        kind: "circle" as const,
        centerX: baseX,
        centerY: baseY,
        bboxX: baseX - 40,
        bboxY: baseY - 40,
        bboxWidth: 80,
        bboxHeight: 80,
        score: 0.93 - index * 0.03,
        cropUrl: null,
        suggestedLabel: label,
        suggestedConfidence: 0.97 - index * 0.02,
        suggestedSource: "ocr",
        topologyScore: 0.82 - index * 0.05,
        topologySource: "merged",
        leaderAnchorX: null,
        leaderAnchorY: null,
        reviewStatus: "pending" as const,
        conflictGroup: `ambiguity-${index + 1}`,
        conflictCount: 2,
        createdAt,
        updatedAt: createdAt
      },
      {
        candidateId: `cand-text-${index + 1}`,
        kind: "text" as const,
        centerX: baseX + 50,
        centerY: baseY + 10,
        bboxX: baseX + 30,
        bboxY: baseY - 12,
        bboxWidth: 60,
        bboxHeight: 28,
        score: 0.88 - index * 0.03,
        cropUrl: null,
        suggestedLabel: label,
        suggestedConfidence: 0.91 - index * 0.02,
        suggestedSource: "easyocr",
        topologyScore: 0.7 - index * 0.05,
        topologySource: "merged",
        leaderAnchorX: null,
        leaderAnchorY: null,
        reviewStatus: "pending" as const,
        conflictGroup: `ambiguity-${index + 1}`,
        conflictCount: 2,
        createdAt,
        updatedAt: createdAt
      }
    ];
  });

  const candidateAssociations = labels.map((label, index) => {
    const baseX = 420 + index * 220;
    const baseY = 260 + index * 120;
    return {
      associationId: `assoc-${index + 1}`,
      shapeCandidateId: `cand-shape-${index + 1}`,
      textCandidateId: `cand-text-${index + 1}`,
      shapeKind: "circle" as const,
      label,
      score: 0.84 - index * 0.03,
      geometryScore: 0.8 - index * 0.03,
      topologyScore: 0.72 - index * 0.04,
      source: "merged",
      leaderAnchorX: null,
      leaderAnchorY: null,
      bboxX: baseX - 40,
      bboxY: baseY - 40,
      bboxWidth: 130,
      bboxHeight: 80
    };
  });

  const pageVocabulary = labels.map((label, index) => {
    const baseX = 420 + index * 220;
    const baseY = 260 + index * 120;
    return {
      label,
      normalizedLabel: label,
      occurrences: 1,
      maxConfidence: 0.97 - index * 0.02,
      sources: ["ocr"],
      bboxX: baseX + 30,
      bboxY: baseY - 12,
      bboxWidth: 60,
      bboxHeight: 28
    };
  });

  const pipelineConflicts = labels.map((label, index) => {
    const baseX = 420 + index * 220;
    const baseY = 260 + index * 120;
    return {
      conflictId: `conflict-${index + 1}`,
      type: "association_ambiguity" as const,
      severity: "warning" as const,
      label,
      message: `shape↔text спор для №${label}: рядом две почти равные привязки.`,
      candidateIds: [`cand-shape-${index + 1}`, `cand-text-${index + 1}`],
      markerIds: [`marker-${index + 1}`],
      relatedLabels: [label],
      bboxX: baseX - 40,
      bboxY: baseY - 40,
      bboxWidth: 130,
      bboxHeight: 80
    };
  });

  return {
    sessionId: SESSION_ID,
    title: "Ambiguity Review Fixture",
    state: "draft",
    document: {
      documentId: "doc-1",
      fileName: "fixture.png",
      contentType: "image/png",
      sizeBytes: 1024,
      width: 1400,
      height: 900,
      storageUrl: "/mock-doc.png",
      uploadedAt: createdAt
    },
    viewport: {
      centerX: 700,
      centerY: 450,
      zoom: 1
    },
    candidates,
    candidateAssociations,
    pageVocabulary,
    missingLabels: [],
    pipelineConflicts,
    markers,
    actionLog: [
      {
        actionId: "action-auto-1",
        actor: "ai",
        type: "auto_annotation_completed",
        createdAt,
        payload: {
          candidateCount: candidates.length,
          autoAccepted: 0,
          autoReview: labels.length,
          pendingCandidates: candidates.length
        }
      }
    ],
    summary: {
      totalMarkers: labels.length,
      aiDetected: 0,
      aiReview: labels.length,
      humanConfirmed: 0,
      humanCorrected: 0,
      rejected: 0
    },
    createdAt,
    updatedAt: createdAt
  };
}

async function installSessionFixture(
  page: import("@playwright/test").Page,
  options: MockSessionOptions = {}
) {
  let sequence = 0;
  let rejectedCount = 0;
  let session = createMockSession(options);
  let remainingInitialLoadFailures = options.failInitialLoadCount ?? 0;
  const remainingCommandFailures = new Map(Object.entries(options.failCommandCounts ?? {}));
  const autoAnnotateDelayMs = options.autoAnnotateDelayMs ?? 0;
  const exportErrorMessage = options.exportErrorMessage ?? null;

  const nextTimestamp = () => `2026-04-09T10:00:${String(sequence++).padStart(2, "0")}.000Z`;

  const rebuildSummary = () => {
    session.summary = {
      totalMarkers: session.markers.length,
      aiDetected: session.markers.filter((marker) => marker.status === "ai_detected").length,
      aiReview: session.markers.filter((marker) => marker.status === "ai_review").length,
      humanConfirmed: session.markers.filter((marker) => marker.status === "human_confirmed").length,
      humanCorrected: session.markers.filter((marker) => marker.status === "human_corrected").length,
      rejected: rejectedCount
    };
  };

  const clearMarkerConflicts = (markerId: string) => {
    session.pipelineConflicts = session.pipelineConflicts.filter((conflict) => !conflict.markerIds.includes(markerId));
  };

  await page.route(SESSION_ROUTE, async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }

    if (remainingInitialLoadFailures > 0) {
      remainingInitialLoadFailures -= 1;
      await route.fulfill({
        status: 500,
        contentType: "text/plain",
        body: "Не удалось открыть сессию."
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ session })
    });
  });

  await page.route(SESSION_COMMANDS_ROUTE, async (route) => {
    const payload = route.request().postDataJSON() as {
      type?: string;
      markerId?: string;
      status?: string;
      candidateId?: string;
      pointType?: "center" | "top_left";
      label?: string | null;
      confidence?: number | null;
    };
    const failureKey = `${payload.type ?? "unknown"}:${payload.markerId ?? "*"}`;
    const remainingFailures = remainingCommandFailures.get(failureKey) ?? 0;

    if (remainingFailures > 0) {
      remainingCommandFailures.set(failureKey, remainingFailures - 1);
      await route.fulfill({
        status: 500,
        contentType: "text/plain",
        body: `Команда ${payload.type ?? "unknown"} временно недоступна.`
      });
      return;
    }

    const marker = session.markers.find((item) => item.markerId === payload.markerId) ?? null;
    const candidate = session.candidates.find((item) => item.candidateId === payload.candidateId) ?? null;

    if (payload.type === "place_marker" && candidate) {
      const createdAt = nextTimestamp();
      const newMarker = {
        markerId: `marker-created-${sequence}`,
        label: payload.label ?? candidate.suggestedLabel ?? null,
        x: candidate.centerX,
        y: candidate.centerY,
        pointType: payload.pointType ?? "center",
        status: payload.status ?? ("human_draft" as const),
        confidence: payload.confidence ?? null,
        createdBy: "human" as const,
        updatedBy: "human" as const,
        createdAt,
        updatedAt: createdAt
      };
      candidate.reviewStatus = "accepted";
      session.markers = [...session.markers, newMarker];
      rebuildSummary();
      session.updatedAt = createdAt;
      session.actionLog = [
        {
          actionId: `action-create-${sequence}`,
          actor: "human",
          type: "marker_created",
          createdAt,
          payload: {
            markerId: newMarker.markerId,
            label: newMarker.label,
            pointType: newMarker.pointType,
            status: newMarker.status,
            x: newMarker.x,
            y: newMarker.y
          }
        },
        ...session.actionLog
      ];
    }

    if (payload.type === "update_marker" && marker) {
      const updatedAt = nextTimestamp();
      if (payload.label !== undefined) {
        marker.label = payload.label;
      }
      if (payload.pointType) {
        marker.pointType = payload.pointType;
      }
      if (payload.status) {
        marker.status = payload.status;
      }
      if (payload.confidence !== undefined) {
        marker.confidence = payload.confidence;
      }
      marker.updatedBy = "human";
      marker.updatedAt = updatedAt;
      rebuildSummary();
      session.updatedAt = updatedAt;
      session.actionLog = [
        {
          actionId: `action-update-${sequence}`,
          actor: "human",
          type: "marker_updated",
          createdAt: updatedAt,
          payload: {
            markerId: marker.markerId,
            label: marker.label,
            pointType: marker.pointType,
            status: marker.status,
            confidence: marker.confidence
          }
        },
        ...session.actionLog
      ];
    }

    if (payload.type === "confirm_marker" && marker) {
      const confirmedAt = nextTimestamp();
      marker.status = payload.status === "human_corrected" ? "human_corrected" : "human_confirmed";
      marker.updatedBy = "human";
      marker.updatedAt = confirmedAt;
      clearMarkerConflicts(marker.markerId);
      rebuildSummary();
      session.updatedAt = confirmedAt;
      session.actionLog = [
        {
          actionId: `action-confirm-${sequence}`,
          actor: "human",
          type: "marker_confirmed",
          createdAt: confirmedAt,
          payload: {
            markerId: marker.markerId,
            label: marker.label,
            pointType: marker.pointType,
            status: marker.status,
            x: marker.x,
            y: marker.y
          }
        },
        ...session.actionLog
      ];
    }

    if (payload.type === "delete_marker" && marker) {
      const deletedAt = nextTimestamp();
      session.markers = session.markers.filter((item) => item.markerId !== marker.markerId);
      clearMarkerConflicts(marker.markerId);
      rejectedCount += 1;
      rebuildSummary();
      session.updatedAt = deletedAt;
      session.actionLog = [
        {
          actionId: `action-delete-${sequence}`,
          actor: "human",
          type: "marker_deleted",
          createdAt: deletedAt,
          payload: {
            markerId: marker.markerId,
            label: marker.label
          }
        },
        ...session.actionLog
      ];
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ session })
    });
  });

  await page.route(SESSION_AUTO_ANNOTATE_ROUTE, async (route) => {
    if (autoAnnotateDelayMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, autoAnnotateDelayMs));
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ session })
    });
  });

  await page.route(SESSION_REJECT_CANDIDATE_ROUTE, async (route) => {
    const candidateId = route.request().url().split("/candidates/")[1]?.split("/reject")[0] ?? "";
    const candidate = session.candidates.find((item) => item.candidateId === candidateId) ?? null;

    if (candidate) {
      candidate.reviewStatus = "rejected";
      session.updatedAt = nextTimestamp();
      session.actionLog = [
        {
          actionId: `action-reject-${sequence}`,
          actor: "human",
          type: "candidate_rejected",
          createdAt: session.updatedAt,
          payload: {
            candidateId: candidate.candidateId,
            label: candidate.suggestedLabel
          }
        },
        ...session.actionLog
      ];
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ session })
    });
  });

  await page.route(SESSION_EXPORT_ROUTE, async (route) => {
    if (exportErrorMessage) {
      await route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({ detail: exportErrorMessage })
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/zip",
      body: Buffer.from("PK\x05\x06" + "\0".repeat(18), "binary")
    });
  });

  await page.route(SESSION_ASSET_ROUTE, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "image/png",
      body: Buffer.from(DOC_DATA_URL.split(",")[1] ?? "", "base64")
    });
  });
}

async function openHistory(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: "История" }).click();
  return page.getByRole("dialog", { name: "История" });
}

async function openFixtureSession(page: import("@playwright/test").Page) {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      await page.goto(`/sessions/${SESSION_ID}`, { waitUntil: "domcontentloaded" });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const hasTransientConnectionError =
        message.includes("ERR_CONNECTION_REFUSED") ||
        message.includes("ERR_CONNECTION_RESET") ||
        message.includes("ERR_ABORTED");

      if (attempt === 4 || !hasTransientConnectionError) {
        throw error;
      }

      await page.waitForTimeout(2000);
      continue;
    }

    try {
      await expect(page.getByRole("button", { name: "История" })).toBeVisible({ timeout: 10_000 });
      return;
    } catch (error) {
      const bodyText = (await page.locator("body").textContent()) ?? "";
      const hasTransientNextError =
        bodyText.includes("missing required error components") ||
        bodyText.includes("Internal Server Error") ||
        bodyText.includes("This page could not be found");

      if (attempt === 4 || !hasTransientNextError) {
        throw error;
      }

      await page.waitForTimeout(2000);
    }
  }
}

test.describe("ambiguity review flow", () => {
  test.describe.configure({ timeout: 60_000 });

  test.beforeEach(async ({ page }) => {
    page.on("pageerror", (error) => {
      console.error("[pageerror]", error.message);
    });

    page.on("console", (message) => {
      if (message.type() === "error") {
        console.error("[console:error]", message.text());
      }
    });
  });

  test("skip moves ambiguity case into deferred flow and confirm closes it through history", async ({ page }) => {
    await installSessionFixture(page);
    await openFixtureSession(page);

    await expect(page.getByText("AI review").first()).toBeVisible();
    await expect(page.getByText("Review-проход")).toBeVisible();

    await page.getByRole("button", { name: "Пропустить" }).click();

    await expect(page.getByText("Текущий проход завершён")).toBeVisible();
    await page.getByRole("button", { name: /открыть отложенные 1/i }).click();

    await expect(page.locator("span", { hasText: "Отложенный проход" }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Подтвердить и дальше" })).toBeVisible();

    await page.getByRole("button", { name: "Подтвердить и дальше" }).click();

    const historyDialog = page.getByRole("dialog", { name: "История" });
    await expect(historyDialog).toBeVisible();
    await expect(historyDialog.getByText("подтверждено 1", { exact: true }).first()).toBeVisible();
    await expect(historyDialog.getByText("отложено 1", { exact: true }).first()).toBeVisible();
    await expect(historyDialog.getByText("возвращено 1", { exact: true }).first()).toBeVisible();
    await expect(historyDialog.getByText("Все спорные точки в этой сессии уже закрыты.").first()).toBeVisible();
  });

  test("delete from ambiguity review opens history with false-positive resolution", async ({ page }) => {
    await installSessionFixture(page);
    await openFixtureSession(page);

    await expect(page.getByText("AI review").first()).toBeVisible();
    await page.getByRole("button", { name: "Ложный и дальше" }).click();

    const historyDialog = page.getByRole("dialog", { name: "История" });
    await expect(historyDialog).toBeVisible();
    await expect(historyDialog.getByText("Спорная AI-точка удалена как ложная").first()).toBeVisible();
    await expect(historyDialog.getByText("Все спорные точки в этой сессии уже закрыты.").first()).toBeVisible();
  });

  test("direct confirm keeps remaining ambiguity marker in active review queue", async ({ page }) => {
    await installSessionFixture(page, { labels: ["12", "34"] });
    await openFixtureSession(page);

    await expect(page.locator("span", { hasText: "Review-проход" }).first()).toBeVisible();
    await page.getByRole("button", { name: "Подтвердить и дальше" }).click();

    await expect(page.locator("span", { hasText: "Review-проход" }).first()).toBeVisible();
    await expect(page.getByText("Сейчас: 1 из 1")).toBeVisible();

    const historyDialog = await openHistory(page);
    await expect(historyDialog).toContainText("Спорная AI-точка подтверждена");
    await historyDialog.getByRole("button", { name: "следующий кейс" }).first().click();

    await expect(historyDialog).toBeHidden();
    await expect(page.locator("span", { hasText: "Review-проход" }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Пропустить" })).toBeVisible();
  });

  test("keyboard review flow navigates, confirms, and deletes without leaving the main queue context", async ({ page }) => {
    await installSessionFixture(page, { labels: ["12", "34"] });
    await openFixtureSession(page);

    await expect(page.getByText("Сейчас: 1 из 2")).toBeVisible();

    await page.keyboard.press("ArrowRight");
    await expect(page.getByText("Сейчас: 2 из 2")).toBeVisible();

    await page.keyboard.press("a");
    await expect(page.getByText("Сейчас: 1 из 2")).toBeVisible();

    await page.keyboard.press("d");
    await expect(page.getByText("Сейчас: 2 из 2")).toBeVisible();

    await page.keyboard.press("Enter");
    await expect(page.locator("span", { hasText: "Review-проход" }).first()).toBeVisible();
    await expect(page.getByText("Сейчас: 1 из 1")).toBeVisible();

    await page.keyboard.press("Delete");

    const historyDialog = await openHistory(page);
    await expect(historyDialog).toBeVisible();
    await expect(historyDialog).toContainText("Спорная AI-точка подтверждена");
    await expect(historyDialog).toContainText("Спорная AI-точка удалена как ложная");
    await expect(historyDialog).toContainText("Все спорные точки в этой сессии уже закрыты.");
  });

  test("history jump returns to deferred queue context for a skipped marker", async ({ page }) => {
    await installSessionFixture(page, { labels: ["12", "34"] });
    await openFixtureSession(page);

    await page.getByRole("button", { name: "Пропустить" }).click();
    await expect(page.locator("span", { hasText: "Review-проход" }).first()).toBeVisible();

    const historyDialog = await openHistory(page);
    await expect(historyDialog).toBeVisible();
    await historyDialog.getByRole("button", { name: /к точке/i }).first().click();

    await expect(historyDialog).toBeHidden();
    await expect(page.locator("span", { hasText: "Отложенный проход" }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Пропуск не нужен" })).toBeDisabled();
  });

  test("history next-case CTA resumes the live review queue from deferred mode", async ({ page }) => {
    await installSessionFixture(page, { labels: ["12", "34"] });
    await openFixtureSession(page);

    await page.getByRole("button", { name: "Пропустить" }).click();

    let historyDialog = await openHistory(page);
    await historyDialog.getByRole("button", { name: "в отложенные 1" }).first().click();

    await expect(historyDialog).toBeHidden();
    await expect(page.locator("span", { hasText: "Отложенный проход" }).first()).toBeVisible();

    historyDialog = await openHistory(page);
    await historyDialog.getByRole("button", { name: "следующий кейс" }).first().click();

    await expect(historyDialog).toBeHidden();
    await expect(page.locator("span", { hasText: "Review-проход" }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Пропустить" })).toBeVisible();
  });

  test("history queue switching lets operator move between review and deferred passes", async ({ page }) => {
    await installSessionFixture(page, { labels: ["12", "34"] });
    await openFixtureSession(page);

    await page.getByRole("button", { name: "Пропустить" }).click();

    let historyDialog = await openHistory(page);
    await historyDialog.getByRole("button", { name: "в отложенные 1" }).first().click();

    await expect(page.locator("span", { hasText: "Отложенный проход" }).first()).toBeVisible();

    historyDialog = await openHistory(page);
    await historyDialog.getByRole("button", { name: "в review 1" }).first().click();

    await expect(historyDialog).toBeHidden();
    await expect(page.locator("span", { hasText: "Review-проход" }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Пропустить" })).toBeVisible();
  });

  test("left rail queue counters stay in sync across skip, delete, and confirm", async ({ page }) => {
    await installSessionFixture(page, { labels: ["12", "34", "56"] });
    await openFixtureSession(page);

    await expect(page.getByRole("button", { name: "review 3", exact: true })).toBeVisible();
    await expect(page.getByText("Всего спорных: 3")).toBeVisible();

    await page.getByRole("button", { name: "Пропустить" }).click();
    await expect(page.getByRole("button", { name: "review 2", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "отложенные 1", exact: true })).toBeVisible();
    await expect(page.getByText("Отложено: 1")).toBeVisible();

    await page.getByRole("button", { name: "Ложный и дальше" }).click();
    await expect(page.getByRole("button", { name: "review 1", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "отложенные 1", exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Подтвердить и дальше" }).click();
    await expect(page.getByRole("button", { name: "review 0", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "отложенные 1", exact: true })).toBeVisible();
    await expect(page.getByText("Текущий проход завершён")).toBeVisible();
  });

  test("history keeps the sticky toolbar visible and switches to compact mode on long scroll", async ({ page }) => {
    await installSessionFixture(page, {
      labels: ["11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22"]
    });
    await openFixtureSession(page);

    for (let step = 0; step < 11; step += 1) {
      await page.keyboard.press(step % 2 === 0 ? "Enter" : "Delete");
      await expect(page.locator("span", { hasText: "Review-проход" }).first()).toBeVisible();
    }

    await page.keyboard.press("Delete");

    const historyDialog = await openHistory(page);
    await expect(historyDialog).toBeVisible();

    const compactButton = historyDialog.getByRole("button", { name: "компактно" });
    const scrollArea = historyDialog.locator("div.min-h-0.flex-1.overflow-y-auto.pr-1");

    await expect(compactButton).toHaveAttribute("aria-pressed", "false");
    await expect(historyDialog.getByText(/^Последних действий:/)).toBeVisible();
    await expect(historyDialog.getByText("full", { exact: true })).toBeVisible();

    await scrollArea.evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    await expect.poll(async () => scrollArea.evaluate((element) => element.scrollTop)).toBeGreaterThan(24);

    await expect(historyDialog.getByText(/^Последних действий:/)).not.toBeVisible();
    await expect(historyDialog.getByText("compact", { exact: true })).toBeVisible();
    await expect(compactButton).toBeVisible();

    await compactButton.click();
    await expect(compactButton).toHaveAttribute("aria-pressed", "true");
    await scrollArea.evaluate((element) => {
      element.scrollTop = 0;
    });
    await expect(historyDialog.getByText("compact", { exact: true })).toBeVisible();
  });

  test("load error offers retry and recovers the workspace", async ({ page }) => {
    await installSessionFixture(page, { failInitialLoadCount: 1 });
    await page.goto(`/sessions/${SESSION_ID}`, { waitUntil: "domcontentloaded" });

    await expect(page.getByText("Не удалось открыть рабочее поле")).toBeVisible();
    await page.getByRole("button", { name: "Повторить загрузку" }).click();

    await expect(page.getByText("Review-проход")).toBeVisible();
    await expect(page.getByRole("button", { name: "История" })).toBeVisible();
  });

  test("workspace error banner can reload the session after a failed command", async ({ page }) => {
    await installSessionFixture(page, {
      labels: ["12", "34"],
      failCommandCounts: { "confirm_marker:marker-1": 1 }
    });
    await openFixtureSession(page);

    await page.getByRole("button", { name: "Подтвердить и дальше" }).click();

    await expect(page.getByText("Команда confirm_marker временно недоступна.")).toBeVisible();
    await page.getByRole("button", { name: "Обновить сессию" }).click();

    await expect(page.getByText("Команда confirm_marker временно недоступна.")).not.toBeVisible();
    await expect(page.locator("span", { hasText: "Review-проход" }).first()).toBeVisible();
    await expect(page.getByText("Сейчас: 1 из 2")).toBeVisible();
  });

  test("blocked export shows backend message in workspace error banner", async ({ page }) => {
    await installSessionFixture(page, {
      exportErrorMessage: "Export blocked until pipeline conflicts are resolved: missing_vocab_label:29A"
    });
    await openFixtureSession(page);

    await page.getByRole("button", { name: "Экспорт ZIP" }).click();

    await expect(page.getByText("Не получилось синхронизировать сессию.")).toBeVisible();
    await expect(page.getByText("Export blocked until pipeline conflicts are resolved: missing_vocab_label:29A")).toBeVisible();
  });

  test("export button triggers archive download on success", async ({ page }) => {
    await installSessionFixture(page);
    await openFixtureSession(page);

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "Экспорт ZIP" }).click();
    const download = await downloadPromise;

    expect(download.suggestedFilename()).toBe("Ambiguity Review Fixture-export.zip");
  });

  test("rejecting a candidate keeps the operator in candidate review mode", async ({ page }) => {
    await installSessionFixture(page, { labels: ["12", "34"] });
    await openFixtureSession(page);

    await page.getByRole("button", { name: "№ 12" }).first().click();
    await expect(page.getByText("Почему открыт этот режим").first()).toBeVisible();
    await expect(page.getByText(/кандидат\s+1\s+из\s+4/i)).toBeVisible();
    await expect(page.getByRole("button", { name: "к AI review 2" }).first()).toBeVisible();
    await expect(page.getByText("Кандидат", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Создать точку" })).toBeVisible();

    await page.getByRole("button", { name: "Ложный" }).click();

    await expect(page.getByText("Кандидат", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Создать точку" })).toBeVisible();
    await expect(page.locator("span", { hasText: "Review-проход" }).first()).not.toBeVisible();
  });

  test("candidate review notice can return operator back to ambiguity queue", async ({ page }) => {
    await installSessionFixture(page, { labels: ["12", "34"] });
    await openFixtureSession(page);

    await page.getByRole("button", { name: "№ 12" }).first().click();
    await expect(page.getByText("Почему открыт этот режим").first()).toBeVisible();
    await expect(page.getByText(/кандидат\s+1\s+из\s+4/i)).toBeVisible();

    await page.getByRole("button", { name: "к AI review 2" }).first().click();

    await expect(page.locator("span", { hasText: "Review-проход" }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Пропустить" })).toBeVisible();
  });

  test("candidate can become a draft marker, keep focus, and save an updated label on confirm", async ({ page }) => {
    await installSessionFixture(page, { labels: ["12", "34"] });
    await openFixtureSession(page);

    await page.getByRole("button", { name: "№ 12" }).first().click();
    await page.getByRole("button", { name: "Создать точку" }).click();

    await expect(page.getByText("Точка пока черновая. Проверь место через лупу и потом подтверди.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Подтвердить по лупе" })).toBeVisible();

    const labelInput = page.locator("label", { hasText: "ярлык -" }).locator("input");
    await labelInput.fill("12A");
    await expect(labelInput).toHaveValue("12A");

    await page.getByRole("button", { name: "Подтвердить по лупе" }).click();

    const historyDialog = await openHistory(page);
    await expect(historyDialog).toContainText("Точка добавлена");
    await expect(historyDialog).toContainText("Точка подтверждена");
    await expect(historyDialog).toContainText("12A");
  });

  test("auto-annotate lock disables manual candidate actions while background run is active", async ({ page }) => {
    await installSessionFixture(page, {
      labels: ["12", "34"],
      autoAnnotateDelayMs: 900
    });
    await openFixtureSession(page);
    await page.getByRole("button", { name: "№ 12" }).first().click();

    const createButton = page.getByRole("button", { name: "Создать точку" });
    const falseButton = page.getByRole("button", { name: "Ложный" });

    await expect(createButton).toBeEnabled();
    await expect(falseButton).toBeEnabled();

    await page.getByRole("button", { name: "Прогнать" }).click();

    await expect(page.getByRole("button", { name: "Идёт…" })).toBeVisible();
    await expect(createButton).toBeDisabled();
    await expect(falseButton).toBeDisabled();

    await expect(page.getByRole("button", { name: "Прогнать" })).toBeVisible();
    await expect(createButton).toBeEnabled();
    await expect(falseButton).toBeEnabled();
  });

  test("failed confirm does not advance or close the current ambiguity case", async ({ page }) => {
    await installSessionFixture(page, {
      labels: ["12", "34"],
      failCommandCounts: { "confirm_marker:marker-1": 1 }
    });
    await openFixtureSession(page);

    await page.getByRole("button", { name: "Подтвердить и дальше" }).click();

    await expect(page.locator("span", { hasText: "Review-проход" }).first()).toBeVisible();
    await expect(page.getByText("Команда confirm_marker временно недоступна.")).toBeVisible();
    await expect(page.getByText("Сейчас: 1 из 2")).toBeVisible();

    const historyDialog = await openHistory(page);
    await expect(historyDialog).not.toContainText("Спорная AI-точка подтверждена");
  });

  test("failed delete does not advance or mark ambiguity case as resolved", async ({ page }) => {
    await installSessionFixture(page, {
      labels: ["12", "34"],
      failCommandCounts: { "delete_marker:marker-1": 1 }
    });
    await openFixtureSession(page);

    await page.getByRole("button", { name: "Ложный и дальше" }).click();

    await expect(page.locator("span", { hasText: "Review-проход" }).first()).toBeVisible();
    await expect(page.getByText("Команда delete_marker временно недоступна.")).toBeVisible();
    await expect(page.getByText("Сейчас: 1 из 2")).toBeVisible();

    const historyDialog = await openHistory(page);
    await expect(historyDialog).not.toContainText("Спорная AI-точка удалена как ложная");
  });
});
