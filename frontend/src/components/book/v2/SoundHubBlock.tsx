/**
 * «Звуковая разметка» в хабе книги, рядом с консилиумом: смета перед запуском, ход
 * прогона, итог со ссылкой в Читалку. Только для того, кто правит разметку — хаб не
 * рисует блок остальным. Прогон стоит денег, поэтому запуск — всегда через окно со сметой.
 */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, LinkButton } from "../../../ui";
import { useToast } from "../../ToastProvider";
import { describeSoundError, isSoundRunActive, soundKeys, startSound, stopSound, useSoundBook, useSoundEstimate } from "../../../v2/soundApi";
import type { SoundEstimate, SoundMode, SoundRun } from "../../../v2/types";

type SoundBlock = SoundEstimate["blocked"][SoundMode];

const BLOCK_TEXT: Record<Exclude<SoundBlock, "">, string> = {
  no_credits: "не хватает денег на балансе RouterAI",
  book_busy: "книга сейчас размечается",
  already_running: "прогон уже идёт",
};

const MODE_LABEL: Record<SoundMode, string> = { rest: "Доразметить", all: "Разметить заново" };

const rub = (value: number) => Math.round(value).toLocaleString("ru-RU");

function progressLine(run: SoundRun): string {
  if (run.status === "queued" || run.phase === "queued") return "Звуковая разметка: в очереди…";
  const money = `потрачено ${rub(run.spent_rub)} ₽ из ≈ ${rub(run.estimate_rub)}`;
  if (run.phase === "places") return `Звуковая разметка: сверяю места книги · ${money}`;
  const current = Math.min(run.chapters_done + 1, run.chapters_total);
  return `Звуковая разметка: глава ${current} из ${run.chapters_total} · ${money}`;
}

function outcomeLine(run: SoundRun, places: number, pairs: number): string {
  if (run.status === "done") {
    const day = run.finished_at ? ` ${run.finished_at.slice(8, 10)}.${run.finished_at.slice(5, 7)}` : "";
    const markers = Number(run.result?.markers ?? 0);
    const incomplete = run.chapters_incomplete > 0 ? ` · неполных глав ${run.chapters_incomplete}` : "";
    return `Звуковая разметка${day}: новых маркеров ${markers}, мест ${places}, пар на решение ${pairs}${incomplete}`;
  }
  if (run.status === "stopped") return `Звуковая разметка остановлена: ${run.error || "без причины"}`;
  if (run.status === "failed") return `Звуковая разметка упала: ${run.error || "без подробностей"}`;
  return "";
}

export function SoundHubBlock({ bookId, soundTo }: { bookId: string; soundTo: string }) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const [confirming, setConfirming] = useState(false);
  const book = useSoundBook(bookId, true);
  const estimate = useSoundEstimate(bookId, confirming);
  const run = book.data?.run ?? null;
  const active = isSoundRunActive(run);

  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: soundKeys.book(bookId) }),
      queryClient.invalidateQueries({ queryKey: soundKeys.estimate(bookId) }),
    ]);

  const start = useMutation({
    mutationFn: (mode: SoundMode) => startSound(bookId, mode),
    onSuccess: async () => {
      setConfirming(false);
      pushToast({ tone: "success", title: "Звуковая разметка запущена", detail: "Ход виден здесь; по завершении придёт сообщение в Telegram." });
      await refresh();
    },
    onError: async (error) => {
      pushToast({ tone: "error", title: "Звуковая разметка не запущена", detail: describeSoundError(error) });
      await refresh();
    },
  });

  const stop = useMutation({
    mutationFn: () => stopSound(bookId),
    onSuccess: async () => {
      pushToast({ tone: "success", title: "Остановка запрошена", detail: "Текущая глава доделается и сохранится." });
      await refresh();
    },
    onError: (error) => pushToast({ tone: "error", title: "Не остановилась", detail: describeSoundError(error) }),
  });

  if (active && run) {
    const fraction = run.chapters_total ? run.chapters_done / run.chapters_total : 0;
    return (
      <div className="hub-consilium" aria-live="polite">
        <div className="hub-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(fraction * 100)}>
          <span style={{ width: `${Math.round(fraction * 100)}%` }} />
        </div>
        <span className="hub-consilium-line">{progressLine(run)}</span>
        <Button size="sm" variant="ghost" loading={stop.isPending} disabled={run.stop_requested} onClick={() => stop.mutate()}>
          {run.stop_requested ? "останавливается…" : "остановить"}
        </Button>
      </div>
    );
  }

  const outcome = run ? outcomeLine(run, book.data?.places.length ?? 0, book.data?.pairs.length ?? 0) : "";
  const failed = run && (run.status === "stopped" || run.status === "failed");
  const plan = estimate.data;

  return (
    <div className="hub-consilium">
      {!confirming ? (
        <div className="hub-consilium-row">
          <Button size="sm" variant="secondary" onClick={() => setConfirming(true)}>
            {failed ? "Продолжить звуковую разметку" : "Звуковая разметка"}
          </Button>
          {outcome ? (
            run?.status === "done" ? (
              <LinkButton to={soundTo} size="sm" variant="ghost">{outcome} →</LinkButton>
            ) : (
              <span className="hub-consilium-line hub-consilium-line--warn">{outcome}</span>
            )
          ) : null}
        </div>
      ) : (
        <div className="hub-confirm" role="group" aria-label="Запуск звуковой разметки">
          {estimate.isLoading ? <p>Считаю смету…</p> : null}
          {estimate.isError ? <p>{describeSoundError(estimate.error, "Смета не посчиталась.")}</p> : null}
          {plan ? (
            <>
              <p>
                ИИ читает главы по порядку и отмечает сцены, переходы и значимые звуки; ваши правки прогон не трогает.
                Глав всего {plan.chapters_total}, ещё не размечено: {plan.chapters_to_read.rest}.
                {plan.credits !== null ? ` Баланс RouterAI: ${rub(plan.credits)} ₽.` : " Баланс RouterAI не прочитан."}
              </p>
              <div className="hub-confirm-actions">
                {(["rest", "all"] as SoundMode[]).map((mode) => {
                  const blocked = plan.blocked[mode];
                  const nothing = plan.chapters_to_read[mode] === 0;
                  // неизвестный код блокировки — тоже блокировка, а не повод разрешить запуск
                  const why = blocked ? BLOCK_TEXT[blocked] ?? "запуск сейчас недоступен" : nothing ? "все главы уже размечены" : "";
                  return (
                    <Button
                      key={mode}
                      size="sm"
                      variant={mode === "rest" ? "primary" : "secondary"}
                      disabled={Boolean(why) || start.isPending}
                      loading={start.isPending && start.variables === mode}
                      title={why || undefined}
                      onClick={() => start.mutate(mode)}
                    >
                      {/* неразрывные пробелы: «≈ 58 ₽» не рвётся, «₽» не уезжает на свою строку */}
                      {MODE_LABEL[mode]} —{"\u00a0"}≈{"\u00a0"}{rub(plan.estimate_rub[mode])}{"\u00a0"}₽{why ? ` (${why})` : ""}
                    </Button>
                  );
                })}
                <Button size="sm" variant="ghost" disabled={start.isPending} onClick={() => setConfirming(false)}>
                  Отмена
                </Button>
              </div>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}
