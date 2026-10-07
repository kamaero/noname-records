/**
 * Запуск платного прогона с проверкой месячного лимита.
 *
 * Сервер отвечает 409 `over_limit` с цифрами, если смета не помещается в остаток месяца.
 * Администратор может запустить «сверх лимита» — тем же запросом с `?override_limit=1`
 * (сервер пишет это в журнал). Остальным сервер откажет 403 — страница скажет, к кому идти.
 */
import { ApiError, apiPostJson } from "../api/client";

function rub(value: unknown): string {
  return `${Math.round(Number(value) || 0).toLocaleString("ru-RU")} ₽`;
}

export function overLimitText(payload: Record<string, unknown>): string {
  return `Прогон ≈ ${rub(payload.estimate_rub)}, до месячного лимита осталось ${rub(payload.left_rub)} (лимит ${rub(payload.limit_rub)}).`;
}

/** Сервер запустил, но предупредил: у модели нет цены, траты не войдут в лимит. Редко и важно. */
function warnIfUnpriced<T>(result: T): T {
  const warning = (result as { spend_warning?: unknown } | null)?.spend_warning;
  if (typeof warning === "string" && warning) window.alert(warning);
  return result;
}

export async function postWithLimit<T>(path: string, body: unknown): Promise<T> {
  try {
    return warnIfUnpriced(await apiPostJson<T>(path, body));
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 409 || error.payload.error !== "over_limit") throw error;
    if (!window.confirm(`${overLimitText(error.payload)}\n\nЗапустить сверх лимита?`)) throw error;
    const sep = path.includes("?") ? "&" : "?";
    try {
      return warnIfUnpriced(await apiPostJson<T>(`${path}${sep}override_limit=1`, body));
    } catch (second) {
      if (second instanceof ApiError && second.status === 403) {
        throw new ApiError("Сверх лимита запускает только администратор студии.", { status: 403, payload: second.payload });
      }
      throw second;
    }
  }
}
