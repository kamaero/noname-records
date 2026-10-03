/**
 * «Иллюстрации»: кто на авторской картинке.
 *
 * В книге 190 иллюстраций, и ни одна не подписана — узнать героя можно только по
 * сцене вокруг. Поэтому экран показывает по одной картинке крупно, под ней текст, из
 * которого она выросла, и кандидатов, найденных в этом тексте. Человек нажимает имя
 * (или цифру), и открывается следующая: 190 картинок проходятся за один присест
 * только так, а не сеткой миниатюр, где ничего не разглядеть.
 *
 * Сетка миниатюр была бы к тому же тяжелее: 43 МБ картинок, и уменьшать их нечем —
 * Pillow в зависимостях нет. По одной за раз грузится ровно то, что смотрят.
 */
import "./IllustrationsPage.css";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPostJson, describeApiError } from "../api/client";
import { useToast } from "../components/ToastProvider";
import { Button, EmptyState, PageHeader } from "../ui";
import { Icon } from "../components/Icon";

type Candidate = { character_id: string; name: string; hits: number };

type Illustration = {
  id: string;
  ordinal: number;
  url: string;
  context: string;
  chapter: string;
  status: "new" | "bound" | "skipped";
  character: { id: string; name: string } | null;
  candidates: Candidate[];
};

type IllustrationsResponse = {
  book: { id: string; title: string };
  counts: { total: number; bound: number; skipped: number };
  cast: Array<{ character_id: string; name: string }>;
  items: Illustration[];
};

/** Контекст с картинкой посередине: метку из разбора показываем как разрыв. */
function ContextText({ text }: { text: string }) {
  const [before, after] = text.split("⟦здесь картинка⟧");
  return (
    <p className="ill-context">
      {before}
      {after !== undefined ? <mark className="ill-here">здесь картинка</mark> : null}
      {after}
    </p>
  );
}

