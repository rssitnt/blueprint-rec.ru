"use client";

import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { createBatchJobs, createJob, createJobPreviewSession, deleteJob, getBatch, getJob, listBatches, listJobs, resolveAssetUrl, resolveBatchExportUrl } from "@/lib/api";
import type { BatchJobWarning, BatchListItem, DrawingJob, DrawingJobBatch, JobListItem } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

const statusLabels: Record<DrawingJob["status"], string> = {
  queued: "В очереди",
  running: "Обрабатывается",
  completed: "Готово",
  failed: "Ошибка"
};

const statusClasses: Record<DrawingJob["status"], string> = {
  queued: "border-transparent bg-[#2f251b] text-[#e9d0a2]",
  running: "border-transparent bg-[#2a211d] text-[#f0d6bf]",
  completed: "border-transparent bg-[#1f2a22] text-[#a9e1bb]",
  failed: "border-transparent bg-[#311d1c] text-[#ffb2ab]"
};

function formatDate(value: string) {
  try {
    return new Date(value).toLocaleString("ru-RU");
  } catch {
    return value;
  }
}

function formatConfidence(value: number | null | undefined) {
  if (value == null) {
    return "—";
  }
  return `${Math.round(value * 100)}%`;
}

function downloadLinkClass(isMuted = false) {
  return [
    "inline-flex min-h-8 items-center justify-center rounded-full px-3 text-xs font-semibold",
    isMuted
      ? "bg-[#1c1714] text-[#a89b90]"
      : "bg-[#27201c] text-[#f6efe8]"
  ].join(" ");
}

function humanizeTechnicalMessage(message: string | null | undefined) {
  const text = (message ?? "").trim();
  if (!text) {
    return null;
  }

  const lowered = text.toLowerCase();
  if (lowered.includes("legacy pipeline")) {
    return "Старый маршрут обработки недоступен. Система переключила задачу на внутренний режим.";
  }
  if (lowered.includes("ocr near-tie") || lowered.includes("near-tie")) {
    return "Есть спорные номера, где автоматике не хватило уверенности. Их лучше проверить вручную.";
  }
  if (lowered.includes("tile-gemini") || lowered.includes("gemini")) {
    return "Часть номеров найдена в сложном режиме. Эти места лучше проверить вручную.";
  }
  if (lowered.includes("timeout")) {
    return "Один из внутренних шагов не успел завершиться вовремя. Результат лучше проверить вручную.";
  }
  if (lowered.includes("ocr") || lowered.includes("pipeline")) {
    return "Автоматический разбор отработал с ограничениями. Результат лучше проверить вручную.";
  }
  if (text.includes("C:\\") || text.includes("C:/")) {
    return "Во время обработки произошла внутренняя ошибка. Попробуй запустить задачу ещё раз.";
  }
  return text;
}

function humanizeReviewReason(reason: string) {
  const text = reason.trim();
  const lowered = text.toLowerCase();
  if (!text) {
    return null;
  }
  if (lowered.includes("ocr near-tie") || lowered.includes("near-tie")) {
    return "Есть несколько спорных номеров, где внутри одной области автоматике было трудно выбрать правильную цифру.";
  }
  if (lowered.includes("tile-gemini") || lowered.includes("gemini")) {
    return "Часть точек определялась в тяжёлом режиме. Их лучше проверить вручную.";
  }
  if (lowered.includes("weak") || lowered.includes("слаб")) {
    return "Часть найденных точек выглядит слишком слабо и требует ручной проверки.";
  }
  if (lowered.includes("ocr")) {
    return "Автоматический разбор не везде был уверенным. Результат стоит проверить вручную.";
  }
  return text;
}

function getFileExtension(file: File) {
  const match = /\.([^.]+)$/.exec(file.name);
  return match ? match[1].toLowerCase() : "";
}

function getBaseName(file: File) {
  return file.name.replace(/\.[^.]+$/, "");
}

function isArchiveFile(file: File) {
  const extension = getFileExtension(file);
  const normalizedName = file.name.toLowerCase();
  return (
    extension === "zip" ||
    extension === "rar" ||
    extension === "7z" ||
    extension === "tar" ||
    normalizedName.endsWith(".tar.gz") ||
    normalizedName.endsWith(".tgz") ||
    normalizedName.endsWith(".tar.bz2") ||
    normalizedName.endsWith(".tbz2") ||
    normalizedName.endsWith(".tar.xz") ||
    normalizedName.endsWith(".txz")
  );
}

function isDrawingFile(file: File) {
  const extension = getFileExtension(file);
  return ["pdf", "png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"].includes(extension);
}

function isLabelsFile(file: File) {
  const extension = getFileExtension(file);
  return ["xlsx", "csv", "txt", "tsv"].includes(extension);
}

