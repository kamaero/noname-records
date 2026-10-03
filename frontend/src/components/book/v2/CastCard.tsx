import { useMemo } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPostJson, describeApiError } from "../../../api/client";
import { Button, LinkButton } from "../../../ui";
import { useToast } from "../../ToastProvider";
import { invalidateReader, toBudgetRow, useBookCast } from "../../../v2/editorApi";
import type { BudgetCharacterRow, CharacterMapResponse, SaveBudgetCharacterPayload, SaveBudgetCharacterResponse } from "../../../types";
import { buildPaletteResolution, castTodo, findPaletteConflicts, normalizeHexColor, summarizeCast, type PaletteDraft } from "../../../viewModels/booksCast";
import { plural } from "../../../viewModels/bookHub";
import { hubKeys } from "./hubApi";

type CastCardProps = {
  bookId: string;
};

/** Same request as `LegendEditor.savePalette` — the helper there is not exported. */
function savePalette(row: BudgetCharacterRow, palette: PaletteDraft) {
  const payload: SaveBudgetCharacterPayload = {
    character_color: normalizeHexColor(palette.background, row.character_color || "#13333B"),
    character_text_color: normalizeHexColor(palette.text, row.character_text_color || "#D7FFE9"),
    character_font_weight: palette.weight,
    character_font_style: palette.style,
  };
  return apiPostJson<SaveBudgetCharacterResponse>(`/api/budget/character/${encodeURIComponent(row.character_id)}`, payload);
}

/**
 * Вход в Каст, а не третья таблица ролей: сам список персонажей теперь
 * читают на `/books/:id/cast`, а тут — только то, что решает, идти ли туда
 * прямо сейчас. `crossing` — из того же ответа `character-map`, что раньше
 * питал отдельный экран карты; здесь он живёт вторым запросом, тем же ключом
 * кэша, что и на странице каста, поэтому переход туда не бьёт по сети дважды.
 */
