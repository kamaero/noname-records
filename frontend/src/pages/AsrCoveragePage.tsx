/**
 * ASR `/asr` — покрытие книги по главам.
 *
 * Таблица устроена как каст, только в строках главы: сколько ролей записано из
 * скольких, и можно ли забирать главу в сведение. Пока считается по файлам — у роли
 * есть дубль или нет; когда заработает распознавание, в ту же строку встанет второй,
 * точный счёт: сколько реплик сценария и вправду произнесено.
 *
 * Разница между этими двумя числами и есть то, ради чего экран существует: «роль
 * записана» и «роль записана целиком» — не одно и то же, и сегодня мы знаем только
 * первое.
 */
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiGet, apiPostJson, describeApiError } from "../api/client";
import { useToast } from "../components/ToastProvider";
import { Button, DataTable, EmptyState, LinkButton, PageHeader, type Column } from "../ui";
import { Icon } from "../components/Icon";
import { ChapterFilesRow } from "../components/asr/ChapterFilesRow";
import type { BookRecordingChapter, BookRecordingResponse, BooksResponse, MeResponse } from "../types";
import { plural } from "../viewModels/bookHub";
import { ambientPlan, describeAmbientError, startChapterAmbient, stopChapterAmbient } from "../v2/soundApi";
import "./AsrCoveragePage.css";

/** книги, у которых есть что записывать */
const PUBLISHED_BOOK = new Set(["published", "published_to_dictor", "ready_for_mix"]);

