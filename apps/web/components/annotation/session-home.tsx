"use client";

import Link from "next/link";
import { ChangeEvent, useEffect, useState } from "react";
import { createSession, deleteSession, listSessions, uploadDocument } from "@/lib/api";
import type { SessionListItem } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="h-4 w-4">
      <path
        d="M4 7h16m-11 0V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7m-8 0 1 11a1.5 1.5 0 0 0 1.5 1.36h5A1.5 1.5 0 0 0 16 18l1-11m-6 3.5v5m3-5v5"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.85"
      />
    </svg>
  );
}

export function SessionHome() {
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [showSessions, setShowSessions] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const response = await listSessions();
        if (!cancelled) {
          setSessions(response.sessions);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Failed to load sessions.");
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleCreateSession(nextFile: File) {
    setIsBusy(true);
    setError(null);

    try {
      const suggestedTitle = title.trim() || nextFile.name.replace(/\.[^.]+$/, "") || "New annotation session";
      const created = await createSession({ title: suggestedTitle });
      await uploadDocument(created.session.sessionId, nextFile);

      window.location.assign(`/sessions/${created.session.sessionId}`);
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : "Failed to create session.");
      setIsBusy(false);
    }
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const nextFile = event.target.files?.[0] ?? null;
    setFile(nextFile);
    if (!nextFile) {
      return;
    }

    if (!title.trim()) {
      setTitle(nextFile.name.replace(/\.[^.]+$/, ""));
    }

    void handleCreateSession(nextFile);
  }

  async function handleDeleteSession(session: SessionListItem) {
    const confirmed = window.confirm(`Удалить сессию "${session.title}"?`);
    if (!confirmed) {
      return;
    }

    setDeletingSessionId(session.sessionId);
    setError(null);

    try {
      await deleteSession(session.sessionId);
      setSessions((current) => current.filter((item) => item.sessionId !== session.sessionId));
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Не удалось удалить сессию.");
    } finally {
      setDeletingSessionId(null);
    }
  }

  return (
    <div className="h-full overflow-auto pr-1 text-white">
      <div className="mx-auto flex min-h-full w-full max-w-[1120px] flex-col gap-5 py-4">
        <section className="space-y-5 rounded-[1.8rem] border border-[#2b2e35] bg-[#17191f] p-6 shadow-[0_30px_80px_rgba(8,10,14,0.34)]">
          <div className="space-y-2">
            <h1 className="text-3xl font-semibold tracking-tight text-white">Новая сессия</h1>
            <p className="max-w-[42rem] text-sm leading-6 text-[#9ca2ac]">
              Выбери чертёж, и рабочее поле откроется сразу. Без лишних кнопок и промежуточных экранов.
            </p>
          </div>

          <Card className="!border-[#2f333b] !bg-[#1d2027]">
            <div className="space-y-4">
              <div className="space-y-2">
                <label className="text-xs font-semibold uppercase tracking-[0.2em] text-[#8f949d]">Название</label>
                <input
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="Например: лист 4"
                  disabled={isBusy}
                  className="w-full rounded-2xl border border-[#30343c] bg-[#111317] px-4 py-3 text-sm text-white outline-none transition placeholder:text-[#6f7580] focus:border-[#656b74] focus:ring-2 focus:ring-[#656b74]/25 disabled:cursor-not-allowed disabled:opacity-60"
                />
              </div>
              <div className="space-y-2">
                <label className="text-xs font-semibold uppercase tracking-[0.2em] text-[#8f949d]">Файл чертежа</label>
                <label className="flex min-h-[4.5rem] cursor-pointer items-center justify-between rounded-[1.3rem] border border-dashed border-[#353941] bg-[#111317] px-5 py-4 text-[15px] text-[#d3d7de] shadow-[0_12px_28px_rgba(8,10,14,0.18)] transition">
                  <span>{isBusy ? "Открываю рабочее поле..." : file ? file.name : "PNG, JPG или WEBP"}</span>
                  <span className="inline-flex min-h-11 items-center rounded-[1rem] border border-[#3a3e47] bg-[#1f232b] px-4 text-[13px] font-semibold uppercase tracking-[0.18em] text-white shadow-[0_10px_24px_rgba(8,10,14,0.2)]">
                    {isBusy ? "Жди" : "Выбрать"}
                  </span>
                  <input
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    className="hidden"
                    disabled={isBusy}
                    onChange={handleFileChange}
                  />
                </label>
              </div>
              {error && <p className="text-sm text-[#f18a81]">{error}</p>}
            </div>
          </Card>
        </section>

        <Card className="!border-[#2b2e35] !bg-[#17191f] shadow-[0_24px_70px_rgba(8,10,14,0.28)]">
          <button
            type="button"
            className="flex min-h-12 w-full items-center justify-between gap-4 rounded-[1rem] text-left transition"
            onClick={() => setShowSessions((current) => !current)}
          >
            <div>
              <p className="text-lg font-medium text-white">Последние сессии</p>
              <p className="mt-1 text-sm text-[#949aa4]">{sessions.length === 0 ? "Пока пусто" : `${sessions.length} в списке`}</p>
            </div>
            <span className="inline-flex min-h-11 items-center rounded-[1rem] border border-[#353941] bg-[#1f232b] px-4 text-[13px] font-semibold uppercase tracking-[0.18em] text-white shadow-[0_10px_24px_rgba(8,10,14,0.18)]">
              {showSessions ? "Скрыть" : "Показать"}
            </span>
          </button>

          {showSessions && (
            <div className="mt-5 space-y-3 border-t border-[#2b2e35] pt-5">
              {sessions.length === 0 && <p className="text-sm leading-6 text-[#949aa4]">Пока пусто. Создай первую сессию выше.</p>}
              {sessions.map((session) => {
                const isDeleting = deletingSessionId === session.sessionId;

                return (
                  <div
                    key={session.sessionId}
                    className="rounded-2xl border border-[#2f333b] bg-[#1d2027] px-4 py-4 transition"
                  >
                    <div className="flex items-start justify-between gap-4">
                      <Link
                        href={`/sessions/${session.sessionId}`}
                        className="min-w-0 flex-1 rounded-[1rem] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#616872] focus-visible:ring-offset-2 focus-visible:ring-offset-[#17191f]"
                      >
                        <div className="min-w-0">
                          <p className="text-sm font-semibold text-white">{session.title}</p>
                          <p className="mt-1 text-xs uppercase tracking-[0.18em] text-[#7f8590]">{session.state}</p>
                          <p className="mt-3 truncate text-sm text-[#adb2bc]">{session.documentName ?? "Файл ещё не загружен"}</p>
                        </div>
                      </Link>

                      <div className="flex shrink-0 items-center gap-2">
                        <span className="inline-flex min-h-9 items-center rounded-full border border-[#363a42] bg-[#232830] px-3 text-sm font-semibold text-[#d3d7de]">
                          {session.markerCount} точек
                        </span>
                        <Button
                          type="button"
                          variant="outline"
                          disabled={isDeleting}
                          className="min-h-9 rounded-full !border-[#5c3534] !bg-[#211718] px-3.5 py-2 text-sm font-semibold !text-[#ffb3ac] shadow-none"
                          onClick={() => void handleDeleteSession(session)}
                        >
                          <span className="mr-1.5 inline-flex items-center justify-center">
                            <TrashIcon />
                          </span>
                          {isDeleting ? "Удаляю" : "Удалить"}
                        </Button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