export function IllustrationsPage() {
  const { bookId = "" } = useParams();
  const queryClient = useQueryClient();
  const { pushToast } = useToast();
  const [position, setPosition] = useState(0);
  const [search, setSearch] = useState("");

  const data = useQuery({
    queryKey: ["illustrations", bookId],
    queryFn: () => apiGet<IllustrationsResponse>(`/api/v2/books/${encodeURIComponent(bookId)}/illustrations`),
    enabled: Boolean(bookId),
    staleTime: 60_000,
  });

  const items = useMemo(() => data.data?.items ?? [], [data.data]);
  const current = items[position];

  const bind = useMutation({
    // id картинки едет вместе с выбором: пока сервер отвечает, человек может уже
    // перелистнуть, и ответ нельзя записывать в ту, что на экране сейчас.
    mutationFn: ({ id, ...body }: { id: string; character_id?: string; skip?: boolean }) =>
      apiPostJson<{ ok: boolean }>(`/api/v2/illustrations/${encodeURIComponent(id)}`, body),
    onSuccess: (_result, body) => {
      queryClient.setQueryData<IllustrationsResponse>(["illustrations", bookId], (old) => {
        if (!old) return old;
        const next = old.items.map((item) =>
          item.id !== body.id
            ? item
            : {
                ...item,
                status: body.skip ? ("skipped" as const) : body.character_id ? ("bound" as const) : ("new" as const),
                character: body.character_id
                  ? { id: body.character_id, name: old.cast.find((c) => c.character_id === body.character_id)?.name ?? "" }
                  : null,
              });
        return {
          ...old,
          items: next,
          counts: {
            total: next.length,
            bound: next.filter((item) => item.status === "bound").length,
            skipped: next.filter((item) => item.status === "skipped").length,
          },
        };
      });
      // Дальше сама: за один присест проходят весь список, и лишний клик на каждой
      // картинке — это 190 лишних кликов.
      const done = items.findIndex((item) => item.id === body.id);
      setPosition((value) => (value === done ? Math.min(items.length - 1, value + 1) : value));
      setSearch("");
    },
    onError: (error) => {
      pushToast({ tone: "error", title: "Не сохранилось", detail: describeApiError(error, "Сервер не ответил.") });
    },
  });

  // Следующая картинка подгружается заранее — иначе каждый переход это пауза на 200 КБ.
  useEffect(() => {
    const next = items[position + 1];
    if (!next) return;
    const img = new Image();
    img.src = next.url;
  }, [items, position]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA")) return;
      if (!current) return;
      if (event.key === "ArrowRight") setPosition((value) => Math.min(items.length - 1, value + 1));
      if (event.key === "ArrowLeft") setPosition((value) => Math.max(0, value - 1));
      // Держат клавишу или жмут дважды — второй выбор ушёл бы в следующую картинку.
      if (bind.isPending) return;
      if (event.key.toLowerCase() === "s" || event.key === "ы") bind.mutate({ id: current.id, skip: true });
      const digit = Number(event.key);
      if (digit >= 1 && digit <= 9 && current.candidates[digit - 1]) {
        bind.mutate({ id: current.id, character_id: current.candidates[digit - 1].character_id });
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [items.length, current, bind]);

  if (data.isLoading) return <div className="ill"><p className="ill-dim">Открываю…</p></div>;
  if (data.isError) {
    return (
      <div className="ill">
        <div className="ui-note ui-note--error">Не удалось открыть: {describeApiError(data.error, "сервер не ответил")}.</div>
      </div>
    );
  }
  if (!items.length) {
    return (
      <div className="ill">
        <PageHeader title="Иллюстрации" back={{ to: `/books/${bookId}`, label: "К книге" }} />
        <EmptyState icon="doc" text="У этой книги нет загруженных иллюстраций." />
      </div>
    );
  }

  const counts = data.data?.counts ?? { total: 0, bound: 0, skipped: 0 };
  const left = counts.total - counts.bound - counts.skipped;
  const cast = data.data?.cast ?? [];
  const found = search.trim().length >= 2
    ? cast.filter((item) => item.name.toLowerCase().includes(search.trim().toLowerCase())).slice(0, 12)
    : [];

  return (
    <div className="ill">
      <PageHeader
        title="Иллюстрации"
        subtitle={`${data.data?.book.title ?? ""} · привязано ${counts.bound}, отложено ${counts.skipped}, осталось ${left}`}
        back={{ to: `/books/${bookId}`, label: "К книге" }}
      />

      <div className="ill-body">
        <figure className="ill-figure">
          <img src={current.url} alt={`Иллюстрация ${current.ordinal}`} />
          <figcaption className="ill-dim">
            №{current.ordinal} из {counts.total}
            {current.chapter ? ` · ${current.chapter}` : ""}
            {current.status === "bound" && current.character ? ` · привязана: ${current.character.name}` : ""}
            {current.status === "skipped" ? " · отложена как сцена" : ""}
          </figcaption>
        </figure>

        <div className="ill-side">
          <ContextText text={current.context} />

          <div className="ill-pick">
            <h3 className="ill-h">Кто на картинке?</h3>
            <div className="ill-buttons">
              {current.candidates.map((candidate, index) => (
                <button
                  key={candidate.character_id}
                  type="button"
                  className={
                    current.character?.id === candidate.character_id
                      ? "btn btn-sm v2r-btn is-on"
                      : "btn btn-sm v2r-btn"
                  }
                  onClick={() => bind.mutate({ id: current.id, character_id: candidate.character_id })}
                  title={`Названа в тексте ${candidate.hits} раз — клавиша ${index + 1}`}
                >
                  <span className="ill-key">{index + 1}</span> {candidate.name}
                </button>
              ))}
              {current.candidates.length ? null : <span className="ill-dim">В тексте рядом никого не нашлось — ищите вручную.</span>}
            </div>

            <label className="ill-search">
              <Icon name="search" />
              <input
                className="ui-input"
                value={search}
                placeholder="Другой персонаж"
                onChange={(event) => setSearch(event.target.value)}
              />
            </label>
            {found.length ? (
              <div className="ill-buttons">
                {found.map((item) => (
                  <button
                    key={item.character_id}
                    type="button"
                    className="btn btn-sm v2r-btn"
                    onClick={() => bind.mutate({ id: current.id, character_id: item.character_id })}
                  >
                    {item.name}
                  </button>
                ))}
              </div>
            ) : null}
          </div>

          <div className="ill-actions">
            <Button size="sm" variant="ghost" onClick={() => bind.mutate({ id: current.id, skip: true })} loading={bind.isPending}>
              Не портрет <span className="ill-key">S</span>
            </Button>
            {current.status !== "new" ? (
              <Button size="sm" variant="ghost" onClick={() => bind.mutate({ id: current.id, character_id: "" })}>
                Снять привязку
              </Button>
            ) : null}
            <span className="ill-nav">
              <Button size="sm" variant="secondary" disabled={position === 0} onClick={() => setPosition((v) => v - 1)}>←</Button>
              <span className="ill-dim num">{position + 1} / {items.length}</span>
              <Button
                size="sm"
                variant="secondary"
                disabled={position >= items.length - 1}
                onClick={() => setPosition((v) => v + 1)}
              >
                →
              </Button>
            </span>
          </div>
          <p className="ill-dim">Цифры 1–9 выбирают кандидата, S откладывает, стрелки листают.</p>
        </div>
      </div>
    </div>
  );
}