/** ISO → «дд.мм»; пустое или кривое значение не должно ломать строку. */
function shortDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}`;
}

/** Нажатие на кнопку внутри строки — только её собственное дело.
 *
 * Вся строка раскрывает опись главы, и без этого «сверить» запускало бы сверку и
 * заодно разворачивало список файлов, а «архив» — начинал скачивание и разворачивал.
 * Всплытие гасится у каждого действия в строке, включая ссылки: у них своё поведение,
 * и складывать его с чужим нельзя.
 */
function stopRow<T extends { stopPropagation: () => void }>(run?: () => void) {
  return (event: T) => {
    event.stopPropagation();
    run?.();
  };
}

export function AsrCoveragePage() {
  const [params, setParams] = useSearchParams();
  const [onlyStarted, setOnlyStarted] = useState(false);
  /* Раскрытых глав может быть несколько разом: сравнить, что записано в двух соседних,
     — обычное дело, и закрывать предыдущую за человека было бы самоуправством. */
  const [openChapters, setOpenChapters] = useState<Set<string>>(() => new Set());
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { pushToast } = useToast();

  const meQuery = useQuery({ queryKey: ["me"], queryFn: () => apiGet<MeResponse>("/api/me") });
  /* Тот же признак, каким право на удаление решает сервер (`delete_audio_file`): здесь
     только предсказание для интерфейса. Суточное право диктора на свой дубль живёт на
     экране записи — в описи главы стирает тот, кто отвечает за книгу целиком. */
  const canDeleteTakes = (meQuery.data?.roles ?? []).some((role) => role === "admin" || role === "author");

  // Отметку «Глава сдана» ставит только администратор (сервер отвечает 403 остальным).
  const isAdmin = (meQuery.data?.roles ?? []).includes("admin");

  const booksQuery = useQuery({ queryKey: ["books"], queryFn: () => apiGet<BooksResponse>("/api/books") });
  const books = useMemo(
    () => (booksQuery.data?.items ?? []).filter((book) => PUBLISHED_BOOK.has(book.status)),
    [booksQuery.data],
  );
  const bookId = params.get("book_id") || books[0]?.id || "";
  const book = books.find((item) => item.id === bookId);

  const coverage = useQuery({
    queryKey: ["v2", "book-recording", bookId],
    queryFn: () => apiGet<BookRecordingResponse>(`/api/v2/books/${encodeURIComponent(bookId)}/recording`),
    enabled: Boolean(bookId),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    // Эмбиент сочиняется минутами на трек: пока идёт хоть одна глава, ход обновляется сам.
    refetchInterval: (query) =>
      (query.state.data?.chapters ?? []).some((chapter) => chapter.ambient_state === "running") ? 5_000 : false,
  });

  const recognise = useMutation({
    mutationFn: (chapter: BookRecordingChapter) =>
      apiPostJson<{ ok: boolean; job_id?: string }>(`/api/v2/chapters/${encodeURIComponent(chapter.chapter_id)}/asr`, {}),
    onSuccess: async (_result, chapter) => {
      pushToast({
        tone: "success",
        title: "Распознавание запущено",
        detail: `Глава ${chapter.chapter_index}. Минута машинного времени на файл — результат появится здесь сам.`,
      });
      await queryClient.invalidateQueries({ queryKey: ["v2", "book-recording", bookId] });
    },
    onError: (error) =>
      pushToast({ tone: "error", title: "Не запустилось", detail: describeApiError(error, "сервер не ответил") }),
  });

  /* Строгая проверка рассказчика — платный прогон. Сама ставится,
     когда глава собирается; кнопка — для повтора по решению владельца. */
  const auditChapter = useMutation({
    mutationFn: (chapter: BookRecordingChapter) =>
      apiPostJson<{ ok: boolean; job_id?: string }>(`/api/v2/chapters/${encodeURIComponent(chapter.chapter_id)}/audit`, {}),
    onSuccess: async (_result, chapter) => {
      pushToast({
        tone: "success",
        title: "Строгая проверка запущена",
        detail: `Глава ${chapter.chapter_index}. Платный прогон запущен; результат появится здесь.`,
      });
      await queryClient.invalidateQueries({ queryKey: ["v2", "book-recording", bookId] });
    },
    onError: async (error) => {
      const detail = error instanceof ApiError && error.errorCode === "chapter_not_assembled"
        ? "Глава изменилась — соберите её заново"
        : error instanceof ApiError && error.errorCode === "already_queued"
          ? "Проверка уже ожидает или выполняется; повтор не запущен"
          : error instanceof ApiError && error.errorCode === "audit_disabled"
            ? "Строгая проверка отключена"
            : error instanceof ApiError && error.errorCode === "audit_missing_key"
              ? "Добавьте ключ RouterAI в настройках нейросетей"
              : error instanceof ApiError && error.errorCode === "audit_over_limit"
                ? "Месячный лимит исчерпан; проверьте настройки трат"
                : error instanceof ApiError && error.errorCode === "audit_budget_unavailable"
                  ? "Не удалось проверить месячный лимит"
            : describeApiError(error, "сервер не ответил");
      pushToast({ tone: "error", title: "Не запустилось", detail });
      await queryClient.invalidateQueries({ queryKey: ["v2", "book-recording", bookId] });
    },
  });

  /* «Сдана» = сведена, прослушана и выложена; её ставит человек, а не скачивание архива.
     Когда сдана последняя опубликованная глава, спрашиваем про «Озвучена»: сервер сам
     книгу не закрывает — это решение владельца, с письмами и закрытием сроков. */
  const release = useMutation({
    mutationFn: (chapter: BookRecordingChapter) =>
      apiPostJson<{ ok: boolean; all_released: boolean; voiced: boolean }>(
        `/api/v2/chapters/${encodeURIComponent(chapter.chapter_id)}/released`,
        { released: !chapter.released_at },
      ),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["v2", "book-recording", bookId] });
      if (result.all_released && !result.voiced && window.confirm("Все главы сданы. Перевести книгу в «Озвучена»?")) {
        try {
          await apiPostJson(`/api/v2/books/${encodeURIComponent(bookId)}/voiced`, { voiced: true });
          await queryClient.invalidateQueries({ queryKey: ["books"] });
          navigate(`/books/${encodeURIComponent(bookId)}?thanks=1`);
        } catch (error) {
          pushToast({ tone: "error", title: "Книга не переведена", detail: describeApiError(error, "сервер не ответил") });
        }
      }
    },
    onError: (error) =>
      pushToast({ tone: "error", title: "Отметка не сохранилась", detail: describeApiError(error, "сервер не ответил") }),
  });

  const verify = useMutation({
    mutationFn: (chapter: BookRecordingChapter) =>
      apiPostJson<{ ok: boolean; job_id?: string }>(`/api/v2/chapters/${encodeURIComponent(chapter.chapter_id)}/verify`, {}),
    onSuccess: async (_result, chapter) => {
      pushToast({
        tone: "success",
        title: "Сверка запущена",
        detail: `Глава ${chapter.chapter_index}. Файлы перечитываются с диска сервера — на большую главу это несколько минут.`,
      });
      await queryClient.invalidateQueries({ queryKey: ["v2", "book-recording", bookId] });
    },
    onError: (error) => {
      // Дедупликация на сервере отвечает 409 already_queued: сверка уже идёт, а не
      // «не вышло» — кнопка не должна врать про провал там, где просто нельзя запускать дважды.
      if (error instanceof ApiError && error.errorCode === "already_queued") {
        pushToast({ tone: "error", title: "Не вышло", detail: "Сверка уже идёт." });
        return;
      }
      pushToast({ tone: "error", title: "Не вышло", detail: describeApiError(error, "сервер не ответил") });
    },
  });

  // Кнопки «проект» (положить .sesx на NAS раньше круга) и ссылки «сессия» нет с 24.10:
  // скачивание главы — одна ссылка, архив с сессией внутри. На NAS проект фон кладёт сам.

  // Сначала смета (сколько треков и минут — её считает раскладка по таймлайну, в строке
  // её нет), потом подтверждение: каждый трек списывается из квоты ElevenLabs.
  const ambient = useMutation({
    mutationFn: async (chapter: BookRecordingChapter) => {
      const plan = await ambientPlan(chapter.chapter_id);
      if (!plan.tracks) return { started: false, nothing: true, skipped: plan.skipped };
      const skipped = plan.skipped
        ? `, ${plan.skipped} ${plural(plan.skipped, "сцена", "сцены", "сцен")} без места на таймлайне ${plural(plan.skipped, "пропускается", "пропускаются", "пропускаются")}`
        : "";
      const tracks = `${plan.tracks} ${plural(plan.tracks, "трек", "трека", "треков")}`;
      if (!window.confirm(`Сгенерировать ${tracks} · ${plan.minutes} мин эмбиента?\n\nСпишется ${tracks} из квоты ElevenLabs${skipped}.`)) {
        return { started: false, nothing: false, skipped: 0 };
      }
      await startChapterAmbient(chapter.chapter_id);
      return { started: true, nothing: false, skipped: 0 };
    },
    onSuccess: async (result, chapter) => {
      if (result.nothing) {
        pushToast({
          tone: "error",
          title: "Нечего генерировать",
          detail: result.skipped
            ? `Глава ${chapter.chapter_index}: у сцен без трека нет места на таймлайне.`
            : `Глава ${chapter.chapter_index}: у всех сцен уже есть трек.`,
        });
        return;
      }
      if (!result.started) return;
      pushToast({
        tone: "success",
        title: "Эмбиент запущен",
        detail: `Глава ${chapter.chapter_index}. Трек сочиняется минутами — ход виден здесь, треки лягут в проект главы.`,
      });
      await queryClient.invalidateQueries({ queryKey: ["v2", "book-recording", bookId] });
    },
    onError: (error) => pushToast({ tone: "error", title: "Не запустилось", detail: describeAmbientError(error) }),
  });

  // Без подтверждения: остановка ничего не тратит, а текущий трек (уже оплачиваемый)
  // всё равно доделается и ляжет в проект.
  const stopAmbient = useMutation({
    mutationFn: (chapter: BookRecordingChapter) => stopChapterAmbient(chapter.chapter_id),
    onSuccess: async (result, chapter) => {
      pushToast(
        result.stopped
          ? { tone: "success", title: "Остановка запрошена — текущий трек доделается", detail: `Глава ${chapter.chapter_index}.` }
          : { tone: "error", title: "Нечего останавливать", detail: `Глава ${chapter.chapter_index}: генерация уже закончилась.` },
      );
      await queryClient.invalidateQueries({ queryKey: ["v2", "book-recording", bookId] });
    },
    onError: (error) => pushToast({ tone: "error", title: "Не остановилось", detail: describeAmbientError(error) }),
  });

  const chapters = coverage.data?.chapters ?? [];
  const rows = onlyStarted ? chapters.filter((chapter) => chapter.recorded_roles > 0) : chapters;
  const canDownload = coverage.data?.can_download ?? false;

  /** Эмбиент в ряду действий главы: кнопка, ход или «✓». Поля считаются только
   * редактору — у диктора все нули, и ничего не рисуется. */
  function ambientControl(row: BookRecordingChapter): ReactNode {
    if (!canDownload || !row.ready) return null;
    if (row.ambient_state === "running") {
      return (
        <>
          <span className="asr-ambient num" title="Треки сочиняются по одному, каждый ложится в проект главы сразу">
            эмбиент {row.ambient_done}/{row.ambient_done + row.ambient_todo}
          </span>
          <button
            type="button"
            className="asr-run"
            disabled={stopAmbient.isPending}
            onClick={stopRow(() => stopAmbient.mutate(row))}
            title="Остановить генерацию: текущий трек доделается, следующие не начнутся"
          >
            стоп
          </button>
        </>
      );
    }
    if (row.ambient_todo > row.ambient_skipped) {
      return (
        <button
          type="button"
          className="asr-run"
          disabled={ambient.isPending}
          onClick={stopRow(() => ambient.mutate(row))}
          title={`Сгенерировать в ElevenLabs по уникальному треку сценам без готового: ${row.ambient_todo - row.ambient_skipped}`}
        >
          эмбиент
        </button>
      );
    }
    if (row.ambient_state === "done") {
      return (
        <span className="asr-ambient" title={`У всех сцен главы, что стоят на таймлайне, есть трек: ${row.ambient_done}`}>
          эмбиент ✓
        </span>
      );
    }
    return null;
  }

  /** Беда последнего прогона эмбиента — строкой под действиями: в строку она не влезает,
   * а раздвигать колонку ради редкого случая — отнимать ширину у названий всех глав. */
  function ambientNote(row: BookRecordingChapter): ReactNode {
    if (!canDownload || !row.ready) return null;
    if (row.ambient_broken) {
      // Битый трек держит архив главы, но это не беда диктора: его чинит новая генерация.
      return (
        <span
          className="asr-ambient-note asr-broken"
          title="Файл трека эмбиента не прошёл сверку — архив главы не соберётся. Перегенерируйте трек в карточке сцены (Читалка → Звук)"
        >
          · битый эмбиент: {row.ambient_broken}
        </span>
      );
    }
    const [note, title] =
      row.ambient_state === "quota"
        ? ["квота исчерпана", "ElevenLabs отказал: квота треков исчерпана. Готовые треки на месте — остальные можно догенерировать после пополнения"]
        : row.ambient_state === "bad_key"
          ? ["ключ ElevenLabs отклонён", "ElevenLabs не принял ключ сервера — генерация остановлена. Готовые треки на месте; нужен рабочий ELEVENLABS_API_KEY"]
          : row.ambient_state === "disk_full"
          ? ["нет места на диске", "На диске сервера не хватило места под трек — квота за него не списана"]
          : row.ambient_state === "failed"
            ? [row.ambient_failed ? `ошибок ${row.ambient_failed}` : "прогон упал",
               "Часть треков не вышла — их можно догенерировать кнопкой; причина в карточке сцены"]
            : ["", ""];
    if (!note) return null;
    return (
      <span className="asr-ambient-note asr-broken" title={title}>
        · {note}
      </span>
    );
  }

  const columns = useMemo<Column<BookRecordingChapter>[]>(
    () => [
      {
        key: "title",
        header: "Глава",
        render: (row) => (
          <span className="asr-title">
            {/* Треугольник не кнопка, а указатель: нажимается вся строка, и своя
                мишень рядом с общей только сбивала бы с толку. */}
            <span className="asr-caret" aria-hidden="true">{openChapters.has(row.chapter_id) ? "▾" : "▸"}</span>
            <span className="asr-index num">{row.chapter_index}.</span>
            <span className="asr-title-text" title={row.chapter_title || "Без названия"}>{row.chapter_title || "Без названия"}</span>
            {row.integrity_bad > 0 || row.session_outdated || row.session_markers_skipped > 0 ? <span className="asr-broken" title="Есть проблема с файлами или проектом — раскройте главу">⚠</span> : null}
            {row.released_at ? <span className="asr-done" title="Сведена, прослушана, выложена">Сдана · {shortDate(row.released_at)}</span> : null}
            {row.delivered_at && !row.released_at ? <span className="asr-done" title="Архив скачан целиком — сведение началось">всё записано</span> : null}
          </span>
        ),
      },
      {
        key: "asr",
        header: "Запись реплик",
        className: "asr-col-bar",
        render: (row) => {
          if (row.asr_coverage === null) {
            return row.recorded_roles && canDownload ? (
              <button type="button" className="asr-run" disabled={recognise.isPending}
                onClick={stopRow(() => recognise.mutate(row))}
                title="Распознать записанное и сверить со сценарием">распознать</button>
            ) : <span className="cast-muted">—</span>;
          }
          const whole = row.asr_coverage >= 1;
          const value = whole ? 100 : Math.min(99, Math.round(row.asr_coverage * 100));
          return (
            <span className="asr-bar" role="progressbar" aria-label={`Найдено реплик: ${row.matched_lines} из ${row.total_lines}`}
              aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(100, Math.max(0, value))}>
              <span className={whole ? "asr-bar-fill is-ready" : "asr-bar-fill"} style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
              <span className={whole ? "asr-bar-value num" : "asr-bar-value num asr-holes"}>{value}% · {row.matched_lines}/{row.total_lines}</span>
            </span>
          );
        },
      },
      {
        key: "audit",
        header: "Рассказчик",
        align: "right",
        className: "asr-col-audit",
        nowrap: true,
        render: (row) => {
          const audit = row.audit;
          const busy = audit?.status === "queued" || audit?.status === "running" || audit?.status === "unknown";
          const label = !audit ? "—" : audit.status === "unknown" ? "исход неизвестен" : busy ? "проверяется…"
            : audit.status === "failed" ? "ошибка"
            : audit.status === "skipped" ? "пропущен"
            : audit.confirmed > 0 ? `${audit.confirmed} подтр.`
            : audit.flags > 0 ? `${audit.flags} флаг.` : "✓";
          const title = !audit ? "Аудит ещё не запускался"
            : audit.status === "unknown" ? "Исход неизвестен — повтор не выполняется. Проверьте журнал задач."
            : audit.status === "failed" ? "Прогон остановлен; причину смотрите в журнале задач"
            : audit.status === "skipped" ? (audit.error_message?.includes("narrator_input_changed_after_payment")
              ? "Состав рассказчика изменился во время проверки. Оплаченный результат сохранён; автоматический повтор запрещён."
              : "Проверка пропущена; условия и исход оплаты смотрите в журнале задач")
            : `Строгая проверка (${audit.model}): отклонений ${audit.flags}, подтверждено ${audit.confirmed}. Цена STT ${audit.stt_cost_rub == null ? "неизвестна" : `${audit.stt_cost_rub.toFixed(2)} ₽`}`;
          return (
            <span className="asr-links">
              <span className={audit?.confirmed ? "num asr-holes" : "cast-muted"} title={title}>{label}</span>
              {isAdmin && row.audit_assembled && !busy ? (
                <button
                  type="button"
                  className="asr-run"
                  disabled={auditChapter.isPending}
                  onClick={stopRow(() => auditChapter.mutate(row))}
                  title="Платная проверка сборки рассказчика; стоимость зависит от длительности и тарифов, повтор оплачивается заново"
                >
                  {audit ? "повторить" : "проверить"}
                </button>
              ) : null}
            </span>
          );
        },
      },
      {
        key: "backup",
        header: "Бэкап",
        className: "asr-col-backup",
        render: (row) => {
          const total = row.mirrored_files + row.unmirrored_files;
          if (!total) return <span className="cast-muted">—</span>;
          if (row.mirror_broken || row.stale_unmirrored) return <span className="asr-broken" title={row.mirror_broken ? "Копия на NAS не прошла сверку; файл на сервере цел" : "Копия ожидается больше суток; проверьте NAS"}>Ошибка</span>;
          if (row.unmirrored_files) return <span className="cast-muted" title={`На NAS скопировано ${row.mirrored_files} из ${total} файлов`}>Копируется</span>;
          return <span title={`Все ${total} файлов главы подтверждены на NAS`}>Есть ✓</span>;
        },
      },
      {
        key: "get",
        header: <span className="asr-sr">Архив</span>,
        align: "right",
        className: "asr-col-get",
        nowrap: true,
        render: (row) => (
          <>
          {isAdmin && (row.released_at || row.delivered_at || row.ready) ? (
            <button
              type="button"
              className="asr-get asr-release"
              disabled={release.isPending}
              onClick={stopRow(() => release.mutate(row))}
              title={row.released_at ? "Убрать отметку «Сдана»" : "Глава сведена, прослушана и выложена"}
            >
              {row.released_at ? "Снять отметку" : "Глава сдана"}
            </button>
          ) : null}
          {row.recorded_roles && canDownload ? (
            <>
              <span className="asr-links">
                {/* Голый `.sesx` отдельной ссылкой убран 24.10: в нём пути сервера, и на
                    своём компьютере Audition искал файлы вручную. Сессия едет в архиве,
                    рядом с дублями, и зовёт их по имени. */}
                <a
                  className="asr-get"
                  href={`/api/v2/chapters/${encodeURIComponent(row.chapter_id)}/archive.zip`}
                  onClick={stopRow()}
                  title={`${row.ready ? "Дубли главы" : "То, что уже записано"}, эмбиент и сессия Audition одним архивом. Распакуйте и откройте сессия.sesx — файлы найдутся сами`}
                >
                  {row.ready ? "архив" : "часть архива"}
                </a>
                {ambientControl(row)}
              </span>
              {ambientNote(row)}
            </>
          ) : null}
          </>
        ),
      },
    ],
    [canDownload, isAdmin, release, recognise, auditChapter, ambient, stopAmbient, openChapters],
  );

  const toggleChapter = useCallback((row: BookRecordingChapter) => {
    setOpenChapters((open) => {
      const next = new Set(open);
      if (!next.delete(row.chapter_id)) next.add(row.chapter_id);
      return next;
    });
  }, []);

  const renderDetail = useCallback(
    (row: BookRecordingChapter) => {
      if (!openChapters.has(row.chapter_id)) return null;
      return <>
        <div className="asr-detail-status">
          {row.integrity_bad > 0 ? <span className="asr-broken">Не прошли проверку файлов: {row.integrity_bad}</span>
            : row.integrity_checked ? <span>Файлы проверены: {row.integrity_ok} ✓</span>
            : row.integrity_files > 0 && row.integrity_skipped === row.integrity_files ? <span>Для проверки файлов нет контрольных сумм</span>
            : row.recorded_roles && canDownload ? <button type="button" className="asr-run" disabled={verify.isPending} onClick={() => verify.mutate(row)}>Проверить файлы</button> : null}
          {row.session_outdated ? <span className="asr-broken">Проект устарел</span> : null}
          {row.session_markers_skipped > 0 ? <span className="asr-broken">{row.session_markers_skipped} {plural(row.session_markers_skipped, "маркер", "маркера", "маркеров")} без места</span> : null}
        </div>
        <ChapterFilesRow chapterId={row.chapter_id} bookId={bookId} canDelete={canDeleteTakes} />
      </>;
    },
    [openChapters, bookId, canDeleteTakes, canDownload, verify],
  );

  /* Прогресс книги — по собранным главам: все роли записаны и проект .sesx лежит на
     NAS. Сумма «роль × глава» (сотни при касте в семьдесят) читалась как число ролей
     и сбивала с толку. Устаревший проект не в счёт — его ещё пересоберут.
     «Собрана», а не «сдана»: «сдана» в таблице — отмечена человеком (сведена и выложена),
     а «всё записано» — архив уже забран в сведение. */
  const assembled = chapters.filter((chapter) => chapter.ready && chapter.session_archived && !chapter.session_outdated).length;
  const bookPercent = chapters.length ? Math.round((assembled / chapters.length) * 100) : 0;

  const ready = coverage.data?.ready_chapters ?? 0;
  const started = coverage.data?.started_chapters ?? 0;
  const total = coverage.data?.total_chapters ?? 0;
  const missingLines = coverage.data?.missing_lines ?? 0;
  const delivered = coverage.data?.delivered_chapters ?? 0;
  const released = chapters.filter((chapter) => chapter.released_at).length;

  return (
    <div className="asr">
      <PageHeader
        title="ASR · покрытие"
        subtitle={
          coverage.isLoading
            ? "Считаю покрытие…"
            : total
              ? `Глав: ${total} · в работе ${started} · готовы ${ready} · всё записано ${delivered} · сданы ${released}${missingLines ? ` · не найдено реплик ${missingLines}` : ""}`
              : "Ни одной главы"
        }
        actions={
          books.length > 1 ? (
            <select
              className="ui-input asr-book"
              value={bookId}
              aria-label="Книга"
              onChange={(event) => setParams({ book_id: event.target.value }, { replace: true })}
            >
              {books.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.display_title || item.title}
                </option>
              ))}
            </select>
          ) : undefined
        }
      />

      <div className="asr-summary">
        <div
          className="asr-book-bar"
          role="progressbar"
          aria-label={`Собрано глав книги ${book ? `«${book.display_title || book.title}»` : ""}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={bookPercent}
          title="Собранная глава — все роли записаны и файл проекта .sesx лежит на NAS. «Запись реплик» показывает найденные распознаванием реплики сценария"
        >
          <span className={chapters.length && assembled === chapters.length ? "asr-book-fill is-ready" : "asr-book-fill"} style={{ width: `${bookPercent}%` }} />
          <span className="asr-book-title">{book ? book.display_title || book.title : "—"}</span>
          <span className="asr-book-value num">
            собрано глав {assembled}<span className="asr-of"> из {chapters.length}</span> · {bookPercent}%
          </span>
        </div>
        <Button
          size="sm"
          variant="secondary"
          aria-pressed={onlyStarted}
          icon={<Icon name="filter" />}
          onClick={() => setOnlyStarted((value) => !value)}
        >
          Только начатые
        </Button>
        {onlyStarted ? <span className="asr-count num">{rows.length} из {total}</span> : null}
      </div>

      {coverage.isError ? (
        <div className="ui-note ui-note--error" role="alert">
          Не удалось загрузить покрытие: {describeApiError(coverage.error, "сервер не ответил")}.
        </div>
      ) : (
        <DataTable
          className="asr-table"
          aria-label="Покрытие книги по главам"
          columns={columns}
          rows={rows}
          rowKey={(row) => row.chapter_id}
          onRowClick={toggleChapter}
          rowClassName={(row) => (row.released_at ? "asr-row-released" : undefined)}
          rowLabel={(row) => `Опись главы ${row.chapter_index}. ${row.chapter_title}`}
          renderDetail={renderDetail}
          loading={coverage.isLoading || booksQuery.isLoading}
          skeletonRows={8}
          empty={
            <EmptyState
              icon="mic"
              text={
                onlyStarted
                  ? "Ни одну главу ещё не начали записывать."
                  : book
                    ? "У книги нет глав, открытых для записи."
                    : "Ни одна книга ещё не опубликована для дикторов."
              }
              action={<LinkButton to="/recording">Открыть сценарий</LinkButton>}
            />
          }
        />
      )}
    </div>
  );
}
