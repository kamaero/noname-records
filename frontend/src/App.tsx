import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./api/client";
import type { MeResponse } from "./types";
import { AppShell, roleAccess } from "./layout/AppShell";
import { SkeletonScreen } from "./components/Skeleton";
import { BookCommandCenterPage } from "./pages/BookCommandCenterPage";
import { IllustrationsPage } from "./pages/IllustrationsPage";
import { CastPage } from "./pages/CastPage";
import { CommandCenterPage } from "./pages/CommandCenterPage";
import { HelpPage } from "./pages/HelpPage";
import { RecordingPage } from "./pages/RecordingPage";
import { AsrCoveragePage } from "./pages/AsrCoveragePage";
import { RoleScriptPage } from "./v2/RoleScriptPage";
import { LogPage } from "./pages/LogPage";
import { LoginPage } from "./pages/LoginPage";
import { UploadPage } from "./pages/UploadPage";
import { UsersPage } from "./pages/UsersPage";
import { AuditionsPage } from "./pages/AuditionsPage";
import { DictorsPage } from "./pages/DictorsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { ReaderEntryPage, ScriptReaderPage } from "./v2/ScriptReader";

function Layout() {
  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: () => apiGet<MeResponse>("/api/me"),
  });

  if (meQuery.isLoading) {
    return <SkeletonScreen label="Загружаю профиль…" lines={4} />;
  }

  if (meQuery.isError || !meQuery.data?.authenticated) {
    window.location.href = "/app/login";
    return null;
  }

  const me = meQuery.data;

  return (
    <AppShell me={me}>
      <Routes>
        <Route path="/" element={<CommandCenterPage />} />
        <Route path="/command" element={<CommandCenterPage />} />
        <Route path="/books" element={<CommandCenterPage />} />
        <Route path="/books/:bookId" element={<BookCommandCenterPage />} />
        <Route path="/books/:bookId/cast" element={<CastPage />} />
        {/* Экрана карты больше нет: она въехала в Каст. Ссылка могла разойтись
            по переписке — ведём туда, где теперь живут роли. */}
        <Route path="/books/:bookId/roles" element={<BookSubpageRedirect sub="cast" />} />
        <Route path="/books/:bookId/illustrations" element={<IllustrationsPage />} />
        <Route path="/recording" element={<RecordingPage />} />
        <Route path="/asr" element={<AsrCoveragePage />} />
        <Route path="/reader" element={<PreparationRoute me={me}><ReaderEntryPage /></PreparationRoute>} />
        <Route
          path="/reader/role/:bookId"
          element={<PreparationRoute me={me}><RoleScriptPage /></PreparationRoute>}
        />
        <Route
          path="/reader/:chapterId"
          element={<PreparationRoute me={me}><ScriptReaderPage /></PreparationRoute>}
        />
        <Route path="/recording/role/:bookId" element={<RoleScriptPage />} />
        <Route path="/upload" element={<UploadPage />} />
        <Route path="/users" element={<UsersPage me={me} />} />
        <Route path="/dictors" element={<DictorsPage me={me} />} />
        <Route path="/auditions" element={<AuditionsPage />} />
        <Route path="/settings" element={<SettingsPage me={me} />} />
        <Route path="/log" element={<LogPage me={me} />} />
        <Route path="/help" element={<HelpPage />} />

        {/* ---- v1 routes, kept as redirects so old links/bookmarks still land somewhere ---- */}
        <Route path="/workspace" element={<Navigate to="/" replace />} />
        <Route path="/prep" element={<Navigate to="/" replace />} />
        <Route path="/check" element={<Navigate to="/books" replace />} />
        <Route path="/validation" element={<Navigate to="/books" replace />} />
        <Route path="/asr-daw" element={<Navigate to="/books" replace />} />
        <Route path="/check/script/:chapterId" element={<ReaderRedirect />} />
        <Route path="/validation/script/:chapterId" element={<ReaderRedirect />} />
        <Route path="/recording/script/:chapterId" element={<ReaderRedirect />} />
        <Route path="/script/:chapterId" element={<ReaderRedirect />} />
        <Route path="/budget/:bookId" element={<BookSubpageRedirect sub="cast" />} />
        <Route path="/books/:bookId/budget" element={<BookSubpageRedirect sub="cast" />} />
        <Route path="/dictor-pro" element={<Navigate to="/recording" replace />} />
        <Route path="/dictor-neo" element={<Navigate to="/recording" replace />} />
        <Route path="/dictor-pro/script/:chapterId" element={<Navigate to="/recording" replace />} />
        <Route path="/dictor-neo/script/:chapterId" element={<Navigate to="/recording" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}

/** Кто не готовит книгу, тот остаётся на своей поверхности: диктор и агент — в «Записи».
    Сервер их и так не пустит в неопубликованную главу; редирект нужен, чтобы вместо
    пустого экрана человек попал туда, где у него есть работа. */
export function preparationRedirect(me: MeResponse, pathname: string, search = ""): string | null {
  const { pureDictor, isAgent } = roleAccess(me);
  if (!pureDictor && !isAgent) return null;
  if (pathname === "/reader") return `/recording${search}`;

  const rolePrefix = "/reader/role/";
  if (pathname.startsWith(rolePrefix)) {
    return `/recording/role/${pathname.slice(rolePrefix.length)}${search}`;
  }

  const chapterPrefix = "/reader/";
  if (pathname.startsWith(chapterPrefix)) {
    let chapterId = "";
    try {
      chapterId = decodeURIComponent(pathname.slice(chapterPrefix.length));
    } catch {
      return "/recording";
    }
    const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
    params.set("chapter_id", chapterId);
    return `/recording?${params.toString()}`;
  }
  return "/recording";
}

function PreparationRoute({ me, children }: { me: MeResponse; children: ReactNode }) {
  const { pathname, search } = useLocation();
  const redirect = preparationRedirect(me, pathname, search);
  return redirect ? <Navigate to={redirect} replace /> : children;
}

/** Old script URLs (`/check/script/:id`, `/script/:id`, …) → the v2 reader. */
function ReaderRedirect() {
  const { chapterId = "" } = useParams();
  if (!chapterId) return <Navigate to="/reader" replace />;
  return <Navigate to={`/reader/${encodeURIComponent(chapterId)}`} replace />;
}

/** Old per-book URLs (`/budget/:id`) → the hub's sub-page. */
function BookSubpageRedirect({ sub }: { sub: "cast" }) {
  const { bookId = "" } = useParams();
  if (!bookId) return <Navigate to="/books" replace />;
  return <Navigate to={`/books/${encodeURIComponent(bookId)}/${sub}`} replace />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/*" element={<Layout />} />
    </Routes>
  );
}
