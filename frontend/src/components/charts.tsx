"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { SEVERITY_ORDER } from "@/lib/utils";

const SEVERITY_FILL: Record<string, string> = {
  blocker: "hsl(var(--severity-blocker))",
  warn: "hsl(var(--severity-warn))",
  info: "hsl(var(--severity-info))",
};

/** Findings by severity, ordered blocker first so the eye lands on the worst. */
export function SeverityChart({ data }: { data: Record<string, number> }) {
  const rows = Object.entries(data)
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => (SEVERITY_ORDER[a.name] ?? 9) - (SEVERITY_ORDER[b.name] ?? 9));

  if (!rows.length) return null;

  return (
    <ResponsiveContainer width="100%" height={240}>
      <PieChart>
        <Pie
          data={rows}
          dataKey="value"
          nameKey="name"
          innerRadius={54}
          outerRadius={82}
          paddingAngle={2}
          strokeWidth={0}
        >
          {rows.map((row) => (
            <Cell key={row.name} fill={SEVERITY_FILL[row.name] ?? "hsl(var(--muted))"} />
          ))}
        </Pie>
        <Tooltip
          contentStyle={{
            background: "hsl(var(--card))",
            border: "1px solid hsl(var(--border))",
            borderRadius: 8,
            fontSize: 12,
          }}
        />
        <Legend
          verticalAlign="bottom"
          iconType="circle"
          formatter={(value) => <span className="text-xs capitalize">{value}</span>}
        />
      </PieChart>
    </ResponsiveContainer>
  );
}

/** Findings by entity, worst first. */
export function EntityChart({ data }: { data: Record<string, number> }) {
  const rows = Object.entries(data)
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 10);

  if (!rows.length) return null;

  return (
    <ResponsiveContainer width="100%" height={Math.max(220, rows.length * 30)}>
      <BarChart data={rows} layout="vertical" margin={{ left: 8, right: 24 }}>
        <CartesianGrid horizontal={false} stroke="hsl(var(--border))" />
        <XAxis type="number" tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
        <YAxis
          type="category"
          dataKey="name"
          width={120}
          tick={{ fontSize: 11 }}
          stroke="hsl(var(--muted-foreground))"
        />
        <Tooltip
          contentStyle={{
            background: "hsl(var(--card))",
            border: "1px solid hsl(var(--border))",
            borderRadius: 8,
            fontSize: 12,
          }}
        />
        <Bar dataKey="value" fill="hsl(var(--primary))" radius={[0, 4, 4, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/**
 * The before/after comparison the whole product rests on.
 *
 * The two series are the same measures taken at two points, so they share one
 * axis. Separate charts would let the scale flatter the improvement.
 */
export function BeforeAfterChart({
  before,
  after,
}: {
  before: number | null;
  after: number | null;
}) {
  const rows = [
    { name: "Health score", before: before ?? 0, after: after ?? 0 },
  ];

  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={rows} margin={{ left: 8, right: 8 }}>
        <CartesianGrid vertical={false} stroke="hsl(var(--border))" />
        <XAxis dataKey="name" tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
        <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
        <Tooltip
          contentStyle={{
            background: "hsl(var(--card))",
            border: "1px solid hsl(var(--border))",
            borderRadius: 8,
            fontSize: 12,
          }}
        />
        <Legend iconType="circle" formatter={(v) => <span className="text-xs capitalize">{v}</span>} />
        <Bar dataKey="before" name="Before cleaning" fill="hsl(var(--severity-blocker))" radius={[4, 4, 0, 0]} />
        <Bar dataKey="after" name="After cleaning" fill="hsl(var(--severity-ok))" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/**
 * Records in against records that reached the target.
 *
 * The reconciliation payload mixes counts with flags such as fully_accounted,
 * so anything that is not a number is skipped rather than coerced into a bar.
 */
export function FlowChart({ counts }: { counts: Record<string, number | boolean> }) {
  const order = [
    ["records_in", "In"],
    ["records_loaded", "Loaded"],
    ["records_archived", "Archived"],
    ["records_rejected", "Rejected"],
    ["records_held_back", "Held back"],
  ] as const;

  const rows = order
    .filter(([key]) => typeof counts[key] === "number")
    .map(([key, label]) => ({ name: label, value: counts[key] as number }));

  if (!rows.length) return null;

  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={rows} margin={{ left: 8, right: 8 }}>
        <CartesianGrid vertical={false} stroke="hsl(var(--border))" />
        <XAxis dataKey="name" tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
        <YAxis tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
        <Tooltip
          contentStyle={{
            background: "hsl(var(--card))",
            border: "1px solid hsl(var(--border))",
            borderRadius: 8,
            fontSize: 12,
          }}
        />
        <Bar dataKey="value" fill="hsl(var(--primary))" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
