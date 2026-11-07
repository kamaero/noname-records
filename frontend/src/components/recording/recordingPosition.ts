/** Позиция привязана к учётке: на общем компьютере дикторы не продолжают чужое чтение. */
export type ReadingAnchor = { segmentId: string; fraction: number };
export type RecordingPosition = {
  bookId: string;
  chapterId: string;
  role: string;
  anchor?: ReadingAnchor;
};

const key = (uid: string) => `recording.position.v1:${uid}`;

export function readRecordingPosition(uid: string): RecordingPosition | null {
  if (!uid) return null;
  try {
    const value = JSON.parse(localStorage.getItem(key(uid)) || "null");
    if (!value || typeof value.bookId !== "string" || !value.bookId ||
        typeof value.chapterId !== "string" || !value.chapterId || typeof value.role !== "string") return null;
    const anchor = value.anchor;
    return {
      bookId: value.bookId, chapterId: value.chapterId, role: value.role,
      ...(anchor && typeof anchor.segmentId === "string" && anchor.segmentId &&
          Number.isFinite(anchor.fraction) && anchor.fraction >= 0 && anchor.fraction <= 1
        ? { anchor: { segmentId: anchor.segmentId, fraction: anchor.fraction } } : {}),
    };
  } catch {
    return null;
  }
}

export function writeRecordingPosition(uid: string, position: RecordingPosition): void {
  if (!uid) return;
  try {
    localStorage.setItem(key(uid), JSON.stringify(position));
  } catch {
    // Закрытый storage не должен мешать читать сценарий.
  }
}
