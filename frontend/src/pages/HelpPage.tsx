import { useEffect, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import "./HelpPage.css";

function HelpSection({ id, title, lead, children }: { id: string; title: string; lead?: string; children: ReactNode }) {
  return (
    <section id={id} className="help-section ui-card ui-card-pad" aria-labelledby={`${id}-title`}>
      <header className="help-section-head">
        <h2 id={`${id}-title`}>{title}</h2>
        {lead ? <p>{lead}</p> : null}
      </header>
      {children}
    </section>
  );
}

/** Подраздел внутри карточки: свой якорь для содержания, заголовок третьего уровня. */
function HelpSub({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <div id={id} className="help-sub" aria-labelledby={`${id}-title`} role="group">
      <h3 id={`${id}-title`} className="help-sub-title">{title}</h3>
      {children}
    </div>
  );
}

function UiLabel({ children }: { children: ReactNode }) {
  return <span className="help-label">{children}</span>;
}

function Note({ tone = "info", children }: { tone?: "info" | "warning"; children: ReactNode }) {
  return <aside className={`help-note help-note--${tone}`}>{children}</aside>;
}

const toc = [
  ["start", "Voice actor quick start"], ["bot", "Telegram bot"], ["roles", "Who does what"], ["flow", "Book workflow"],
  ["recording", "Uploads"], ["script", "Reading the script"], ["role-lines", "All lines for a role"],
  ["stress", "Word stress"], ["palette", "Role palette"], ["upload", "Files and uploads"],
  ["asr", "ASR and recording progress"], ["mobile", "Using a phone"], ["troubleshooting", "Troubleshooting"],
  ["author-admin", "Authors and administrators"], ["cast-proposal", "Suggested and approved cast"],
  ["consilium", "AI review"], ["sound", "Sound annotation"], ["ambient", "Ambience"], ["terms", "Glossary"],
] as const;

export function HelpPage() {
  // Роутер не прокручивает переходы внутри приложения к якорю автоматически.
  const { hash } = useLocation();
  useEffect(() => {
    const id = decodeURIComponent(hash.replace(/^#/, ""));
    if (!id) return;
    const frame = window.requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView({ block: "start" }));
    return () => window.cancelAnimationFrame(frame);
  }, [hash]);

  return (
    <div className="help">
      <section className="help-hero ui-card ui-card--accent ui-card-pad">
        <div className="help-hero-copy">
          <span className="help-eyebrow">Noname Records</span>
          <h1>Help with your recording workspace</h1>
          <p>Follow a book from automatic annotation and author review to recording, uploads and delivery. Start with the voice actor guide below.</p>
        </div>
        <div className="help-hero-actions">
          <Link className="ui-btn ui-btn--primary" to="/recording">Open script</Link>
          <Link className="ui-btn ui-btn--secondary" to="/asr">View ASR</Link>
        </div>
      </section>

      <nav className="help-toc ui-card ui-card-pad" aria-label="Help contents">
        <strong>Contents</strong>
        <div className="help-toc-links">
          {toc.map(([id, label], index) => <a key={id} href={`#${id}`}><span className="num">{index + 1}</span>{label}</a>)}
        </div>
      </nav>

      <HelpSection id="start" title="Voice actor quick start" lead="Once a book is open for recording, find your text in Script and submit your files in Uploads.">
        <ol className="help-steps help-steps--primary">
          <li><strong>Press Start in the Telegram bot once.</strong><span>The bot needs this permission to send casting, audition and missing-line notifications. Sign in through Telegram at least once, or ask an administrator to link Telegram to your password account. See <a href="#bot">Telegram bot</a>.</span></li>
          <li><strong>Open <Link to="/recording">Script</Link>.</strong><span>Choose your published script and role. Use Upload take above the text to open <Link to="/takes">Uploads</Link> with the same selection.</span></li>
          <li><strong>Check the book, role and chapter.</strong><span>Your selection determines the script, suggested filename and destination of the upload.</span></li>
          <li><strong>Look for <UiLabel>Take for an assigned role</UiLabel>.</strong><span>If it says Audition, have the role assignment corrected before uploading your main recording.</span></li>
          <li><strong>Filter your role.</strong><span>Open <UiLabel>Chapter cast</UiLabel> and select your role. Use ↑ ↓ to move between its lines. <UiLabel>My lines</UiLabel> collects them across the entire book.</span></li>
          <li><strong>Adjust pronunciation and colors.</strong><span>Voice actors can edit stress marks and role palettes directly while reading.</span></li>
          <li><strong>Name, check and upload the file.</strong><span>Copy the suggested filename, wait for the saved confirmation, then find your recording in the recent files list.</span></li>
        </ol>
        <Note tone="warning"><strong>Preparation</strong> is for authors and administrators. Voice actors use reviewed, published chapters in <strong>Script</strong>.</Note>
        <Note>Reopen the welcome guide using <UiLabel>?</UiLabel> in the top bar. On a phone, use the help item at the bottom of the navigation menu.</Note>
      </HelpSection>

      <HelpSection id="bot" title="Telegram bot" lead="The bot lets you know when the studio needs something from you.">
        <h3>Why you need to press Start</h3>
        <p>Telegram bots cannot start a conversation. Open the studio bot and press <UiLabel>Start</UiLabel> once. The welcome guide shows whether the bot can reach you and provides buttons to open it and check again.</p>
        <h3>Messages you may receive</h3>
        <div className="help-task-grid help-task-grid--three">
          <article><h3>🎬 Role approved</h3><p>The role is assigned to you. Record its lines by chapter and submit them as takes. The message links to the role's full script.</p></article>
          <article><h3>🎙 Audition invitation</h3><p>Your name appears with a question mark in the cast, such as Name?. Select the book and role in Uploads. The file will be saved as an audition for review by the author.</p></article>
          <article><h3>🎧 / ✏️ Lines to record</h3><p>A missing-line message means transcription did not find some lines. A reassignment message means annotation changed and those lines now belong to your role. Both include the beginnings of the lines. Send an additional recording as described under <a href="#upload">Files and uploads</a>.</p></article>
        </div>
        <Note>Telegram must be linked to your account and you must have pressed Start. Casting notifications match your name in the cast; missing-line notifications use the recording's actor name. If several accounts match a name, the bot forwards casting messages to the owner and agents instead of choosing a recipient. It also forwards casting messages when no account matches. Missing-line notifications cannot be delivered when no matching recipient exists.</Note>
      </HelpSection>

      <HelpSection id="roles" title="Who does what" lead="Account roles determine the actions available to you.">
        <div className="help-role-grid">
          <article className="help-role-card help-role-card--dictor">
            <div className="help-role-title"><code>dictor</code><strong>Voice actor</strong></div>
            <p>Reads published scripts and submits recordings.</p>
            <h3>Can</h3>
            <ul><li>open Library, Script, Uploads, ASR and Help;</li><li>filter roles and collect a role's lines across a book;</li><li>edit word stress and resolve ambiguous pronunciations;</li><li>change a role's background, text color and typography;</li><li>upload takes for assigned roles and auditions;</li><li>delete their own files within 24 hours of upload;</li><li>view coverage and recording status;</li><li>receive studio bot messages.</li></ul>
            <h3>Cannot</h3>
            <ul><li>open Preparation or compare the script with the original;</li><li>reassign dialogue, approve or publish chapters;</li><li>start processing, ASR, file verification or exports;</li><li>change casting assignments, rates or other cast data.</li></ul>
          </article>
          <article className="help-role-card">
            <div className="help-role-title"><code>author</code><strong>Author</strong></div>
            <p>Reviews the meaning and accuracy of the script.</p>
            <ul><li>checks characters, speaker assignments and the original text;</li><li>corrects ambiguous passages and pronunciation;</li><li>marks chapters reviewed and publishes them to voice actors;</li><li>runs AI review and sound annotation;</li><li>listens to auditions and approves voice actors;</li><li>monitors recordings, ASR, ambience and exports.</li></ul>
          </article>
          <article className="help-role-card">
            <div className="help-role-title"><code>agent</code><strong>Agent</strong></div>
            <ul><li>coordinates voice actors and submits their recordings;</li><li>reads published scripts and exports full role scripts to PDF;</li><li>suggests voice actors for unassigned roles using Name?;</li><li>uploads auditions and takes for their actors, including people without accounts;</li><li>does not start annotation, edit scripts or delete files.</li></ul>
          </article>
          <article className="help-role-card">
            <div className="help-role-title"><code>admin</code><strong>Administrator</strong></div>
            <p>Manages production and account access.</p>
            <ul><li>uploads books and controls automatic processing;</li><li>has author capabilities and manages publication;</li><li>manages user accounts;</li><li>uses the activity log to investigate errors.</li></ul>
          </article>
        </div>
      </HelpSection>

      <HelpSection id="flow" title="Book workflow" lead="The book page shows progress. Each stage uses the results of the previous one.">
        <ol className="help-flow">
          <li><span>1</span><div><strong>Segmentation</strong><p>Split the text into chapters and paragraphs without rewriting it.</p></div></li>
          <li><span>2</span><div><strong>Characters</strong><p>Extract characters, aliases and the cast.</p></div></li>
          <li><span>3</span><div><strong>Roles</strong><p>Assign speakers to dialogue and build the script.</p></div></li>
          <li><span>4</span><div><strong>Word stress</strong><p>For Russian text, dictionaries and context resolve stress marks. Unresolved words enter a queue.</p></div></li>
          <li><span>5</span><div><strong>Review</strong><p>The author or administrator checks the script, makes corrections and approves chapters.</p></div></li>
          <li><span>6</span><div><strong>Publication</strong><p>Only reviewed chapters become available to voice actors.</p></div></li>
          <li><span>7</span><div><strong>Script and uploads</strong><p>Voice actors read their roles in Script and submit audio in Uploads.</p></div></li>
          <li><span>8</span><div><strong>ASR and assembly</strong><p>Check audio coverage and assemble materials for mixing.</p></div></li>
        </ol>
        <Note>Voice actors start in Script. A missing chapter must be reviewed and published by an author or administrator first.</Note>
      </HelpSection>

      <HelpSection id="recording" title="Uploads: choose the right destination" lead="The selected book, role, chapter and actor must describe the file you are submitting.">
        <div className="help-task-grid">
          <article><h3>1. Book</h3><p>Choose the correct <UiLabel>Book</UiLabel> when several are open. A single book is shown without a selector.</p></article>
          <article><h3>2. Role</h3><p>The <UiLabel>Role</UiLabel> list shows available roles and line counts. Your assigned roles are listed below it.</p></article>
          <article><h3>3. Chapter</h3><p>Select the chapter you recorded. This updates the filename and keeps Script on the same chapter. A genuine audition may omit the chapter.</p></article>
          <article><h3>4. Voice actor</h3><p>A voice actor's own name is fixed in <UiLabel>Recorded by</UiLabel>. Agents, authors and administrators may enter the actor's name when uploading on their behalf. This name is used for assignments and coverage.</p></article>
        </div>
        <h3>How takes and auditions are distinguished</h3>
        <div className="help-compare">
          <article className="help-compare-good"><strong>Take for an assigned role</strong><p>The cast assigns this role to the actor named on the upload. The file becomes a recording for the selected chapter.</p></article>
          <article className="help-compare-warn"><strong>Role audition</strong><p>The role is unassigned or the actor is only suggested as Name?. The file goes to auditions. If the role belongs to someone else, check the assignment and Recorded by before continuing.</p></article>
        </div>
        <Note tone="warning">The server determines the upload type. If your assigned role appears as Audition, ask an author or administrator to check your name in the cast before submitting your main take.</Note>
      </HelpSection>

      <HelpSection id="script" title="Reading and navigating the script" lead="Reading controls are above the text. Stress display, context and font settings are saved in this browser.">
        <dl className="help-controls">
          <div><dt><UiLabel>Chapters</UiLabel></dt><dd>Published chapters. Voice actors do not see drafts.</dd></div>
          <div><dt><UiLabel>Prev.</UiLabel> / <UiLabel>Next</UiLabel></dt><dd>Move to adjacent published chapters. The same controls appear below the text.</dd></div>
          <div><dt><UiLabel>Chapter cast</UiLabel></dt><dd>Roles, actors, line counts, notes and colors. Select a role to show its lines with context. The button then shows <UiLabel>My role: …</UiLabel>.</dd></div>
          <div><dt><UiLabel>↑</UiLabel> <UiLabel>↓</UiLabel></dt><dd>Move between the selected role's lines. Keyboard arrows work too, unless an input or dialog has focus.</dd></div>
          <div><dt><UiLabel>Show all</UiLabel></dt><dd>Clear the role filter and restore the full chapter.</dd></div>
          <div><dt><UiLabel>My lines</UiLabel></dt><dd>Open your role across the entire book. With several roles, choose one first. If none is assigned to you, use <UiLabel>All lines for role</UiLabel>.</dd></div>
          <div><dt><UiLabel>Find in chapter…</UiLabel></dt><dd>Show paragraphs containing your search text.</dd></div>
          <div><dt><UiLabel>Word stress</UiLabel>: <UiLabel>none</UiLabel> <UiLabel>rare</UiLabel> <UiLabel>all</UiLabel></dt><dd>Rare shows words prone to mispronunciation and all human-added marks. All also shows common words. None hides marks without deleting pronunciation decisions. Russian stress tools apply to Russian text.</dd></div>
          <div><dt><UiLabel>± paragraphs</UiLabel></dt><dd>Show zero to five context paragraphs around each selected line.</dd></div>
          <div><dt><UiLabel>S</UiLabel> <UiLabel>M</UiLabel> <UiLabel>L</UiLabel></dt><dd>Change the script's font size.</dd></div>
          <div><dt><UiLabel>PDF / print</UiLabel></dt><dd>Open the browser's print dialog to print or save a PDF.</dd></div>
        </dl>
        <Note>Dimmed paragraphs are context. Use the button between excerpts to reveal omitted text. Authors and administrators also have Original, Sound, Uncertain, AI review, Locations and Profile tools. Original is available on desktop; AI review appears after a review run.</Note>
      </HelpSection>

      <HelpSection id="role-lines" title="All lines for a role" lead="Read the role across the whole book, in order, with context around every line.">
        <ol className="help-steps">
          <li><strong>From Script or Uploads:</strong><span>select a role and use <UiLabel>All lines for this role →</UiLabel>.</span></li>
          <li><strong>From the chapter cast:</strong><span>use <UiLabel>all</UiLabel> beside a role, or select it and use the main role script button.</span></li>
          <li><strong>Above the text:</strong><span><UiLabel>My lines</UiLabel> opens your assigned role directly when there is only one.</span></li>
          <li><strong>Navigate:</strong><span>buttons and ↑ ↓ move between lines; the counter shows your position. Use <UiLabel>My roles:</UiLabel> to switch roles. Long scripts load in batches with <UiLabel>Show more</UiLabel>.</span></li>
          <li><strong>Review <UiLabel>Pronunciation traps</UiLabel>:</strong><span>rare words in the role's lines, grouped by chapter, with stress marks where available. <UiLabel>new only</UiLabel> shows each word where the role first meets it; <UiLabel>all</UiLabel> repeats it in every chapter. A ? means stress is unresolved. <UiLabel>Back to lines</UiLabel> returns to the script.</span></li>
          <li><strong>Adjust the view:</strong><span>change context, stress display, font size or print settings.</span></li>
          <li><strong>Return:</strong><span><UiLabel>Back to script</UiLabel> returns to your reading workspace. Each chapter heading opens the full chapter; voice actors can open only published chapters.</span></li>
        </ol>
        <Note>Click a word to open the same stress editor used in the chapter reader. See <a href="#stress">Word stress</a>.</Note>
      </HelpSection>

      <HelpSection id="stress" title="Word stress: queue and individual corrections" lead="Russian pronunciation tools let voice actors correct stress marks. Saved changes are shared with other readers.">
        <h3>Open the queue</h3>
        <ol className="help-steps">
          <li><strong>On desktop:</strong><span>use <UiLabel>Word stress: N unresolved</UiLabel> above the text.</span></li>
          <li><strong>On a phone:</strong><span>open <UiLabel>More</UiLabel>, then the word stress queue.</span></li>
          <li><strong>Choose a category:</strong><span>Author terms, Common Russian or Ambiguous.</span></li>
        </ol>
        <div className="help-task-grid help-task-grid--three">
          <article><h3>Author terms</h3><p>Names, locations and invented words absent from ordinary dictionaries. Usually the best place to start.</p></article>
          <article><h3>Common Russian</h3><p>Ordinary words whose stress could not be resolved reliably from dictionaries and context.</p></article>
          <article><h3>Ambiguous</h3><p>Homographs: identical spellings with pronunciations that depend on meaning. Decisions often apply to one occurrence.</p></article>
        </div>
        <h3>Queue controls</h3>
        <ul className="help-list"><li><strong>Find a word…</strong> filters the current category.</li><li><strong>A word chip</strong> opens a sample passage and the stress editor.</li><li><strong>Show more</strong> loads the next batch.</li><li><strong>✕</strong> marks a word as not needing stress and removes it from this book's queue.</li></ul>
        <h3>Set stress in the text</h3>
        <ol className="help-steps">
          <li><strong>Click a word</strong><span>in the chapter or role script to open the editor.</span></li>
          <li><strong>Select the stressed vowel</strong><span>or move between vowels with ← →. The preview updates immediately.</span></li>
          <li><strong>Choose the scope:</strong><span><UiLabel>For this book</UiLabel> applies to the current book; <UiLabel>For this author</UiLabel> applies across books by the linked author.</span></li>
          <li><strong>Wait for confirmation</strong><span>showing the number of updated segments.</span></li>
        </ol>
        <p className="help-shortcuts"><kbd>←</kbd><kbd>→</kbd> choose vowel <span>·</span> <kbd>Enter</kbd> save to book <span>·</span> <kbd>Esc</kbd> close</p>
        <h3>Resolve a homograph</h3>
        <p>Expand a word in <UiLabel>Ambiguous</UiLabel>. Choosing a pronunciation saves it for the displayed passage only. <UiLabel>In chapter</UiLabel> opens the full context.</p>
        <Note tone="warning">Saving <UiLabel>For this author</UiLabel> requires a linked author profile. Use it for a stable name or term pronounced the same way throughout that author's books.</Note>
      </HelpSection>

      <HelpSection id="palette" title="Role palette" lead="Voice actors can adjust how roles look without changing casting, line assignments or rates.">
        <ol className="help-steps">
          <li><strong>Open <UiLabel>Chapter cast</UiLabel>.</strong><span>It appears beside the text on desktop and as a sheet on a phone.</span></li>
          <li><strong>Find the role and open its palette menu.</strong><span>This opens <UiLabel>Character palette</UiLabel>.</span></li>
          <li><strong>Adjust colors and typography.</strong><span>Choose background, text color, weight and style; the preview updates immediately.</span></li>
          <li><strong>Review any warning.</strong><span>Similar colors in the same chapter can be hard to distinguish, especially in print. You can still choose to keep them.</span></li>
          <li><strong>Press <UiLabel>Save</UiLabel>.</strong><span>The role's lines update to the new palette.</span></li>
        </ol>
        <Note>Voice actors cannot change the Narrator palette, assigned actor, role name or payment data.</Note>
      </HelpSection>

      <HelpSection id="upload" title="Filenames and audio uploads" lead="Check the suggested name and classification before sending a large recording.">
        <h3>Before uploading</h3>
        <ol className="help-steps">
          <li><strong>Check book, role and chapter.</strong><span>Wait for the filename suggestion to update after changing the selection.</span></li>
          <li><strong>Check the file type.</strong><span>Main recordings should say <UiLabel>Take for an assigned role</UiLabel>.</span></li>
          <li><strong>Press <UiLabel>Copy</UiLabel>.</strong><span>Rename your file using the exact suggestion under <UiLabel>Name your file:</UiLabel>.</span></li>
          <li><strong>Drop files into the upload area</strong><span>or click to choose one or more audio files.</span></li>
          <li><strong>Review the preview.</strong><span>Each file shows its final name, warnings or rejection reason.</span></li>
          <li><strong>Press <UiLabel>Upload files</UiLabel>.</strong><span>Keep the page open until the server confirms the files are saved.</span></li>
        </ol>
        <p>Supported audio formats include WAV, MP3, M4A, FLAC, OGG, AAC, OPUS and WEBM. You can upload several files together.</p>
        <p>In a suggested filename, <code>Ch01</code> means chapter one and <code>Ch34</code> means chapter thirty-four. The selected chapter determines the destination. A conflicting number in the filename must be checked before upload.</p>
        <h3>Messages before uploading</h3>
        <dl className="help-errors">
          <div><dt>Role not specified</dt><dd>Choose a role to save the file under.</dd></div>
          <div><dt>Chapter not specified</dt><dd>A take needs a chapter; an audition may omit it.</dd></div>
          <div><dt>Duplicate files in this batch</dt><dd>The same file was selected twice. Keep one copy.</dd></div>
          <div><dt>This file was already uploaded</dt><dd>Identical content already exists under this role and chapter. No need to resend it.</dd></div>
          <div><dt>Filename role differs</dt><dd>Check the selected role and original filename.</dd></div>
          <div><dt>Filename chapter does not match your selection</dt><dd>Check both chapter numbers. If the selected chapter is correct and the filename is simply wrong, confirm that the selection is correct.</dd></div>
        </dl>
        <p><strong>Additional recordings.</strong> Send either the full role file again under its usual name, or just the missing/corrected lines with <code>fix1</code>, <code>fix2</code>, and so on appended to the filename, for example <code>Book_Ch11_Alice_fix1.wav</code>. Both forms count toward coverage; the chapter session uses the latest submitted recording for each line.</p>
        <h3>After uploading</h3>
        <ul className="help-list"><li>Look for <strong>Done; saved N files</strong>.</li><li>Check time, role and filename under <UiLabel>Recent chapter files</UiLabel>.</li><li><strong>Uploaded N of M</strong> means part of the batch was saved. Retry only rejected files.</li><li><UiLabel>Clear list</UiLabel> clears the form without deleting server files.</li><li>Voice actors can delete their own files within 24 hours using <UiLabel>Delete</UiLabel>. This removes the server file and NAS mirror and cannot be undone. Later deletion requires an author or administrator.</li></ul>
        <Note tone="warning">Keep your originals until the final chapter is released and store a safe backup. Uploading to the studio does not replace your own recording archive.</Note>
      </HelpSection>

      <HelpSection id="asr" title="ASR and recording progress" lead="ASR shows what the system found in uploaded recordings and what is still missing.">
        <p>Open <Link to="/asr">ASR</Link> and select the book. The bar above the table shows assembled chapters. <UiLabel>Started only</UiLabel> hides chapters without recordings. Expand a chapter to inspect its files.</p>
        <dl className="help-controls">
          <div><dt>Recorded lines</dt><dd>Expected script lines found by transcription and their percentage of the chapter.</dd></div>
          <div><dt>Narrator</dt><dd>Strict audit of the assembled narrator recording, with confirmed findings and passages needing attention. The owner must enable this paid feature. Each retry is charged separately.</dd></div>
          <div><dt>Backup</dt><dd>Verified means all chapter files have confirmed NAS copies. Copying means some are still pending. An error needs a storage or checksum check.</dd></div>
          <div><dt>File verification</dt><dd>Inside an expanded chapter, checksums show whether files still match what the server received.</dd></div>
        </dl>
        <div className="help-status-grid"><div><strong>—</strong><span>no data yet;</span></div><div><strong>✓</strong><span>step confirmed;</span></div><div><strong>⚠</strong><span>needs author or administrator attention;</span></div><div><strong>all recorded</strong><span>the full chapter archive has been downloaded for mixing;</span></div><div><strong>delivered</strong><span>mixed, reviewed and published; marked manually.</span></div></div>
        <Note>Voice actors can view coverage. Starting transcription/alignment and downloading sessions, archives or projects requires author or administrator access.</Note>
      </HelpSection>

      <HelpSection id="mobile" title="Using a phone" lead="Secondary tools are folded away to leave more room for the text.">
        <ul className="help-list"><li>Open navigation from the top menu. The welcome guide and theme switch are at the bottom.</li><li><UiLabel>Chapters</UiLabel> and <UiLabel>Chapter cast</UiLabel> open as sheets.</li><li>Search, stress, context, font and print tools are under <UiLabel>More</UiLabel>.</li><li>↑ ↓ stays beside the selected role.</li><li>Use stable Wi-Fi for large uploads and keep the browser open until saving is confirmed.</li></ul>
      </HelpSection>

      <HelpSection id="troubleshooting" title="Troubleshooting" lead="Check the current state shown in the interface before repeating an action.">
        <div className="help-faq">
          <details><summary>A book or chapter is missing from Script</summary><p>Voice actors see published chapters only. An author or administrator must review and publish the chapter first.</p></details>
          <details><summary>My role is missing or is not recognized as mine</summary><p>Ask an author to compare your account name with the assigned actor name in the cast. A question mark means a suggestion, not an approved assignment.</p></details>
          <details><summary>The upload says Audition instead of Take</summary><p>Check Recorded by and the cast assignment before sending the main recording. The server determines the file type from those names.</p></details>
          <details><summary>ASR is still empty after upload</summary><p>Check recent files first. Transcription starts automatically after saving and may take several minutes.</p></details>
          <details><summary>I changed a stress mark but cannot see it</summary><p>Select rare or all in Word stress and wait for the saved confirmation.</p></details>
          <details><summary>The bot does not send messages</summary><p>Open it in Telegram and press Start. Reopen the welcome guide with ? and check reachability again. If it is reachable, ask the author to verify your cast name and linked Telegram account.</p></details>
          <details><summary>I uploaded the wrong file</summary><p>Within 24 hours, delete your own file under Recent chapter files. Later, ask an author or administrator.</p></details>
        </div>
      </HelpSection>

      <HelpSection id="author-admin" title="Authors and administrators" lead="Responsibilities explain who can resolve a problem and why some controls are hidden from voice actors.">
        <div className="help-task-grid">
          <article><h3>Author</h3><ol><li>Open Preparation and compare the script with the original.</li><li>Correct speaker assignments and ambiguous passages.</li><li>Review characters and pronunciation.</li><li>Mark chapters reviewed and publish them.</li><li>Monitor recording and ASR.</li></ol></article>
          <article><h3>Administrator</h3><ol><li>Upload books and manage processing.</li><li>Investigate stopped runs and errors.</li><li>Manage users and access roles.</li><li>Control publication, verification and exports.</li><li>Inspect actions and failures in the log.</li></ol></article>
        </div>

        <HelpSub id="cast-proposal" title="Cast: suggest or approve a voice actor">
          <p>Open <UiLabel>Cast</UiLabel> from the book page. A plain actor name means an approved assignment; <strong>Name?</strong> means an audition suggestion.</p>
          <ul className="help-list"><li>Suggesting an actor invites them to audition. Approving the actor assigns the role and notifies them.</li><li>Notifications are sent only when the actor or effective status changes. Rewriting an approved name as Name?, or adding another question mark, does not send another message.</li><li>When several accounts match a name, casting messages are forwarded to the owner and agents with a candidate list. Missing accounts are also reported to them.</li><li>The audition count in a role row opens its recordings. <UiLabel>Auditions · N</UiLabel> filters to roles with submitted auditions.</li><li>Agents may suggest actors only for unassigned roles. An existing assignment is preserved. The Narrator is selected by the owner or author.</li><li>Author and owner decisions follow voting priority. Review the result shown after assigning an actor.</li></ul>
        </HelpSub>

        <HelpSub id="consilium" title="AI review">
          <p>Two independent AI reviewers identify speakers without seeing the script assignments. Where they disagree with the script, an arbiter checks the text for evidence.</p>
          <ol className="help-steps">
            <li><strong>Start.</strong><span>On the book's Review step, open <UiLabel>AI review panel</UiLabel>. Review the estimate and RouterAI balance first. <UiLabel>Recheck</UiLabel> reuses reviewer responses for unchanged chapters and charges for arbitration. <UiLabel>Reread all</UiLabel> reads the book again.</span></li>
            <li><strong>Track progress.</strong><span>Use <UiLabel>stop</UiLabel> to finish and save the current section before stopping. The owner receives a Telegram message on completion.</span></li>
            <li><strong>Inspect findings.</strong><span><UiLabel>AI review: N</UiLabel> opens unresolved findings in the reader. Each card shows the script assignment, independent responses, arbiter decision and evidence quote. Insufficient evidence leaves the decision to you.</span></li>
            <li><strong>Decide.</strong><span>Choose a role, keep the script assignment or choose another role, then use <UiLabel>next finding →</UiLabel>. A warning identifies passages edited after the run.</span></li>
          </ol>
        </HelpSub>

        <HelpSub id="sound" title="Sound annotation">
          <p>Sound annotations help the engineer identify scenes, locations, transitions and significant sounds. Authors and administrators can use this layer.</p>
          <h3>Starting a run</h3>
          <ol className="help-steps">
            <li><strong>Open <UiLabel>Sound annotation</UiLabel> on the Review step.</strong><span>Review the chapter estimate and RouterAI balance. <UiLabel>Annotate remaining chapters</UiLabel> handles missing, incomplete or changed chapters. <UiLabel>Annotate again</UiLabel> reads all chapters. Both preserve human edits and deletions.</span></li>
            <li><strong>Watch progress.</strong><span>The status shows the current chapter and spending. Stopping finishes and saves the current chapter. The owner receives a Telegram message when the run ends.</span></li>
            <li><strong>Open the result.</strong><span>The results link opens the reader's <UiLabel>Book locations</UiLabel> sheet.</span></li>
          </ol>
          <h3>The sound layer</h3>
          <p><UiLabel>Sound</UiLabel> shows or hides the layer. On a phone, find it under <UiLabel>More</UiLabel>.</p>
          <dl className="help-controls">
            <div><dt>▸ Scene bar</dt><dd>Appears before a scene's first paragraph with location, ambience and mood. Other chapter references show where that location recurs. Search buttons copy sound queries; the edit button opens the scene.</dd></div>
            <div><dt>◆ Transition</dt><dd>A dashed row between paragraphs for flashbacks, dreams or fades to silence. Click to edit.</dd></div>
            <div><dt>● Sound</dt><dd>A marker beside or after a paragraph. Hover to highlight the associated phrase.</dd></div>
            <div><dt>Unplaced markers</dt><dd>After text edits, a marker may lose its paragraph. Use <UiLabel>Place on paragraph</UiLabel> to position it again.</dd></div>
          </dl>
          <h3>Editing a marker</h3>
          <ul className="help-list"><li>Scenes have location, time, weather, ambience, mood and music queries. Transitions describe what happens. Sounds have a description, queries and an exact quote. Save protects your edit from future runs; Delete prevents the marker from returning.</li><li>Use <UiLabel>Move scene start to another paragraph</UiLabel> or <UiLabel>Move to another paragraph</UiLabel>, then click the new paragraph. Esc cancels.</li><li><UiLabel>+ Add sound to this paragraph</UiLabel> creates a sound; <UiLabel>to another paragraph…</UiLabel> changes its destination.</li><li><UiLabel>Annotate chapter again…</UiLabel> is a paid AI request. Only AI markers are replaced; manual edits and deletions remain.</li><li>AI scenes retain their identity and ambience when they stay on the same paragraph or move up to three paragraphs within the same location. A removed scene, a larger move, or a move to a different location may detach the old track. The file stays in the chapter folder but is no longer included in the project. Manually edited scenes are preserved.</li></ul>
          <h3>Book locations</h3>
          <p><UiLabel>Locations</UiLabel> opens Book locations. Under <strong>Same location or different?</strong>, merge keeps the first name and moves the other location's scenes to it. Separate dismisses that suggested pair for future runs. Edit names, descriptions and environmental queries under All locations; chapter references open their chapters.</p>
          <Note>Scenes become range markers in the Audition session; transitions and sounds become point markers. Markers without recorded lines or with zero duration may be unplaced. Editing markers makes an active chapter project outdated and triggers a rebuild. Chapters already downloaded for mixing or marked delivered are preserved. Administrators mark delivery in ASR; delivered rows appear dimmed.</Note>
        </HelpSub>

        <HelpSub id="ambient" title="Ambience">
          <p>Ambience is instrumental background music. Each scene receives its own track: AI writes the prompt and ElevenLabs generates the music. Earlier track descriptions help vary instruments and texture.</p>
          <ol className="help-steps">
            <li><strong>Finish recording the chapter and add scene annotations.</strong><span>The chapter's ASR row then offers <UiLabel>ambience</UiLabel>.</span></li>
            <li><strong>Confirm the estimate.</strong><span>The dialog shows track count, duration and ElevenLabs quota use. Scenes without a timeline position are skipped and listed in the confirmation.</span></li>
            <li><strong>Wait for tracks.</strong><span>Each takes a few minutes. The counter shows progress; <UiLabel>stop</UiLabel> finishes the current track without starting another. A check mark means all eligible scenes have tracks.</span></li>
          </ol>
          <ul className="help-list"><li>Tracks follow the recorded scene length, with a minimum of three minutes and a maximum of ten.</li><li>Files are saved in the chapter folder beside takes and included in its archive. Repeated location names receive a number suffix. Keep generated filenames unchanged.</li><li>The Audition session alternates tracks between <strong>Ambient 1</strong> and <strong>Ambient 2</strong>, starting each at its scene boundary.</li><li><strong>Regenerate one scene:</strong> open its card in the reader. The Ambience block shows status, player and prompt. Edited prompts are sent as entered; otherwise AI writes a new prompt. Generate or Regenerate ambience uses one ElevenLabs track from your quota and keeps the previous file in the chapter folder.</li></ul>
          <dl className="help-errors">
            <div><dt>Quota exhausted</dt><dd>Existing tracks are safe. Top up before generating the rest.</dd></div>
            <div><dt>Out of disk space</dt><dd>The server could not save the track. No quota was charged for it.</dd></div>
            <div><dt>ElevenLabs key rejected</dt><dd>Generation stopped. Configure a working key.</dd></div>
            <div><dt>Errors / run failed</dt><dd>Some tracks failed. Review their scene cards before generating again; a failed request may already have been charged.</dd></div>
          </dl>
        </HelpSub>
      </HelpSection>

      <HelpSection id="terms" title="Glossary" lead="Terms used in labels, statuses and messages.">
        <dl className="help-terms">
          <div><dt>Cast</dt><dd>Roles, actors, line counts, notes and palettes for a book.</dd></div><div><dt>Narrator</dt><dd>The narrative voice, with a neutral palette that voice actors cannot edit.</dd></div>
          <div><dt>Take</dt><dd>A main recording of a role approved for that actor.</dd></div><div><dt>Audition</dt><dd>A sample recording without an approved assignment to that actor.</dd></div>
          <div><dt>Context</dt><dd>Dimmed paragraphs around a selected role's lines.</dd></div><div><dt>Author term pronunciation</dt><dd>Stress assigned to a name, location or invented word.</dd></div>
          <div><dt>Homograph</dt><dd>A word with identical spelling but different pronunciation depending on meaning.</dd></div><div><dt>ASR</dt><dd>Speech recognition and comparison of recorded lines with the script.</dd></div>
          <div><dt>Coverage</dt><dd>The proportion of expected roles or lines found in recordings.</dd></div><div><dt>File verification</dt><dd>Checksums confirming that audio files remain intact.</dd></div>
          <div><dt>NAS</dt><dd>Separate storage for verified backup copies.</dd></div><div><dt>Published</dt><dd>A reviewed chapter made available to voice actors.</dd></div>
          <div><dt>Name?</dt><dd>An actor suggested for a role and invited to audition, but not yet approved.</dd></div><div><dt>Marker</dt><dd>A scene, transition or sound annotation.</dd></div>
          <div><dt>Ambience</dt><dd>A unique background music track for each scene.</dd></div><div><dt>All recorded</dt><dd>All roles are recorded and the full chapter archive has been downloaded for mixing. Downloading a partial archive does not give this status. Marker edits and new ambience no longer mark that project outdated.</dd></div><div><dt>Delivered</dt><dd>The chapter has been mixed, reviewed and published. An administrator marks it manually. When all chapters are delivered, the system offers to mark the book as recorded.</dd></div>
        </dl>
        <div className="help-footer-actions"><Link className="ui-btn ui-btn--primary" to="/recording">Open script</Link><Link className="ui-btn ui-btn--secondary" to="/">Back to library</Link></div>
      </HelpSection>
    </div>
  );
}
