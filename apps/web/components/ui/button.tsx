import { ButtonHTMLAttributes } from "react";
import { classNames } from "./utils";

const styles = {
  base: "inline-flex min-h-12 items-center justify-center whitespace-nowrap rounded-[1rem] border px-5 py-3 text-[15px] font-semibold tracking-[-0.01em] shadow-[0_10px_24px_rgba(15,18,32,0.06)] transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-paper focus-visible:ring-ink disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-45",
  primary: "border-transparent bg-ink text-paper hover:-translate-y-px hover:bg-graphite active:translate-y-0 active:bg-ink",
  outline: "border-[#d7deee] bg-paper text-ink hover:-translate-y-px hover:border-[#b7c5df] hover:bg-[#f8fafc] active:translate-y-0"
};

export function Button(props: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "outline" }) {
  const { className, variant = "primary", ...rest } = props;
  return (
    <button
      className={classNames(styles.base, variant === "primary" ? styles.primary : styles.outline, className)}
      {...rest}
    />
  );
}
