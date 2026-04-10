import { classNames } from "./utils";

const palette: Record<string, string> = {
  draft: "bg-slate-100 text-slate-800 border-slate-200",
  ready: "bg-emerald-100 text-emerald-800 border-emerald-200",
  ai_detected: "bg-[#e6efff] text-[#153a8a] border-[#bfd1ff]",
  ai_review: "bg-[#fff1d8] text-[#8b5b00] border-[#f7d88f]",
  human_confirmed: "bg-[#dff7ea] text-[#145c3c] border-[#9fdfba]",
  human_corrected: "bg-[#e9efff] text-[#244bb1] border-[#bacbff]",
  rejected: "bg-[#ffe3e3] text-[#8f1f1f] border-[#f4b6b6]",
  default: "bg-gray-100 text-gray-800 border-gray-200"
};

export function Badge({ value }: { value: string }) {
  return (
    <span
      className={classNames(
        "inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold uppercase tracking-wide",
        palette[value] ?? palette.default
      )}
    >
      {value.replace(/_/g, " ")}
    </span>
  );
}
