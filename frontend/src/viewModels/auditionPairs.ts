import { apiPostJson } from "../api/client";
import type { AuditionLane, AuditionPair, AuditionReason, AuditionScoreResponse } from "../v2/types";

/** Актёр уже утверждён на другую роль этой книги — по умолчанию его пробы скрыты:
    цель студии — многообразие голосов, один актёр — одна роль. Утверждённый на эту же
    роль не «занят»: его проба на свою роль остаётся на виду, иначе лист роли прятал бы
    того, кого на неё выбрали. */
/** Решение владельца 04.10: 7 и выше хотя бы у одного из двоих — сигнал на утверждение;
    ниже 7 у обоих — вежливый отказ (его заводит сервер). */
export const APPROVE_SCORE = 7;

/** Сигнал на утверждение: кто-то из двоих поставил 7 или выше. Решает всё равно человек. */
export function isRecommended(pair: AuditionPair): boolean {
  return Math.max(pair.scores?.author?.score ?? 0, pair.scores?.studio?.score ?? 0) >= APPROVE_SCORE;
}

/** Роль ещё не утверждена: пусто или «Имя?» (предложение). Метка «к утверждению» имеет
    смысл только тут — на занятой роли она звала бы утвердить поверх решения. */
export function roleOpen(pair: AuditionPair): boolean {
  const actor = pair.role_actor.trim();
  return !actor || actor.endsWith("?");
}

export function isBusy(pair: AuditionPair): boolean {
  return pair.busy_on.length > 0 && !pair.approved;
}

function authorScore(pair: AuditionPair): number {
  return pair.scores?.author?.score ?? 0;
}

function studioScore(pair: AuditionPair): number {
  return pair.scores?.studio?.score ?? 0;
}

/** Порядок в листе проб роли: оценка автора, затем студии, затем свежесть.
    Две оценки не сводятся в одно число — автор главный, как в голосах за роль. */
export function sortForRole(pairs: AuditionPair[]): AuditionPair[] {
  return [...pairs].sort(
    (a, b) =>
      authorScore(b) - authorScore(a) ||
      studioScore(b) - studioScore(a) ||
      (b.uploaded_at > a.uploaded_at ? 1 : b.uploaded_at < a.uploaded_at ? -1 : 0),
  );
}

/** Лучшая оценка автора среди проб роли — для таблицы каста. null — автор не оценивал. */
export function bestAuthorScore(pairs: AuditionPair[]): number | null {
  const scores = pairs.map(authorScore).filter((score) => score > 0);
  return scores.length ? Math.max(...scores) : null;
}

/** Ответ окна причины: причина и слова для студии (`ReasonPrompt`). */
export type ScoreAnswer = { reason: AuditionReason | null; comment: string };

/** Оценка, а с ответом окна — и причина, и слова для студии одним запросом: сервер
    пишет владельцу одно письмо на одно сохранение. Комментарий шлётся, только если
    изменился, — иначе каждое нажатие шкалы трогало бы его. */
export function postScore(
  pair: AuditionPair, lane: AuditionLane, score: number | null, answer?: ScoreAnswer | null,
): Promise<AuditionScoreResponse> {
  const reason = answer?.reason;
  const comment = answer && score !== null && answer.comment !== (pair.scores?.[lane]?.comment || "") ? answer.comment : null;
  return apiPostJson<AuditionScoreResponse>("/api/v2/auditions/score", {
    book_id: pair.book_id, role: pair.role, actor_name: pair.actor_name, lane, score,
    ...(reason ? { reason } : {}), ...(comment !== null ? { comment } : {}),
  });
}

/** Решение владельца 11.10: причину спрашивают при любой оценке автора ниже 7 — там же,
    где начинается отказ (с 04.10 до 11.10 — при 5 и ниже). */
export const REASON_MAX_SCORE = APPROVE_SCORE - 1;

export function asksReason(lane: AuditionLane, score: number | null): boolean {
  return lane === "author" && score !== null && score <= REASON_MAX_SCORE;
}

/** Причина относится к герою: общее «Голос» воспринималось как приговор качеству голоса.
    Те же подписи у кнопок бота (`audition_scores.REASON_LABELS`). */
export const REASON_LABEL: Record<AuditionReason, string> = {
  voice: "Тембр не этого героя",
  manner: "Подача не этого героя",
  all: "И тембр, и подача",
};

/** Предел комментария — как на сервере (`COMMENT_MAX_CHARS`). */
export const COMMENT_MAX_CHARS = 1000;

/** Вопрос перед утверждением занятого актёра — исключения редкие, но возможны. */
export function busyConfirmText(pair: AuditionPair): string {
  const roles = pair.busy_on.map((role) => `«${role}»`).join(", ");
  return `${pair.actor_name} уже утверждён(а) на ${roles}. Утвердить и на «${pair.role}»?\n\nМы стремимся к одной роли на актёра.`;
}