export function JobHome() {
  const router = useRouter();
  const [uploadMode, setUploadMode] = useState<"drawing" | "archive">("drawing");
  const [title, setTitle] = useState("");
  const [drawingFile, setDrawingFile] = useState<File | null>(null);
  const [labelsFile, setLabelsFile] = useState<File | null>(null);
  const [batchArchive, setBatchArchive] = useState<File | null>(null);
  const [isDropActive, setIsDropActive] = useState(false);
  const [jobs, setJobs] = useState<JobListItem[]>([]);
  const [batches, setBatches] = useState<BatchListItem[]>([]);
  const [activeBatch, setActiveBatch] = useState<{ batch: DrawingJobBatch; jobs: JobListItem[] } | null>(null);
  const [activeJob, setActiveJob] = useState<DrawingJob | null>(null);
  const [listTab, setListTab] = useState<"jobs" | "batches">("jobs");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isBatchSubmitting, setIsBatchSubmitting] = useState(false);
  const [deletingJobId, setDeletingJobId] = useState<string | null>(null);
  const [openingPreviewJobId, setOpeningPreviewJobId] = useState<string | null>(null);
  const [batchWarnings, setBatchWarnings] = useState<BatchJobWarning[]>([]);
  const [batchCreatedCount, setBatchCreatedCount] = useState<number | null>(null);
  const [trackedBatchJobIds, setTrackedBatchJobIds] = useState<string[]>([]);
  const [selectedResultPageIndex, setSelectedResultPageIndex] = useState(0);
  const [showSecondaryDownloads, setShowSecondaryDownloads] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAllJobs, setShowAllJobs] = useState(false);
  const [showAllBatches, setShowAllBatches] = useState(false);
  const notifiedBatchKeyRef = useRef<string | null>(null);
  const pendingAttentionTitleRef = useRef<string | null>(null);
  const activeBatchRef = useRef<HTMLDivElement | null>(null);
  const shouldScrollToActiveBatchRef = useRef(false);
  const activeResultRef = useRef<HTMLDivElement | null>(null);
  const shouldScrollToActiveResultRef = useRef(false);
  const dragDepthRef = useRef(0);

  const activeFailureMessage = humanizeTechnicalMessage(activeJob?.result?.summary.failureMessage ?? activeJob?.errorMessage);
  const activeReviewReasons = useMemo(
    () =>
      (activeJob?.result?.summary.reviewReasons ?? [])
        .map((reason) => humanizeReviewReason(reason))
        .filter((reason): reason is string => Boolean(reason)),
    [activeJob?.result?.summary.reviewReasons]
  );
  const hasReviewNotice = activeReviewReasons.length > 0;

  const applyDroppedFiles = useCallback((files: File[]) => {
    const archives = files.filter(isArchiveFile);
    const drawings = files.filter(isDrawingFile);
    const labels = files.filter(isLabelsFile);

    setError(null);

    if (archives.length > 0) {
      const archive = archives[0];
      setUploadMode("archive");
      setBatchArchive(archive);
      setDrawingFile(null);
      setLabelsFile(null);
      if (!title.trim()) {
        setTitle(getBaseName(archive));
      }
      if (archives.length > 1) {
        setError("Взял первый архив из нескольких.");
      }
      return;
    }

    if (drawings.length > 0 || labels.length > 0) {
      const drawing = drawings[0] ?? null;
      const labelsSheet = labels[0] ?? null;

      setUploadMode("drawing");
      setBatchArchive(null);
      setDrawingFile(drawing);
      setLabelsFile(labelsSheet);

      if (!title.trim()) {
        if (drawing) {
          setTitle(getBaseName(drawing));
        } else if (labelsSheet) {
          setTitle(getBaseName(labelsSheet));
        }
      }

      if (drawings.length > 1 || labels.length > 1) {
        setError("Взял первый подходящий файл каждого типа.");
      }
      return;
    }

    setError("Этот формат сюда не подходит.");
  }, [title]);

  async function refreshJobs() {
    const response = await listJobs();
    setJobs(response.jobs);
  }

  async function refreshBatches() {
    const response = await listBatches();
    setBatches(response.batches);
  }

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [jobsResponse, batchesResponse] = await Promise.all([listJobs(), listBatches()]);
        if (cancelled) {
          return;
        }
        setJobs(jobsResponse.jobs);
        setBatches(batchesResponse.batches);
        if (jobsResponse.jobs[0]?.jobId) {
          const latest = await getJob(jobsResponse.jobs[0].jobId);
          if (!cancelled) {
            setActiveJob(latest.job);
          }
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Не удалось загрузить список задач.");
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!activeJob || (activeJob.status !== "queued" && activeJob.status !== "running")) {
      return;
    }

    let cancelled = false;
    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const response = await getJob(activeJob.jobId);
          if (cancelled) {
            return;
          }
          setActiveJob(response.job);
          if (response.job.status === "completed" || response.job.status === "failed") {
            await refreshJobs();
          }
        } catch (pollError) {
          if (!cancelled) {
            setError(pollError instanceof Error ? pollError.message : "Не удалось обновить статус задачи.");
          }
        }
      })();
    }, 2500);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeJob]);

  useEffect(() => {
    if (typeof document === "undefined") {
      return;
    }

    const baseTitle = document.title;
    const handleVisibility = () => {
      if (document.visibilityState === "visible") {
        pendingAttentionTitleRef.current = null;
        document.title = baseTitle;
        return;
      }
      if (pendingAttentionTitleRef.current) {
        document.title = pendingAttentionTitleRef.current;
      }
    };

    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibility);
      document.title = baseTitle;
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const handleDragEnter = (event: DragEvent) => {
      if (!event.dataTransfer?.types.includes("Files")) {
        return;
      }
      event.preventDefault();
      dragDepthRef.current += 1;
      setIsDropActive(true);
    };

    const handleDragOver = (event: DragEvent) => {
      if (!event.dataTransfer?.types.includes("Files")) {
        return;
      }
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      if (!isDropActive) {
        setIsDropActive(true);
      }
    };

    const handleDragLeave = (event: DragEvent) => {
      if (!event.dataTransfer?.types.includes("Files")) {
        return;
      }
      event.preventDefault();
      dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
      if (dragDepthRef.current === 0) {
        setIsDropActive(false);
      }
    };

    const handleDrop = (event: DragEvent) => {
      if (!event.dataTransfer?.files?.length) {
        return;
      }
      event.preventDefault();
      dragDepthRef.current = 0;
      setIsDropActive(false);
      applyDroppedFiles(Array.from(event.dataTransfer.files));
    };

    window.addEventListener("dragenter", handleDragEnter);
    window.addEventListener("dragover", handleDragOver);
    window.addEventListener("dragleave", handleDragLeave);
    window.addEventListener("drop", handleDrop);

    return () => {
      window.removeEventListener("dragenter", handleDragEnter);
      window.removeEventListener("dragover", handleDragOver);
      window.removeEventListener("dragleave", handleDragLeave);
      window.removeEventListener("drop", handleDrop);
    };
  }, [applyDroppedFiles, isDropActive]);

  useEffect(() => {
    setSelectedResultPageIndex(0);
  }, [activeJob?.jobId]);

  useEffect(() => {
    setShowSecondaryDownloads(false);
  }, [activeJob?.jobId]);

  useEffect(() => {
    if (!activeBatch || !shouldScrollToActiveBatchRef.current) {
      return;
    }

    shouldScrollToActiveBatchRef.current = false;
    const frame = window.requestAnimationFrame(() => {
      activeBatchRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "start"
      });
    });

    return () => {
      window.cancelAnimationFrame(frame);
    };
  }, [activeBatch]);

  useEffect(() => {
    if (!activeJob || !shouldScrollToActiveResultRef.current) {
      return;
    }

    shouldScrollToActiveResultRef.current = false;
    const frame = window.requestAnimationFrame(() => {
      activeResultRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "start"
      });
    });

    return () => {
      window.cancelAnimationFrame(frame);
    };
  }, [activeJob]);

  useEffect(() => {
    const hasTrackedBatchInFlight = trackedBatchJobIds.some((jobId) => {
      const job = jobs.find((item) => item.jobId === jobId);
      return job != null && (job.status === "queued" || job.status === "running");
    });
    const hasActiveBatchInFlight = Boolean(activeBatch && !activeBatch.batch.summary.finished);

    if (!hasTrackedBatchInFlight && !hasActiveBatchInFlight) {
      return;
    }

    let cancelled = false;
    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const [jobsResponse, batchesResponse] = await Promise.all([listJobs(), listBatches()]);
          if (cancelled) {
            return;
          }
          setJobs(jobsResponse.jobs);
          setBatches(batchesResponse.batches);
          if (activeBatch) {
            const batchResponse = await getBatch(activeBatch.batch.batchId);
            if (!cancelled) {
              setActiveBatch(batchResponse);
            }
          }
        } catch (pollError) {
          if (!cancelled) {
            setError(pollError instanceof Error ? pollError.message : "Не удалось обновить batch-статусы.");
          }
        }
      })();
    }, 3000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeBatch, jobs, trackedBatchJobIds]);

  async function submitDrawingJob() {
    if (!drawingFile) {
      setError("Сначала выбери чертёж.");
      return;
    }
    setIsSubmitting(true);
    setError(null);

    try {
      const response = await createJob({
        title: title.trim() || drawingFile.name.replace(/\.[^.]+$/, ""),
        drawing: drawingFile,
        labels: labelsFile
      });
      setActiveJob(response.job);
      setListTab("jobs");
      setDrawingFile(null);
      setLabelsFile(null);
      setBatchWarnings([]);
      setBatchCreatedCount(null);
      await refreshJobs();
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Не удалось запустить обработку.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function submitBatchJob() {
    if (!batchArchive) {
      setError("Сначала выбери архив.");
      return;
    }

    setIsBatchSubmitting(true);
    setError(null);

    try {
      const response = await createBatchJobs({
        archive: batchArchive,
        titlePrefix: title.trim() || undefined
      });
      setBatchArchive(null);
      setBatchWarnings(response.warnings);
      setBatchCreatedCount(response.jobs.length);
      setTrackedBatchJobIds(response.jobs.map((job) => job.jobId));
      notifiedBatchKeyRef.current = null;
      setListTab("batches");
      if (response.batchId) {
        const batchResponse = await getBatch(response.batchId);
        setActiveBatch(batchResponse);
      }
      if (response.jobs[0]) {
        setActiveJob(response.jobs[0]);
      }
      if (typeof window !== "undefined" && "Notification" in window && Notification.permission === "default") {
        void Notification.requestPermission();
      }
      await refreshJobs();
      await refreshBatches();
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Не удалось запустить пакетную обработку.");
    } finally {
      setIsBatchSubmitting(false);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (batchArchive) {
      await submitBatchJob();
      return;
    }
    await submitDrawingJob();
  }

  function handleDrawingChange(event: ChangeEvent<HTMLInputElement>) {
    const nextFile = event.target.files?.[0] ?? null;
    setDrawingFile(nextFile);
    if (nextFile) {
      setUploadMode("drawing");
      setBatchArchive(null);
    }
    if (nextFile && !title.trim()) {
      setTitle(nextFile.name.replace(/\.[^.]+$/, ""));
    }
  }

  function handleLabelsChange(event: ChangeEvent<HTMLInputElement>) {
    const nextFile = event.target.files?.[0] ?? null;
    setLabelsFile(nextFile);
    if (nextFile) {
      setUploadMode("drawing");
      setBatchArchive(null);
    }
  }

  function handleBatchArchiveChange(event: ChangeEvent<HTMLInputElement>) {
    const nextFile = event.target.files?.[0] ?? null;
    setBatchArchive(nextFile);
    if (nextFile) {
      setUploadMode("archive");
      setDrawingFile(null);
      setLabelsFile(null);
      if (!title.trim()) {
        setTitle(nextFile.name.replace(/\.[^.]+$/, ""));
      }
    }
  }

  async function handleOpenJob(jobId: string) {
    setError(null);
    try {
      const response = await getJob(jobId);
      shouldScrollToActiveResultRef.current = true;
      setActiveJob(response.job);
      setListTab("jobs");
    } catch (openError) {
      setError(openError instanceof Error ? openError.message : "Не удалось открыть задачу.");
    }
  }

  async function handleOpenBatch(batchId: string) {
    setError(null);
    try {
      const response = await getBatch(batchId);
      shouldScrollToActiveBatchRef.current = true;
      setActiveBatch(response);
      setListTab("batches");
    } catch (openError) {
      setError(openError instanceof Error ? openError.message : "Не удалось открыть batch.");
    }
  }

  async function handleDeleteJob(job: JobListItem) {
    const confirmed = window.confirm(`Удалить задачу "${job.title}"?`);
    if (!confirmed) {
      return;
    }

    setDeletingJobId(job.jobId);
    setError(null);
    try {
      await deleteJob(job.jobId);
      if (activeJob?.jobId === job.jobId) {
        setActiveJob(null);
      }
      await refreshJobs();
      await refreshBatches();
      if (activeBatch) {
        const batchResponse = await getBatch(activeBatch.batch.batchId);
        setActiveBatch(batchResponse);
      }
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Не удалось удалить задачу.");
    } finally {
      setDeletingJobId(null);
    }
  }

  async function handleOpenPreview(jobId: string, pageIndex?: number) {
    setOpeningPreviewJobId(jobId);
    setError(null);
    try {
      const response = await createJobPreviewSession(jobId, pageIndex);
      router.push(`/sessions/${response.sessionId}`);
    } catch (previewError) {
      setOpeningPreviewJobId(null);
      setError(previewError instanceof Error ? previewError.message : "Не удалось открыть preview.");
    }
  }

  const activeSummary = activeJob?.result?.summary ?? null;
  const activePages = useMemo(() => activeJob?.result?.pages ?? [], [activeJob]);
  const activePageCount = activeJob?.result?.pages.length ?? 0;
  const selectedResultPage =
    activePages.find((page) => page.pageIndex === selectedResultPageIndex) ??
    activePages[0] ??
    null;
  const canOpenPreview = activeJob?.status === "completed" && (activePageCount <= 1 || selectedResultPage != null);
  const trackedBatchJobs = useMemo(
    () => trackedBatchJobIds.map((jobId) => jobs.find((job) => job.jobId === jobId)).filter((job): job is JobListItem => Boolean(job)),
    [jobs, trackedBatchJobIds]
  );
  const batchProgress = useMemo(() => {
    const total = trackedBatchJobIds.length;
    const queued = trackedBatchJobs.filter((job) => job.status === "queued").length;
    const running = trackedBatchJobs.filter((job) => job.status === "running").length;
    const completed = trackedBatchJobs.filter((job) => job.status === "completed").length;
    const failed = trackedBatchJobs.filter((job) => job.status === "failed").length;
    const degraded = trackedBatchJobs.filter((job) => job.status === "completed" && job.degradedRecognition).length;
    const rescued = trackedBatchJobs.filter((job) => job.status === "completed" && job.emergencyFallbackUsed).length;
    const finished = total > 0 && queued === 0 && running === 0 && completed + failed === total;
    return { total, queued, running, completed, failed, degraded, rescued, finished };
  }, [trackedBatchJobIds, trackedBatchJobs]);
  const jobStatusSummary = useMemo(() => {
    const total = jobs.length;
    const running = jobs.filter((job) => job.status === "queued" || job.status === "running").length;
    const completed = jobs.filter((job) => job.status === "completed").length;
    const failed = jobs.filter((job) => job.status === "failed").length;
    return { total, running, completed, failed };
  }, [jobs]);
  const batchStatusSummary = useMemo(() => {
    const total = batches.length;
    const running = batches.filter((batch) => !batch.summary.finished).length;
    const failed = batches.filter((batch) => batch.summary.failedJobs > 0).length;
    return { total, running, failed };
  }, [batches]);
  const activeBatchSummary = activeBatch?.batch.summary ?? null;
  const activeBatchProductionExportUrl =
    activeBatch && activeBatchSummary?.finished ? resolveBatchExportUrl(activeBatch.batch.batchId, "production") : null;
  const activeBatchReviewExportUrl =
    activeBatch && activeBatchSummary?.finished ? resolveBatchExportUrl(activeBatch.batch.batchId, "review") : null;
  const activeDownloads = useMemo(() => {
    if (!activeJob?.result) {
      return [];
    }
    return [
      ["CSV итог", activeJob.result.artifacts.csvUrl, false],
      ["XLSX итог", activeJob.result.artifacts.xlsxUrl, false],
      ["ZIP итог", activeJob.result.artifacts.zipUrl, false],
      ["CSV review", activeJob.result.artifacts.reviewCsvUrl, true],
      ["XLSX review", activeJob.result.artifacts.reviewXlsxUrl, true],
      ["ZIP review", activeJob.result.artifacts.reviewZipUrl, true],
      ["CSV near-tie", activeJob.result.artifacts.nearTieCsvUrl, true],
      ["JSON near-tie", activeJob.result.artifacts.nearTieJsonUrl, true],
      ["JSON диагностика", activeJob.result.artifacts.resultJsonUrl, true]
    ].filter((entry): entry is [string, string, boolean] => Boolean(entry[1])) as [string, string, boolean][];
  }, [activeJob]);
  const activePrimaryDownloads = useMemo(
    () => activeDownloads.filter(([, , isMuted]) => !isMuted),
    [activeDownloads]
  );
  const activeSecondaryDownloads = useMemo(
    () => activeDownloads.filter(([, , isMuted]) => isMuted),
    [activeDownloads]
  );
  const visibleJobs = useMemo(
    () => (showAllJobs ? jobs : jobs.slice(0, 6)),
    [jobs, showAllJobs]
  );
  const visibleBatches = useMemo(
    () => (showAllBatches ? batches : batches.slice(0, 4)),
    [batches, showAllBatches]
  );

  useEffect(() => {
    if (!batchProgress.finished || batchProgress.total === 0) {
      return;
    }

    const batchKey = trackedBatchJobIds.join(",");
    if (notifiedBatchKeyRef.current === batchKey) {
      return;
    }

    notifiedBatchKeyRef.current = batchKey;
    if (typeof document !== "undefined") {
      pendingAttentionTitleRef.current =
        batchProgress.failed > 0
          ? `Готово: ${batchProgress.completed} ok, ${batchProgress.failed} ошибок`
          : `Готово: batch ${batchProgress.completed}/${batchProgress.total}`;
      if (document.visibilityState !== "visible") {
        document.title = pendingAttentionTitleRef.current;
      }
    }

  }, [batchProgress, trackedBatchJobIds]);

  return (
    <div className="h-full overflow-hidden pr-1 text-[#f6efe8]">
      {isDropActive && (
        <div className="pointer-events-none fixed inset-0 z-50 bg-[#120f0dcc]/95">
          <div className="flex h-full items-center justify-center p-6">
            <div className="rounded-[1.8rem] bg-[#1c1612] px-8 py-7 text-center shadow-none">
              <p className="text-2xl font-semibold text-[#fff6ee]">Отпусти файл</p>
              <p className="mt-2 text-sm text-[#d0c1b1]">
                Архив попадёт в batch, чертёж и таблица разложатся по своим полям автоматически.
              </p>
            </div>
          </div>
        </div>
      )}
      <div className="mx-auto flex min-h-full w-full max-w-[1200px] flex-col gap-4 py-4">
        <section className="space-y-4 rounded-[1.7rem] bg-[#16120f] p-5 md:p-6">
          <div className="space-y-1">
            <h1 className="text-3xl font-semibold tracking-tight text-[#fff8f1]">Новая задача</h1>
          </div>

          <Card className="!rounded-[1.5rem] !border-transparent !bg-[#1b1613] !shadow-none">
            <form className="space-y-3" onSubmit={handleSubmit}>
              <div className="grid gap-3 md:grid-cols-[1.2fr_0.8fr] md:items-end">
                <div className="space-y-2">
                  <label className="text-xs font-semibold uppercase tracking-[0.2em] text-[#c4b6a8]">Название</label>
                  <input
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                    disabled={isSubmitting || isBatchSubmitting}
                    className="w-full rounded-[1rem] border border-transparent bg-[#15110e] px-4 py-3 text-sm text-[#fff8f1] outline-none focus:bg-[#1b1613] focus:ring-2 focus:ring-[#7f644f]/20 disabled:cursor-not-allowed disabled:opacity-60"
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-xs font-semibold uppercase tracking-[0.2em] text-[#c4b6a8]">Режим</label>
                  <div className="grid gap-2 sm:grid-cols-2">
                    <button
                      type="button"
                      onClick={() => {
                        setUploadMode("drawing");
                        setBatchArchive(null);
                      }}
                      className={[
                        "min-h-10 rounded-full px-4 text-sm font-medium transition-none",
                        uploadMode === "drawing"
                          ? "bg-[#2b221d] text-[#fff5eb]"
                          : "bg-[#15110e] text-[#cabcae]"
                      ].join(" ")}
                    >
                      Чертёж
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setUploadMode("archive");
                        setDrawingFile(null);
                        setLabelsFile(null);
                      }}
                      className={[
                        "min-h-10 rounded-full px-4 text-sm font-medium transition-none",
                        uploadMode === "archive"
                          ? "bg-[#2b221d] text-[#fff5eb]"
                          : "bg-[#15110e] text-[#cabcae]"
                      ].join(" ")}
                    >
                      Архив
                    </button>
                  </div>
                </div>
              </div>

              {uploadMode === "drawing" ? (
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-2">
                    <label className="text-xs font-semibold uppercase tracking-[0.2em] text-[#c4b6a8]">Файл чертежа</label>
                    <label className="flex min-h-[4.25rem] cursor-pointer items-center justify-between rounded-[1.1rem] bg-[#15110e] px-4 py-3.5 text-[15px] text-[#efe6dc]">
                      <span className="truncate pr-3">{drawingFile ? drawingFile.name : "Чертёж"}</span>
                      <span className="inline-flex min-h-10 items-center rounded-full bg-[#26201b] px-3.5 text-sm font-medium text-[#fff7ef]">
                        Выбрать
                      </span>
                      <input
                        type="file"
                        accept=".pdf,image/png,image/jpeg,image/webp,image/bmp,image/tiff"
                        className="hidden"
                        disabled={isSubmitting || isBatchSubmitting}
                        onChange={handleDrawingChange}
                      />
                    </label>
                  </div>

                  <div className="space-y-2">
                    <label className="text-xs font-semibold uppercase tracking-[0.2em] text-[#c4b6a8]">Таблица номеров</label>
                    <label className="flex min-h-[4.25rem] cursor-pointer items-center justify-between rounded-[1.1rem] bg-[#15110e] px-4 py-3.5 text-[15px] text-[#efe6dc]">
                      <span className="truncate pr-3">{labelsFile ? labelsFile.name : "Таблица"}</span>
                      <span className="inline-flex min-h-10 items-center rounded-full bg-[#26201b] px-3.5 text-sm font-medium text-[#fff7ef]">
                        Выбрать
                      </span>
                      <input
                        type="file"
                        accept=".xlsx,.csv,.txt,.tsv"
                        className="hidden"
                        disabled={isSubmitting || isBatchSubmitting}
                        onChange={handleLabelsChange}
                      />
                    </label>
                  </div>
                </div>
              ) : (
                <div className="space-y-2">
                  <label className="text-xs font-semibold uppercase tracking-[0.2em] text-[#c4b6a8]">Архив</label>
                  <label className="flex min-h-[4.25rem] cursor-pointer items-center justify-between rounded-[1.1rem] bg-[#15110e] px-4 py-3.5 text-[15px] text-[#efe6dc]">
                    <span className="truncate pr-3">{batchArchive ? batchArchive.name : "Архив"}</span>
                    <span className="inline-flex min-h-10 items-center rounded-full bg-[#26201b] px-3.5 text-sm font-medium text-[#fff7ef]">
                      Выбрать
                    </span>
                    <input
                      type="file"
                      accept=".zip,.tar,.tar.gz,.tgz,.tar.bz2,.tbz2,.tar.xz,.txz,.rar,.7z,application/zip,application/x-tar,application/gzip"
                      className="hidden"
                      disabled={isSubmitting || isBatchSubmitting}
                      onChange={handleBatchArchiveChange}
                    />
                  </label>
                </div>
              )}

              <div className="flex flex-wrap items-center gap-2.5 pt-1">
                <Button
                  type="submit"
                  disabled={isSubmitting || isBatchSubmitting || (uploadMode === "drawing" ? !drawingFile : !batchArchive)}
                  className="min-h-10 rounded-full border-0 bg-[#2b221d] px-4 text-sm font-medium text-[#fff4ea] shadow-none"
                >
                  {isSubmitting ? "Запускаю" : isBatchSubmitting ? "Разбираю архив" : uploadMode === "archive" ? "Запустить batch" : "Запустить распознавание"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={isSubmitting || isBatchSubmitting}
                  className="min-h-10 rounded-full border-0 bg-[#1d1713] px-4 text-sm font-medium text-[#dccfc2] shadow-none"
                  onClick={() => {
                    setTitle("");
                    setDrawingFile(null);
                    setLabelsFile(null);
                    setBatchArchive(null);
                    setUploadMode("drawing");
                    setError(null);
                  }}
                >
                  Сбросить
                </Button>
              </div>

              {error && <p className="text-sm text-[#f18a81]">{error}</p>}
            </form>
          </Card>
        </section>

        <section className="rounded-[1.2rem] bg-[#15110e] px-4 py-3 text-sm text-[#d6c9bc]">
          <div className="flex flex-wrap items-center gap-3">
            <span>{`В работе: ${jobStatusSummary.running}`}</span>
            <span>{`Готово: ${jobStatusSummary.completed}`}</span>
            <span>{`Ошибки: ${jobStatusSummary.failed}`}</span>
            <span>{`Batch: ${batchStatusSummary.total}`}</span>
          </div>
        </section>

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
          <div className="rounded-[1.2rem] bg-[#15110e] p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="inline-flex rounded-full bg-[#1c1714] p-1">
                <button
                  type="button"
                  onClick={() => setListTab("jobs")}
                  className={[
                    "min-h-8 rounded-full px-4 text-sm font-medium transition-none",
                    listTab === "jobs" ? "bg-[#2b221d] text-[#fff5eb]" : "text-[#cabcae]"
                  ].join(" ")}
                >
                  Задачи
                </button>
                <button
                  type="button"
                  onClick={() => setListTab("batches")}
                  className={[
                    "min-h-8 rounded-full px-4 text-sm font-medium transition-none",
                    listTab === "batches" ? "bg-[#2b221d] text-[#fff5eb]" : "text-[#cabcae]"
                  ].join(" ")}
                >
                  Batch
                </button>
              </div>
              <p className="text-xs uppercase tracking-[0.18em] text-[#a99b8e]">
                {listTab === "jobs" ? `${jobStatusSummary.total} задач` : `${batchStatusSummary.total} batch`}
              </p>
            </div>

            <div className="mt-3 max-h-[70vh] overflow-y-auto pr-1 lg:max-h-[calc(100vh-360px)]">
              {listTab === "jobs" ? (
                <div className="space-y-2">
                  {jobs.length === 0 && <p className="text-sm text-[#c5b7a8]">Пока пусто. Запусти первую задачу выше.</p>}
                  {visibleJobs.map((job) => {
                    const isDeleting = deletingJobId === job.jobId;
                    return (
                      <div
                        key={job.jobId}
                        className={[
                          "flex flex-wrap items-start justify-between gap-2.5 rounded-[0.9rem] px-3 py-2",
                          activeJob?.jobId === job.jobId ? "bg-[#261b16]" : "bg-[#17120f]"
                        ].join(" ")}
                      >
                        <button
                          type="button"
                          className="min-w-0 flex-1 text-left"
                          onClick={() => void handleOpenJob(job.jobId)}
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            <span className={`inline-flex min-h-6 items-center rounded-full border px-2 text-[10px] font-semibold ${statusClasses[job.status]}`}>
                              {statusLabels[job.status]}
                            </span>
                            <span className="text-sm font-semibold text-[#fff8f1]">{job.title}</span>
                          </div>
                          <p className="mt-1 text-xs text-[#cbbfb1]">{job.drawingName}</p>
                          <p className="mt-0.5 text-[11px] text-[#aa9a8c]">
                            {formatDate(job.updatedAt)} · {formatConfidence(job.documentConfidence)}
                          </p>
                        </button>
                        <div className="flex shrink-0 items-center gap-2">
                          <Button
                            type="button"
                            variant="outline"
                            className="min-h-7 rounded-full border-0 bg-[#2b221d] px-2.5 text-[11px] font-semibold text-[#fff4ea] shadow-none"
                            onClick={() => void handleOpenJob(job.jobId)}
                          >
                            Открыть
                          </Button>
                          <Button
                            type="button"
                            variant="outline"
                            disabled={isDeleting}
                            className="min-h-7 rounded-full !border-0 !bg-[#2a1715] px-2.5 text-[11px] font-semibold !text-[#ffb3ac] shadow-none"
                            onClick={() => void handleDeleteJob(job)}
                          >
                            {isDeleting ? "Удаляю" : "Удалить"}
                          </Button>
                        </div>
                      </div>
                    );
                  })}
                  {jobs.length > visibleJobs.length && (
                    <button
                      type="button"
                      className="inline-flex min-h-8 items-center rounded-full bg-[#221b17] px-3 text-xs font-medium text-[#d8ccbf]"
                      onClick={() => setShowAllJobs((current) => !current)}
                    >
                      {showAllJobs ? "Показать меньше" : `Еще задачи · ${jobs.length - visibleJobs.length}`}
                    </button>
                  )}
                </div>
              ) : (
                <div className="space-y-2">
                  {batchCreatedCount !== null && (
                    <div className="rounded-[0.9rem] bg-[#1b1a17] px-3 py-2 text-xs text-[#d7cfc4]">
                      Создано задач: {batchCreatedCount}.
                    </div>
                  )}
                  {batchWarnings.length > 0 && (
                    <div className="rounded-[0.9rem] border border-[#5e4527] bg-[#261d15] px-3 py-2 text-xs text-[#e9d1b0]">
                      {batchWarnings[0]?.message}
                      {batchWarnings.length > 1 ? ` · еще ${batchWarnings.length - 1}` : ""}
                    </div>
                  )}
                  {batches.length === 0 && <p className="text-sm text-[#c5b7a8]">Пока пусто. Batch появится после архива.</p>}
                  {visibleBatches.map((batch) => {
                    const isActiveBatch = activeBatch?.batch.batchId === batch.batchId;
                    const finished = batch.summary.finished;
                    return (
                      <div
                        key={batch.batchId}
                        className={[
                          "flex flex-wrap items-start justify-between gap-2.5 rounded-[0.9rem] px-3 py-2",
                          isActiveBatch ? "bg-[#261b16]" : "bg-[#17120f]"
                        ].join(" ")}
                      >
                        <button
                          type="button"
                          className="min-w-0 flex-1 text-left"
                          onClick={() => void handleOpenBatch(batch.batchId)}
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="inline-flex min-h-6 items-center rounded-full border border-transparent bg-[#221a15] px-2 text-[10px] font-semibold text-[#efd7c2]">
                              {finished ? "Готово" : "В работе"}
                            </span>
                            <span className="text-sm font-semibold text-[#fff8f1]">{batch.title}</span>
                          </div>
                          <p className="mt-1 text-xs text-[#cbbfb1]">{batch.archiveName} · задач: {batch.jobCount}</p>
                          <p className="mt-0.5 text-[11px] text-[#aa9a8c]">{formatDate(batch.updatedAt)}</p>
                        </button>
                        <div className="flex shrink-0 items-center gap-2">
                          <Button
                            type="button"
                            variant="outline"
                            className="min-h-7 rounded-full border-0 bg-[#2b221d] px-2.5 text-[11px] font-semibold text-[#fff4ea] shadow-none"
                            onClick={() => void handleOpenBatch(batch.batchId)}
                          >
                            Открыть
                          </Button>
                        </div>
                      </div>
                    );
                  })}
                  {batches.length > visibleBatches.length && (
                    <button
                      type="button"
                      className="inline-flex min-h-8 items-center rounded-full bg-[#221b17] px-3 text-xs font-medium text-[#d8ccbf]"
                      onClick={() => setShowAllBatches((current) => !current)}
                    >
                      {showAllBatches ? "Показать меньше" : `Еще batch · ${batches.length - visibleBatches.length}`}
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>

          <div className="self-start rounded-[1.2rem] bg-[#15110e] p-4 lg:sticky lg:top-4">
            {listTab === "batches" && activeBatch ? (
              <div ref={activeBatchRef} className="space-y-3">
                <div>
                  <p className="text-sm font-semibold text-[#fff8f1]">{activeBatch.batch.title}</p>
                  <p className="mt-1 text-xs text-[#cbbfb1]">{activeBatch.batch.archiveName}</p>
                  <p className="mt-1 text-[11px] uppercase tracking-[0.18em] text-[#aa9a8c]">{formatDate(activeBatch.batch.updatedAt)}</p>
                </div>
                <div className="flex flex-wrap gap-2 text-xs text-[#e7dbce]">
                  {[
                    ["Всего", activeBatchSummary?.totalJobs ?? 0],
                    ["Готово", activeBatchSummary?.completedJobs ?? 0],
                    ["Ошибки", activeBatchSummary?.failedJobs ?? 0],
                    ["В работе", (activeBatchSummary?.runningJobs ?? 0) + (activeBatchSummary?.queuedJobs ?? 0)]
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-full bg-[#1d1713] px-3 py-1">
                      {label}: {value}
                    </div>
                  ))}
                </div>
                {activeBatchProductionExportUrl && (
                  <div className="flex flex-wrap gap-2">
                    <a href={activeBatchProductionExportUrl} className={downloadLinkClass()} download>
                      ZIP итог
                    </a>
                    {activeBatchReviewExportUrl && (
                      <a href={activeBatchReviewExportUrl} className={downloadLinkClass(true)} download>
                        ZIP review
                      </a>
                    )}
                  </div>
                )}
                {!activeBatchSummary?.finished && (
                  <p className="text-xs text-[#c5b7a8]">Batch еще в работе. Экспорт появится после завершения.</p>
                )}
              </div>
            ) : activeJob ? (
              <div ref={activeResultRef} className="space-y-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`inline-flex min-h-6 items-center rounded-full border px-2 text-[10px] font-semibold ${statusClasses[activeJob.status]}`}>
                      {statusLabels[activeJob.status]}
                    </span>
                    <p className="text-sm font-semibold text-[#fff8f1]">{activeJob.title}</p>
                  </div>
                  <p className="mt-1 text-xs text-[#cbbfb1]">{activeJob.input.drawingName}</p>
                  <p className="mt-0.5 text-[11px] text-[#aa9a8c]">
                    {formatConfidence(activeJob.result?.summary.documentConfidence)} · {formatDate(activeJob.updatedAt)}
                  </p>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    disabled={!canOpenPreview || openingPreviewJobId === activeJob.jobId}
                    className="min-h-8 rounded-full border-0 bg-[#2b221d] px-3 text-xs font-semibold text-[#fff4ea] shadow-none"
                    onClick={() => void handleOpenPreview(activeJob.jobId, selectedResultPage?.pageIndex)}
                  >
                    Preview / исправить
                  </Button>
                  {activePrimaryDownloads.map(([label, url]) => (
                    <a key={label} href={resolveAssetUrl(url)} className={downloadLinkClass()} download>
                      {label}
                    </a>
                  ))}
                </div>

                {activeFailureMessage && (
                  <div className="rounded-full bg-[#2b1917] px-3 py-1 text-xs text-[#f5b6b0]">
                    {activeFailureMessage}
                  </div>
                )}

                {activeSummary && (
                  <div className="flex flex-wrap gap-2 text-xs text-[#e7dbce]">
                    {[
                      ["Найдено", activeSummary.foundCount],
                      ["Не найдены", activeSummary.missingCount],
                      ["Неуверенно", activeSummary.uncertainCount],
                      ["Удержано", activeSummary.heldBackCount]
                    ].map(([label, value]) => (
                      <div key={label} className="rounded-full bg-[#1d1713] px-3 py-1">
                        {label}: {value}
                      </div>
                    ))}
                  </div>
                )}

                {hasReviewNotice && (
                  <div className="rounded-full bg-[#1d1611] px-3 py-1 text-xs text-[#f0d9bb]">
                    Есть спорные места — проверь вручную.
                  </div>
                )}

                {activeSecondaryDownloads.length > 0 && (
                  <div className="space-y-2">
                    <button
                      type="button"
                      className="text-[11px] uppercase tracking-[0.18em] text-[#b09f90]"
                      onClick={() => setShowSecondaryDownloads((current) => !current)}
                    >
                      {showSecondaryDownloads ? "Скрыть файлы" : `Еще файлы · ${activeSecondaryDownloads.length}`}
                    </button>
                    {showSecondaryDownloads && (
                      <div className="flex flex-wrap gap-2">
                        {activeSecondaryDownloads.map(([label, url]) => (
                          <a key={label} href={resolveAssetUrl(url)} className={downloadLinkClass(true)} download>
                            {label}
                          </a>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <p className="text-sm text-[#c5b7a8]">Выбери задачу слева, чтобы увидеть итог.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
