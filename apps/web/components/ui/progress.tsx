export function ProgressRing({
  value
}: {
  value: number;
}) {
  const clamped = Math.max(0, Math.min(100, value));

  return (
    <div className="h-2.5 w-full rounded-full bg-line">
      <div
        className="h-2.5 rounded-full bg-ink transition-[width] duration-500"
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}
