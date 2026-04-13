import type {
  AnnotationSession,
  BatchListResponse,
  BatchResponse,
  CreateBatchJobsResponse,
  CreateJobResponse,
  PreviewSessionResponse,
  CreateSessionRequest,
  CreateSessionResponse,
  JobListResponse,
  SessionCommandRequest,
  SessionCommandResponse,
  SessionListResponse,
  UploadDocumentResponse
} from "@blueprint-rec/shared-types";

const configuredApiBaseUrl =
  process.env.NEXT_PUBLIC_ANNOTATION_API_BASE_URL ??
  process.env.NEXT_PUBLIC_INFERENCE_BASE_URL ??
  "";

const API_BASE_URL = configuredApiBaseUrl.trim().replace(/\/$/, "");

function formatNetworkErrorMessage() {
  const target = API_BASE_URL || "same-origin /api";
  return `Не удалось подключиться к сервису разметки (${target}). Проверь, что backend запущен, и попробуй ещё раз.`;
}

async function readErrorMessage(response: Response) {
  const contentType = response.headers.get("content-type") ?? "";

  if (contentType.includes("application/json")) {
    try {
      const payload = (await response.json()) as { detail?: string; message?: string };
      return payload.detail || payload.message || `Request failed with status ${response.status}`;
    } catch {
      return `Request failed with status ${response.status}`;
    }
  }

  const message = await response.text();
  return message || `Request failed with status ${response.status}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData = init?.body instanceof FormData;
  let response: Response;

  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      cache: "no-store",
      headers: {
        ...(isFormData ? {} : { "Content-Type": "application/json" }),
        ...(init?.headers ?? {})
      }
    });
  } catch (error) {
    if (error instanceof Error) {
      throw new Error(formatNetworkErrorMessage());
    }
    throw error;
  }

  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }

  return (await response.json()) as T;
}

async function requestWithoutBody(path: string, init?: RequestInit): Promise<void> {
  let response: Response;

  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      cache: "no-store",
      headers: {
        ...(init?.headers ?? {})
      }
    });
  } catch (error) {
    if (error instanceof Error) {
      throw new Error(formatNetworkErrorMessage());
    }
    throw error;
  }

  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
}

export function resolveAssetUrl(path: string) {
  if (!path) {
    return "";
  }

  if (/^https?:\/\//.test(path)) {
    return path;
  }

  return `${API_BASE_URL}${path}`;
}

export function resolveBatchExportUrl(batchId: string, mode: "production" | "review") {
  return `${API_BASE_URL}/api/batches/${encodeURIComponent(batchId)}/export?mode=${encodeURIComponent(mode)}`;
}

export async function listSessions() {
  return request<SessionListResponse>("/api/sessions");
}

export async function listJobs() {
  return request<JobListResponse>("/api/jobs");
}

export async function listBatches() {
  return request<BatchListResponse>("/api/batches");
}

export async function getBatch(batchId: string) {
  return request<BatchResponse>(`/api/batches/${batchId}`);
}

export async function createSession(payload: CreateSessionRequest) {
  return request<CreateSessionResponse>("/api/sessions", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function createJob(payload: { title?: string; drawing: File; labels?: File | null }) {
  const formData = new FormData();
  formData.append("drawing", payload.drawing);
  if (payload.title?.trim()) {
    formData.append("title", payload.title.trim());
  }
  if (payload.labels) {
    formData.append("labels", payload.labels);
  }
  return request<CreateJobResponse>("/api/jobs", {
    method: "POST",
    body: formData
  });
}

export async function createBatchJobs(payload: { archive: File; titlePrefix?: string }) {
  const formData = new FormData();
  formData.append("archive", payload.archive);
  if (payload.titlePrefix?.trim()) {
    formData.append("title_prefix", payload.titlePrefix.trim());
  }
  return request<CreateBatchJobsResponse>("/api/jobs/batch", {
    method: "POST",
    body: formData
  });
}

export async function deleteSession(sessionId: string) {
  return requestWithoutBody(`/api/sessions/${sessionId}`, {
    method: "DELETE"
  });
}

export async function deleteJob(jobId: string) {
  return requestWithoutBody(`/api/jobs/${jobId}`, {
    method: "DELETE"
  });
}

export async function getSession(sessionId: string) {
  return request<CreateSessionResponse>(`/api/sessions/${sessionId}`);
}

export async function getJob(jobId: string) {
  return request<CreateJobResponse>(`/api/jobs/${jobId}`);
}

export async function createJobPreviewSession(jobId: string, pageIndex?: number) {
  const query = pageIndex != null ? `?page_index=${encodeURIComponent(String(pageIndex))}` : "";
  return request<PreviewSessionResponse>(`/api/jobs/${jobId}/preview-session${query}`, {
    method: "POST"
  });
}

export async function uploadDocument(sessionId: string, file: File) {
  const formData = new FormData();
  formData.append("file", file);
  return request<UploadDocumentResponse>(`/api/sessions/${sessionId}/document`, {
    method: "POST",
    body: formData
  });
}

export async function applySessionCommand(sessionId: string, payload: SessionCommandRequest) {
  return request<SessionCommandResponse>(`/api/sessions/${sessionId}/commands`, {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function detectSessionCandidates(sessionId: string) {
  return request<CreateSessionResponse>(`/api/sessions/${sessionId}/detect-candidates`, {
    method: "POST"
  });
}

export async function autoAnnotateSession(sessionId: string) {
  return request<CreateSessionResponse>(`/api/sessions/${sessionId}/auto-annotate`, {
    method: "POST"
  });
}

export async function rejectSessionCandidate(sessionId: string, candidateId: string) {
  return request<CreateSessionResponse>(`/api/sessions/${sessionId}/candidates/${candidateId}/reject`, {
    method: "POST"
  });
}

export async function refreshSession(sessionId: string): Promise<AnnotationSession> {
  const response = await getSession(sessionId);
  return response.session;
}

export async function downloadSessionExport(sessionId: string, fallbackTitle?: string) {
  const fileName =
    (`${(fallbackTitle || "session").trim() || "session"}-export.zip`)
      .replace(/[<>:"/\\|?*\x00-\x1F]+/g, "-")
      .trim() || "session-export.zip";
  const response = await fetch(`${API_BASE_URL}/api/sessions/${sessionId}/export`, {
    method: "GET",
    cache: "no-store"
  });

  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }

  const archiveBlob = await response.blob();
  const objectUrl = window.URL.createObjectURL(archiveBlob);
  const anchor = window.document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = fileName;
  anchor.rel = "noopener";
  window.document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(objectUrl);
}
