import { useEffect, useState } from "react";
import { fetchFile, saveBlob } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Icon } from "@/components/ui/Icon";

/**
 * Shows an uploaded document in a dialog. The file is fetched with the
 * bearer token and shown from a blob: URL.
 *
 * Only PDFs and images are rendered, and the blob is re-typed to that type:
 * a blob: URL runs with this page's origin, so anything else (say HTML that
 * slipped past the upload check) is offered as a download, never rendered.
 */

const PREVIEWABLE = ["application/pdf", "image/jpeg", "image/png"];

export function DocumentPreview({ path, name, onClose }: {
  /** The document endpoint under /api/v1; null = closed. */
  path: string | null;
  name: string;
  onClose: () => void;
}) {
  const [file, setFile] = useState<{ blob: Blob; url: string | null; name: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!path) return;
    let url: string | null = null;
    let cancelled = false;
    setFile(null);
    setError(null);
    fetchFile(path).then(
      ({ blob, name: served }) => {
        if (cancelled) return;
        const type = blob.type.split(";")[0].trim();
        url = PREVIEWABLE.includes(type) ? URL.createObjectURL(new Blob([blob], { type })) : null;
        setFile({ blob, url, name: served ?? name });
      },
      (e: unknown) => !cancelled && setError(e instanceof Error ? e.message : "Could not open the document"),
    );
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [path, name]);

  const isPdf = file?.blob.type.startsWith("application/pdf");

  return (
    <Dialog open={path !== null} onClose={onClose} title={file?.name ?? name} large>
      {error ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : !file ? (
        <p className="py-16 text-center text-sm text-muted-foreground">Loading document…</p>
      ) : file.url ? (
        isPdf ? (
          <iframe src={file.url} title={file.name} className="h-[70vh] w-full rounded-xl border" />
        ) : (
          <div className="flex max-h-[70vh] justify-center overflow-auto rounded-xl border bg-muted/40 p-2">
            <img src={file.url} alt={file.name} className="h-auto max-w-full object-contain" />
          </div>
        )
      ) : (
        <p className="text-sm text-muted-foreground">This file type can't be previewed. Download it to open it.</p>
      )}
      {file && (
        <div className="flex justify-end">
          <Button variant="outline" size="sm" className="rounded-lg" onClick={() => saveBlob(file.blob, file.name)}>
            <Icon name="download" /> Download
          </Button>
        </div>
      )}
    </Dialog>
  );
}
