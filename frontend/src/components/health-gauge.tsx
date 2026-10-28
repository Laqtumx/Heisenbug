"use client";

import { cn, scoreBand } from "@/lib/utils";

/**
 * The migration health score as a semicircular gauge.
 *
 * Drawn with a stroked arc rather than a chart library: a single value does not
 * need a charting dependency, and an arc renders the same on the server.
 */
export function HealthGauge({
  score,
  before,
  size = 220,
  className,
}: {
  score: number | null;
  before?: number | null;
  size?: number;
  className?: string;
}) {
  const value = score ?? 0;
  const band = scoreBand(score);
  const radius = 80;
  const circumference = Math.PI * radius; // half circle
  const offset = circumference * (1 - Math.max(0, Math.min(value, 100)) / 100);

  const toneVar =
    band.tone === "ok"
      ? "var(--severity-ok)"
      : band.tone === "warn"
        ? "var(--severity-warn)"
        : "var(--severity-blocker)";

  return (
    <div className={cn("flex flex-col items-center", className)}>
      <svg
        width={size}
        height={size * 0.62}
        viewBox="0 0 200 118"
        role="img"
        aria-label={`Migration health score ${score ?? "unknown"} out of 100`}
      >
        <path
          d="M 20 100 A 80 80 0 0 1 180 100"
          fill="none"
          stroke="hsl(var(--muted))"
          strokeWidth={14}
          strokeLinecap="round"
        />
        <path
          d="M 20 100 A 80 80 0 0 1 180 100"
          fill="none"
          stroke={`hsl(${toneVar})`}
          strokeWidth={14}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          style={{ transition: "stroke-dashoffset 700ms ease-out" }}
        />
        <text
          x="100"
          y="82"
          textAnchor="middle"
          className="fill-foreground"
          style={{ fontSize: 34, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}
        >
          {score === null || score === undefined ? "–" : value.toFixed(1)}
        </text>
        <text
          x="100"
          y="102"
          textAnchor="middle"
          className="fill-muted-foreground"
          style={{ fontSize: 11 }}
        >
          out of 100
        </text>
      </svg>

      <div className="mt-1 flex items-center gap-2 text-sm">
        <span className="font-medium">{band.label}</span>
        {before !== null && before !== undefined ? (
          <span className="text-muted-foreground tabular">
            from {before.toFixed(1)} before cleaning
          </span>
        ) : null}
      </div>
    </div>
  );
}
