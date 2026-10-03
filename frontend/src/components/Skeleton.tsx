type SkeletonProps = { label?: string; lines?: number };

function SkeletonBody({ lines }: { lines: number }) {
  return (
    <>
      <div className="skeleton skeleton-title" />
      {Array.from({ length: Math.max(1, lines) }).map((_, idx) => (
        <div key={idx} className="skeleton skeleton-line" style={{ width: `${92 - idx * 14}%` }} />
      ))}
    </>
  );
}

/** Full-page loading placeholder (shimmer) for top-level route loads. */
export function SkeletonScreen({ label = "Загрузка…", lines = 4 }: SkeletonProps) {
  return (
    <div className="shell" aria-busy="true" aria-live="polite">
      <div className="panel skeleton-panel">
        <SkeletonBody lines={lines} />
        <span className="skeleton-label">{label}</span>
      </div>
    </div>
  );
}

/** Inline (panel-sized) loading placeholder for in-page transitions. */
export function SkeletonPanel({ label = "Загрузка…", lines = 3 }: SkeletonProps) {
  return (
    <div className="panel skeleton-panel" aria-busy="true" aria-live="polite">
      <SkeletonBody lines={lines} />
      <span className="skeleton-label">{label}</span>
    </div>
  );
}
