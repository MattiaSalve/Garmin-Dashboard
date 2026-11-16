import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "./cn";

export const Dialog = D.Root;

export function DialogContent({ title, description, children, className }: { title: string; description?: string; children: ReactNode; className?: string }) {
  return (
    <D.Portal>
      <D.Overlay className="fixed inset-0 z-40 bg-black/50" />
      <D.Content
        className={cn(
          "fixed left-1/2 top-1/2 z-50 flex max-h-[88dvh] w-[min(640px,calc(100vw-24px))] -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-border bg-card text-card-foreground shadow-xl",
          className,
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div className="min-w-0">
            <D.Title className="m-0 text-lg font-semibold">{title}</D.Title>
            <D.Description className={description ? "muted m-0" : "sr-only"}>{description ?? title}</D.Description>
          </div>
          <D.Close aria-label="Close" className="shrink-0 cursor-pointer rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground">
            <X size={18} />
          </D.Close>
        </div>
        <div className="overflow-y-auto px-5 py-4">{children}</div>
      </D.Content>
    </D.Portal>
  );
}
