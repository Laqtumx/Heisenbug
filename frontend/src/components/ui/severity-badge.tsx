import { Badge } from "./badge";
import type { Severity } from "@/lib/types";

const LABEL: Record<Severity, string> = {
  blocker: "Blocker",
  warn: "Warning",
  info: "Info",
};

/**
 * Severity shown the same way everywhere, so a blocker never reads as a warning
 * because one screen chose a different word.
 */
export function SeverityBadge({ severity }: { severity: Severity | string }) {
  const key = (["blocker", "warn", "info"].includes(severity) ? severity : "info") as Severity;
  return <Badge variant={key}>{LABEL[key]}</Badge>;
}
