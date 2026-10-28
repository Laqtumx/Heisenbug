"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo } from "react";
import {
  Activity,
  ClipboardCheck,
  Database,
  FileBarChart,
  Gauge,
  LogOut,
  Package,
  ScrollText,
  ShieldCheck,
  Sparkles,
  Truck,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { useSession } from "@/lib/session";
import { humanize } from "@/lib/utils";
import type { Capabilities } from "@/lib/types";

interface NavItem {
  href: string;
  label: string;
  icon: typeof Gauge;
  /** The capability that reveals this item. The server enforces the same rule. */
  capability?: keyof Capabilities;
  /** Shown only to these roles when no single capability covers it. */
  roles?: string[];
}

const NAV: { section: string; items: NavItem[] }[] = [
  {
    section: "Migration",
    items: [
      { href: "/", label: "Overview", icon: Gauge },
      { href: "/batches", label: "Migration batches", icon: Database },
      { href: "/review", label: "Review queue", icon: ClipboardCheck, capability: "can_review" },
    ],
  },
  {
    section: "Quality",
    items: [
      { href: "/quality", label: "Data quality", icon: Activity, capability: "can_validate" },
      { href: "/reconciliation", label: "Reconciliation", icon: FileBarChart },
    ],
  },
  {
    section: "Insight",
    items: [
      { href: "/inventory", label: "Inventory insights", icon: Package, capability: "can_view_inventory" },
      { href: "/finance", label: "Finance controls", icon: Sparkles, capability: "can_view_finance" },
    ],
  },
  {
    section: "Governance",
    items: [
      { href: "/audit", label: "Audit log", icon: ScrollText, capability: "can_view_audit" },
      { href: "/security", label: "Access matrix", icon: ShieldCheck, capability: "can_view_audit" },
    ],
  },
  {
    section: "Supplier",
    items: [
      { href: "/supplier", label: "My orders", icon: Truck, roles: ["supplier"] },
    ],
  },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, capabilities, ready, signOut } = useSession();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (ready && !user) router.replace("/login");
  }, [ready, user, router]);

  // A supplier sees a single-purpose portal, so the migration nav is not just
  // hidden from them, it is absent. The API refuses those routes regardless.
  const isExternal = capabilities?.is_external ?? user?.role === "supplier";

  const sections = useMemo(() => {
    return NAV.map((section) => ({
      ...section,
      items: section.items.filter((item) => {
        if (item.roles && !item.roles.includes(user?.role ?? "")) return false;
        if (isExternal && !item.roles?.includes("supplier")) return false;
        if (item.capability && !capabilities?.[item.capability]) return false;
        return true;
      }),
    })).filter((section) => section.items.length > 0);
  }, [capabilities, isExternal, user?.role]);

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
        Loading…
      </div>
    );
  }

  if (!user) return null;

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-64 shrink-0 flex-col border-r bg-card lg:flex">
        <div className="flex h-14 items-center gap-2 border-b px-4">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Database className="h-4 w-4" aria-hidden />
          </div>
          <div className="leading-tight">
            <p className="text-sm font-semibold">CleanSlate</p>
            <p className="text-[11px] text-muted-foreground">Move the data, not the mistakes</p>
          </div>
        </div>

        <nav className="flex-1 space-y-5 overflow-y-auto p-3 scrollbar-thin">
          {sections.map((section) => (
            <div key={section.section}>
              <p className="px-2 pb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                {section.section}
              </p>
              <ul className="space-y-0.5">
                {section.items.map((item) => {
                  const active =
                    item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
                  const Icon = item.icon;
                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        className={`flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors ${
                          active
                            ? "bg-accent font-medium text-accent-foreground"
                            : "text-muted-foreground hover:bg-accent/60 hover:text-foreground"
                        }`}
                      >
                        <Icon className="h-4 w-4 shrink-0" aria-hidden />
                        {item.label}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>

        <Separator />
        <div className="space-y-2 p-3">
          <div className="px-1">
            <p className="truncate text-sm font-medium">{user.full_name}</p>
            <p className="truncate text-xs text-muted-foreground">{user.email}</p>
          </div>
          <Badge variant="secondary" className="ml-1">
            {humanize(user.role)}
          </Badge>
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={() => {
              void signOut().then(() => router.replace("/login"));
            }}
          >
            <LogOut className="h-3.5 w-3.5" aria-hidden />
            Sign out
          </Button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between border-b bg-card px-4 lg:px-6">
          <div className="flex items-center gap-3">
            <Database className="h-4 w-4 lg:hidden" aria-hidden />
            <span className="text-sm font-medium">
              {sections
                .flatMap((s) => s.items)
                .find((item) =>
                  item.href === "/" ? pathname === "/" : pathname.startsWith(item.href),
                )?.label ?? "CleanSlate"}
            </span>
          </div>
          <div className="flex items-center gap-2">
            {isExternal ? <Badge variant="outline">External supplier</Badge> : null}
            <Badge variant="secondary" className="lg:hidden">
              {humanize(user.role)}
            </Badge>
          </div>
        </header>
        <main className="flex-1 overflow-x-hidden p-4 lg:p-6">{children}</main>
      </div>
    </div>
  );
}
