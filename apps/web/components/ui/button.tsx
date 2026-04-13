import { ButtonHTMLAttributes } from "react";
import { classNames } from "./utils";

const styles = {
  base: "inline-flex min-h-12 items-center justify-center whitespace-nowrap rounded-[1rem] border px-5 py-3 text-[15px] font-semibold tracking-[-0.01em] transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-paper focus-visible:ring-ink disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-45",
  primary: "border-transparent bg-[#2b221d] text-[#fff4ea] active:bg-[#2b221d]",
  outline: "border-transparent bg-[#1d1713] text-[#dccfc2] active:bg-[#1d1713]"
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
