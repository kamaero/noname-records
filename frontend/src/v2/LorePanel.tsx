/**
 * Панель «Лор» — шпаргалка актёру о мире книги.
 *
 * Актёр приходит на книгу, которой не читал, и озвучивает расу, о которой не слышал.
 * Здесь он смотрит, как устроен мир, кто в нём живёт и кем приходится его
 * персонаж остальным.
 *
 * Два рода записей и поэтому два поиска: карточки сущностей пришли списком и ищутся
 * на месте, тела статей лежат на сервере и ищутся там же. Ответы показываются вместе,
 * карточки первыми — по имени ищут чаще, чем по тексту.
 */
import "./LorePanel.css";

import { useState } from "react";
import { Icon } from "../components/Icon";
import { Sheet } from "./Sheet";
import { matchEntities, useLore, useLoreArticle, useLoreSearch, type LoreEntity } from "./loreApi";
import { plural } from "./useIsMobile";

type LorePanelProps = {
  bookId: string;
  onClose: () => void;
};

type Selected = { kind: "article"; id: string } | { kind: "entity"; entity: LoreEntity } | null;

/**
 * «Полумрак. Бароны» → «Полумрак»: по первой части строится оглавление.
 *
 * Заголовок без мира («Нежить.», «Магия.») — это общая часть энциклопедии, и своей
 * группы он не заводит: иначе оглавление превратилось бы в два десятка групп по
 * одной статье.
 */
function worldOf(topic: string): string {
  const match = /^(.+?)\.\s+\S/.exec(topic.trim());
  if (!match) return "General";
  return match[1].split(",")[0].trim() || "General";
}

function ArticleView({ id }: { id: string }) {
  const article = useLoreArticle(id);
  const [zoom, setZoom] = useState("");
  if (article.isLoading) return <p className="lore-dim">Opening article…</p>;
  if (article.isError || !article.data) return <p className="ui-note ui-note--error">Could not open article.</p>;
  return (
    <article className="lore-article">
      <h3 className="lore-article-title">{article.data.title}</h3>
      {article.data.images.map((src) => (
        <button key={src} type="button" className="lore-map" onClick={() => setZoom(src)} title="Open full screen">
          <img src={src} alt="World map" loading="lazy" />
        </button>
      ))}
      {article.data.body.split("\n\n").map((paragraph, index) => (
        <p key={index}>{paragraph}</p>
      ))}
      {zoom ? (
        <div className="lore-zoom" role="dialog" aria-label="Map" onClick={() => setZoom("")}>
          <img src={zoom} alt="World map" />
        </div>
      ) : null}
    </article>
  );
}

function EntityView({ entity }: { entity: LoreEntity }) {
  return (
    <article className="lore-article">
      <h3 className="lore-article-title">{entity.name}</h3>
      {entity.portrait ? <img className="lore-portrait" src={entity.portrait} alt={entity.name} loading="lazy" /> : null}
      {entity.aliases.length ? <p className="lore-dim">Also known as: {entity.aliases.join(", ")}</p> : null}
      <p>{entity.description}</p>
      {entity.topic ? <p className="lore-dim">From section «{entity.topic.replace(/\.$/, "")}»</p> : null}
    </article>
  );
}

export function LorePanel({ bookId, onClose }: LorePanelProps) {
  const lore = useLore(bookId);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Selected>(null);
  const search = useLoreSearch(bookId, query);

  const entities = lore.data?.entities ?? [];
  const articles = lore.data?.articles ?? [];
  const foundEntities = matchEntities(entities, query);
  const hits = search.data?.hits ?? [];
  const searching = query.trim().length >= 2;

  const worlds = new Map<string, typeof articles>();
  for (const article of articles) {
    const key = worldOf(article.topic);
    worlds.set(key, [...(worlds.get(key) ?? []), article]);
  }

  return (
    <Sheet
      title="Lore"
      subtitle={lore.data?.author ? `Story world: ${lore.data.author.name}` : undefined}
      onClose={onClose}
    >
      <div className="lore">
        <div className="lore-search">
          <Icon name="search" />
          <input
            className="ui-input"
            value={query}
            placeholder="Find a character, species or term"
            onChange={(event) => {
              setQuery(event.target.value);
              setSelected(null);
            }}
            autoFocus
          />
          {query ? (
            <button type="button" className="ui-btn ui-btn--ghost ui-btn--sm" onClick={() => setQuery("")}>
              Clear
            </button>
          ) : null}
        </div>

        {selected ? (
          <>
            <button type="button" className="ui-btn ui-btn--ghost ui-btn--sm lore-back" onClick={() => setSelected(null)}>
              ← Back to list
            </button>
            {selected.kind === "article" ? <ArticleView id={selected.id} /> : <EntityView entity={selected.entity} />}
          </>
        ) : searching ? (
          <div className="lore-results">
            {foundEntities.length ? (
              <>
                <h4 className="lore-group">Characters and species</h4>
                <ul className="lore-list">
                  {foundEntities.slice(0, 60).map((entity) => (
                    <li key={entity.id}>
                      <button type="button" onClick={() => setSelected({ kind: "entity", entity })}>
                        <strong>{entity.name}</strong>
                        <span className="lore-dim">{entity.description.slice(0, 90)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
            <h4 className="lore-group">In articles {search.isFetching ? <span className="lore-dim">searching…</span> : null}</h4>
            {hits.length ? (
              <ul className="lore-list">
                {hits.map((hit) => (
                  <li key={hit.id}>
                    <button type="button" onClick={() => setSelected({ kind: "article", id: hit.id })}>
                      <strong>{hit.title}</strong>
                      <span className="lore-dim">{hit.snippet}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : search.isFetching ? null : (
              <p className="lore-dim">Nothing found.</p>
            )}
            {!foundEntities.length && !hits.length && !search.isFetching ? null : null}
          </div>
        ) : lore.isLoading ? (
          <p className="lore-dim">Opening…</p>
        ) : (
          <div className="lore-toc">
            {[...worlds.entries()].map(([world, items]) => (
              <section key={world}>
                <h4 className="lore-group">{world}</h4>
                <ul className="lore-list">
                  {items.map((article) => (
                    <li key={article.id}>
                      <button type="button" onClick={() => setSelected({ kind: "article", id: article.id })}>
                        <strong>{article.title}</strong>
                        <span className="lore-dim">
                          {Math.round(article.chars / 1000)} k characters
                          {article.images ? ` · maps: ${article.images}` : ""}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
            {entities.length ? (
              <p className="lore-dim lore-hint">
                And {entities.length} {plural(entities.length, "entry", "entries", "entries")} for characters, species and titles. Search for them by name above.
              </p>
            ) : null}
          </div>
        )}
      </div>
    </Sheet>
  );
}
