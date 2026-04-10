import type {
  AnnotationSession,
  CreateSessionRequest,
  CreateSessionResponse,
  SessionCommandRequest,
  SessionCommandResponse,
  SessionListResponse,
  UploadDocumentResponse
} from "@blueprint-rec/shared-types";

const API_BASE_URL = (
  process.env.NEXT_PUBLIC_ANNOTATION_API_BASE_URL ||
  process.env.NEXT_PUBLIC_INFERENCE_BASE_URL ||
  "http://127.0.0.1:8000"
).replace(/\/$/, "");

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
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
      ...(init?.headers ?? {})
    }
  });

  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }

  return (await response.json()) as T;
}

async function requestWithoutBody(path: string, init?: RequestInit): Promise<void> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      ...(init?.headers ?? {})
    }
  });

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

export async function listSessions() {
  return request<SessionListResponse>("/api/sessions");
}

export async function createSession(payload: CreateSessionRequest) {
  return request<CreateSessionResponse>("/api/sessions", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function deleteSession(sessionId: string) {
  return requestWithoutBody(`/api/sessions/${sessionId}`, {
    method: "DELETE"
  });
}

export async function getSession(sessionId: string) {
  return request<CreateSessionResponse>(`/api/sessions/${sessionId}`);
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
