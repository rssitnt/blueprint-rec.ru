import { ReactNode } from "react";
import { classNames } from "./utils";

export function Card({
  className,
  children,
  title
}: {
  className?: string;
  title?: string;
  children: ReactNode;
}) {
  return (
    <section className={classNames("rounded-2xl border border-line bg-paper shadow-card", className)}>
      {title && (
        <div className="border-b border-line px-5 py-3.5">
          <h2 className="text-[1.05rem] font-medium text-ink">{title}</h2>
        </div>
      )}
      <div className={classNames("p-5", !title && "px-5 py-4")}>{children}</div>
    </section>
  );
}
