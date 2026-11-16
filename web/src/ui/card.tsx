import type { HTMLAttributes } from "react";
import { cn } from "./cn";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("mb-4 rounded-xl border border-border bg-card p-5 text-card-foreground shadow-sm max-sm:p-4", className)} {...props} />;
}