export function CastCard({ bookId }: CastCardProps) {
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const cast = useBookCast(bookId, true);
  const mapQuery = useQuery({
    queryKey: hubKeys.characterMap(bookId),
    queryFn: () => apiGet<CharacterMapResponse>(`/api/v2/books/${encodeURIComponent(bookId)}/character-map`),
    enabled: Boolean(bookId),
  });
  // Портреты героев — авторские иллюстрации, которые человек привязывает к ролям. Строка
  // появляется только у книги, где картинки загружены, — у прочих ей нечего предложить.
  const illustrations = useQuery({
    queryKey: ["illustrations", bookId, "summary"],
    queryFn: () =>
      apiGet<{ counts: { total: number; bound: number; skipped: number } }>(
        `/api/v2/books/${encodeURIComponent(bookId)}/illustrations?summary=1`,
      ),
    enabled: Boolean(bookId),
  });
  const pictures = illustrations.data?.counts;
  const all = cast.data?.characters ?? [];
  const voiced = useMemo(() => all.map(toBudgetRow).filter((row) => !row.is_narrator), [all]);
  const conflicts = useMemo(() => findPaletteConflicts(voiced), [voiced]);
  // Тот же счёт, что и на самой странице каста (`summarizeCast` там же питает
  // подпись хэдера): рассказчик не роль для назначения актёра, и у свежей
  // книги его `actor_name` пуст всегда — без этого исключения карточка хаба
  // и таблица каста показывали бы разные числа.
  const summary = useMemo(() => summarizeCast(voiced), [voiced]);

  const fix = useMutation({
    mutationFn: async () => {
      const resolution = buildPaletteResolution(voiced, conflicts);
      for (const entry of resolution) await savePalette(entry.row, entry.palette);
      return resolution.length;
    },
    onSuccess: async (changed) => {
      pushToast({
        tone: "success",
        title: "Палитра разведена",
        detail: changed > 0 ? `Обновлено ролей: ${changed}.` : "Подходящих изменений не нашлось: палитра уже разнесена по доступным вариантам.",
      });
      await invalidateReader(queryClient, bookId, ["chapters", "cast"]);
    },
    onError: (error) => pushToast({ tone: "error", title: "Палитра не исправлена", detail: describeApiError(error, "Не удалось развести палитру.") }),
  });

  const total = summary.roles;
  const free = summary.roles - summary.withActor;
  const intersections = mapQuery.data?.intersections ?? [];
  const todo = useMemo(() => castTodo(voiced, intersections, { roles: 5, crossings: 3 }), [voiced, intersections]);
  const castTo = `/books/${encodeURIComponent(bookId)}/cast`;
  // «Ничего не назначено» wins over the neutral "нет конфликтов" filler: when there is
  // truly nothing to do, one friendly line beats three empty-handed ones.
  const hasConflictsLine = Boolean(cast.data) && total > 0;
  // A book with zero voiced roles hasn't had «Персонажи» run yet — that's not
  // "nothing to do", it's "nothing exists yet"; the two read very differently.
  const noRolesYet = Boolean(cast.data) && total === 0;
  const nothingToDo = Boolean(cast.data) && !todo.freeRoles.length && !todo.crossings.length && !conflicts.length;

  return (
    <section className="panel hub-card hub-cast" aria-labelledby="hub-cast-title">
      <header className="hub-card-head">
        <h2 id="hub-cast-title" className="hub-card-title">Каст</h2>
      </header>
      {cast.isError ? (
        <p className="ui-note ui-note--error">Не удалось загрузить каст: {describeApiError(cast.error, "сервер не ответил")}.</p>
      ) : cast.isLoading ? (
        <p className="hub-dim">Считаю состав…</p>
      ) : noRolesYet ? (
        <p className="hub-dim">Ролей пока нет — появятся после шага «Персонажи».</p>
      ) : nothingToDo ? (
        <p className="hub-dim">По касту всё назначено.</p>
      ) : (
        <div className="hub-cast-todo">
          {todo.freeRoles.length ? (
            <div className="hub-cast-section">
              <h3 className="hub-cast-section-title">Роли без актёра</h3>
              <ul className="hub-cast-list">
                {todo.freeRoles.map((role) => (
                  <li key={role.character_id}>
                    <Link to={castTo} className="hub-cast-row">
                      <span className="hub-cast-row-name">{role.name}</span>
                      <span className="hub-dim">{role.lines_count} {plural(role.lines_count, "реплика", "реплики", "реплик")}</span>
                      <span className="ui-chip ui-chip--amber">нет актёра</span>
                    </Link>
                  </li>
                ))}
              </ul>
              {todo.freeMore ? (
                <Link to={castTo} className="hub-cast-more">
                  ещё {todo.freeMore} {plural(todo.freeMore, "роль", "роли", "ролей")} без актёра
                </Link>
              ) : null}
            </div>
          ) : null}
          {todo.crossings.length ? (
            <div className="hub-cast-section">
              <h3 className="hub-cast-section-title">Пересечения</h3>
              <ul className="hub-cast-list">
                {todo.crossings.map((item, index) => (
                  <li key={`${item.actor}-${item.role_a}-${item.role_b}-${index}`} className="hub-cast-crossing">
                    <b>{item.actor}</b>: {item.role_a} ↔ {item.role_b} · гл. {item.chapter}
                  </li>
                ))}
              </ul>
              {todo.crossingsMore ? <Link to={castTo} className="hub-cast-more">ещё {todo.crossingsMore}</Link> : null}
            </div>
          ) : null}
          {hasConflictsLine ? (
            <div className="hub-cast-section hub-cast-conflicts">
              <span className={conflicts.length ? "hub-conflicts is-bad" : "hub-conflicts"}>
                {conflicts.length ? (
                  <>
                    Конфликты палитры: <strong className="num">{conflicts.length}</strong> ·{" "}
                    {conflicts.slice(0, 2).map((c) => `${c.leftName} ↔ ${c.rightName}`).join(" · ")}
                    {conflicts.length > 2 ? ` · ещё ${conflicts.length - 2}` : ""}
                  </>
                ) : (
                  "Конфликтов палитры нет"
                )}
              </span>
              {conflicts.length ? (
                <Button size="sm" variant="secondary" loading={fix.isPending} onClick={() => fix.mutate()}>Исправить</Button>
              ) : null}
            </div>
          ) : null}
        </div>
      )}
      {pictures && pictures.total > 0 ? (
        <div className="hub-cast-section hub-cast-portraits">
          <span>
            Портреты героев: привязано <b className="num">{pictures.bound}</b> из <b className="num">{pictures.total}</b>
            {pictures.total - pictures.bound - pictures.skipped > 0
              ? `, не разобрано ${pictures.total - pictures.bound - pictures.skipped}`
              : ""}
          </span>
          <LinkButton to={`/books/${encodeURIComponent(bookId)}/illustrations`} size="sm" variant="secondary">
            Разметить
          </LinkButton>
        </div>
      ) : null}
      <footer className="hub-card-foot">
        <span className="hub-cast-summary">
          {cast.data ? (
            <>
              <span><b className="num">{total}</b> {plural(total, "роль", "роли", "ролей")}</span>
              <span><b className="num">{free}</b> {plural(free, "свободна", "свободны", "свободны")}</span>
            </>
          ) : null}
        </span>
        <LinkButton to={castTo} size="sm" variant="ghost">Весь каст</LinkButton>
      </footer>
    </section>
  );
}
