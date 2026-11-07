/**
 * «Слова-ловушки» — the rare words of one role, chapter by chapter, stressed, to look at before the session.
 *
 * The narrator of «Полумракские байки» said карли́ца and сомнамбу́ла reading in flow; the
 * marks were there, he did not look. This is what to look at: only this role's words,
 * only rare ones, a word nobody marked yet shown with «?». «Только новые» lists a word
 * in the chapter where the role meets it first, so a later chapter's list stays short.
 */
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiGet, describeApiError } from "../api/client";
import { SkeletonPanel } from "../components/Skeleton";
import { stressSourceLabel } from "./editorApi";
import { plural } from "./useIsMobile";

type TrapWord = { word: string; stressed: string | null; source: string | null; count: number; segment_id: string };

type RoleTrapsResponse = {
  ok: boolean;
  role: string;
  fresh: boolean;
  chapters: Array<{ chapter_id: string; chapter_index: number; chapter_title: string; words: TrapWord[] }>;
};

export function RoleTraps({ bookId, role, fresh, onFresh, chapterHref, heading }: {
  bookId: string;
  role: string;
  fresh: boolean;
  onFresh: (fresh: boolean) => void;
  chapterHref: (chapterId: string) => string;
  heading: (index: number, title: string) => string;
}) {
  const query = useQuery({
    queryKey: ["v2", "role-traps", bookId, role, fresh],
    queryFn: () =>
      apiGet<RoleTrapsResponse>(
        `/api/v2/books/${encodeURIComponent(bookId)}/role-traps?role=${encodeURIComponent(role)}&fresh=${fresh ? 1 : 0}`,
      ),
    enabled: Boolean(bookId && role),
  });

  if (query.isLoading) return <SkeletonPanel label={`Collecting rare words for role «${role}»…`} lines={6} />;
  if (query.isError || !query.data) {
    return <div className="v2r-empty"><p>{describeApiError(query.error, "Could not load pronunciation traps.")}</p></div>;
  }
  const chapters = query.data.chapters;
  const total = chapters.reduce((sum, chapter) => sum + chapter.words.length, 0);
  const unmarked = chapters.reduce((sum, chapter) => sum + chapter.words.filter((word) => !word.stressed).length, 0);

  return (
    <article className="v2r-page v2r-traps">
      <div className="v2r-traps-head">
        <p className="v2r-traps-lead">
          Rare words in this role's lines that are easy to mispronounce. Review them before recording.
          {unmarked > 0 ? <> A word marked with ? has no stress assigned yet. Check it.</> : null}
        </p>
        <div className="v2r-seg" role="group" aria-label="Words to show">
          <button type="button" className={fresh ? "v2r-seg-btn is-on" : "v2r-seg-btn"} aria-pressed={fresh}
            onClick={() => onFresh(true)} title="Show each word only in the chapter where the role first encounters it">
            new only
          </button>
          <button type="button" className={!fresh ? "v2r-seg-btn is-on" : "v2r-seg-btn"} aria-pressed={!fresh}
            onClick={() => onFresh(false)} title="All rare words in each chapter, including previously seen words">
            all
          </button>
        </div>
      </div>

      {total === 0 ? (
        <div className="v2r-empty"><p>Role «{role}» has no rare words in its lines.</p></div>
      ) : (
        chapters.map((chapter) => (
          <section key={chapter.chapter_id} className="v2r-rolescript-chapter">
            <h2 className="v2r-rolescript-h">
              <Link to={chapterHref(chapter.chapter_id)} title="Open full chapter">
                {heading(chapter.chapter_index, chapter.chapter_title)}
              </Link>
              <span className="num">
                {chapter.words.length} {plural(chapter.words.length, "word", "words", "words")}
              </span>
            </h2>
            <p className="v2r-traps-words">
              {chapter.words.map((word, index) => (
                <span key={word.word}>
                  {index > 0 ? " · " : null}
                  <span
                    className={word.stressed ? "v2r-trap" : "v2r-trap v2r-trap--unmarked"}
                    title={word.stressed
                      ? `${stressSourceLabel(word.source)}${word.count > 1 ? ` · ${word.count} times in chapter` : ""}`
                      : "No stress assigned; check this word"}
                  >
                    {word.stressed ?? word.word}
                    {word.stressed ? null : <sup>?</sup>}
                  </span>
                </span>
              ))}
            </p>
          </section>
        ))
      )}
    </article>
  );
}
