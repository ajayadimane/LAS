import qrcode from "qrcode-generator";
import { useMemo } from "react";

/** Renders `text` (a upi://pay link) as an SVG QR code. */
export function UpiQr({ text, size = 200 }: { text: string; size?: number }) {
  const { count, cells } = useMemo(() => {
    const qr = qrcode(0, "M");
    qr.addData(text);
    qr.make();
    const n = qr.getModuleCount();
    const dark: Array<[number, number]> = [];
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (qr.isDark(r, c)) dark.push([r, c]);
    return { count: n, cells: dark };
  }, [text]);

  return (
    <svg viewBox={`-2 -2 ${count + 4} ${count + 4}`} width={size} height={size} role="img"
      aria-label="UPI payment QR code" className="rounded-xl bg-white">
      <rect x="-2" y="-2" width={count + 4} height={count + 4} fill="#fff" />
      <g fill="#0f172a">
        {cells.map(([r, c]) => <rect key={r * count + c} x={c} y={r} width="1.02" height="1.02" />)}
      </g>
    </svg>
  );
}

export const upiLink = (upiId: string, amount: number, note: string) =>
  `upi://pay?pa=${upiId}&pn=LAS%20Services&am=${amount.toFixed(2)}&cu=INR&tn=${encodeURIComponent(note)}`;
