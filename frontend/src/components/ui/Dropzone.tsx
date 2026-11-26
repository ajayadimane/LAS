import { useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { Icon } from "./Icon";

interface DropzoneProps {
  onFile: (file: File) => void;
  busy?: boolean;
  disabled?: boolean;
  /** MIME types the server accepts (also checked here, before uploading). */
  types?: string[];
  formats?: string;
  maxBytes?: number;
  title?: string;
  /** A server-side failure to show under the drop area. */
  error?: string | null;
}

const MB = 1024 * 1024;

/**
 * Drag-and-drop area, also clickable and keyboard-operable as a whole. One file
 * at a time. The type and size are checked before anything is sent, so a wrong
 * file fails at once with a reason instead of after the upload.
 */
export function Dropzone({
  onFile, busy, disabled, types = ["application/pdf", "image/jpeg", "image/png"], formats = "PDF, JPEG or PNG",
  maxBytes = 2 * MB, title = "Drag & drop the document here", error,
}: DropzoneProps) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const off = disabled || busy;
  const maxLabel = `${Math.round(maxBytes / MB)} MB`;

  const take = (file: File | undefined) => {
    if (input.current) input.current.value = "";
    if (!file || off) return;
    if (!types.includes(file.type)) {
      setProblem(`“${file.name}” is not a ${formats} file.`);
    } else if (file.size > maxBytes) {
      setProblem(`“${file.name}” is ${(file.size / MB).toFixed(1)} MB. The limit is ${maxLabel}; compress it or upload a smaller scan.`);
    } else {
      setProblem(null);
      setPicked(file.name);
      onFile(file);
    }
  };

  const shown = problem ?? error ?? null;

  return (
    <div>
      <div
        role="button" tabIndex={off ? -1 : 0} aria-disabled={off || undefined}
        aria-label={`${title}. Or press to choose a file. ${formats}, up to ${maxLabel}.`}
        onClick={() => !off && input.current?.click()}
        onKeyDown={(e) => {
          if (!off && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); input.current?.click(); }
        }}
        onDragOver={(e) => { e.preventDefault(); if (!off) setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); take(e.dataTransfer.files?.[0]); }}
        className={cn(
          "group flex cursor-pointer flex-col items-center rounded-2xl border-2 border-dashed px-5 py-7 text-center outline-none transition-colors",
          "focus-visible:ring-2 focus-visible:ring-primary/40",
          over ? "border-primary bg-accent" : shown ? "border-destructive/50 bg-destructive/5" : "border-primary/40 bg-accent/40 hover:border-primary hover:bg-accent/70",
          off && "cursor-default opacity-70",
        )}
      >
        {busy ? (
          <>
            <span className="h-10 w-10 animate-spin rounded-full border-[3px] border-primary/25 border-t-primary" aria-hidden="true" />
            <p className="mt-3 font-bold">Uploading…</p>
            {picked && <p className="mt-1 max-w-full truncate text-sm text-muted-foreground">{picked}</p>}
          </>
        ) : (
          <>
            <span className="flex h-12 w-12 items-center justify-center rounded-full border border-primary/20 bg-success/60 text-accent-foreground transition-transform group-hover:-translate-y-0.5">
              <Icon name="cloudupload" className="h-6 w-6" />
            </span>
            <p className="mt-3 font-bold">{over ? "Drop to upload" : title}</p>
            <p className="mt-1 text-sm text-muted-foreground">
              or <span className="font-semibold text-primary underline-offset-2 group-hover:underline">click to choose a file</span>
            </p>
            <p className="mt-3 text-xs text-muted-foreground">{formats} · up to {maxLabel}</p>
          </>
        )}
        <input ref={input} type="file" accept={types.join(",")} className="hidden" onChange={(e) => take(e.target.files?.[0])} />
      </div>
      {shown && (
        <p role="alert" className="mt-2 flex items-start gap-1.5 text-xs text-destructive">
          <Icon name="alertcircle" className="mt-px h-3.5 w-3.5 shrink-0" /> {shown}
        </p>
      )}
    </div>
  );
}
