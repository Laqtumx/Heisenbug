"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AlertTriangle, Database, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useSession } from "@/lib/session";

/**
 * The demo accounts, listed on the sign-in screen.
 *
 * Each one lands on a different view because the role changes what the API will
 * return. Signing in as the supplier is the quickest way to see row level
 * security at work: the same database, a fraction of the rows.
 */
const DEMO_ACCOUNTS = [
  { email: "admin@cleanslate.test", role: "Admin", note: "Everything, including rollback" },
  { email: "engineer@cleanslate.test", role: "Migration engineer", note: "Runs the pipeline, no payroll" },
  { email: "finance@cleanslate.test", role: "Finance controller", note: "Ledger, invoices, approvals" },
  { email: "inventory@cleanslate.test", role: "Inventory manager", note: "Items and stock only" },
  { email: "supplier@cleanslate.test", role: "Supplier (external)", note: "Own orders only, no finance" },
];

const DEMO_PASSWORD = "cleanslate";

export default function LoginPage() {
  const { signIn, user, ready } = useSession();
  const router = useRouter();
  const [email, setEmail] = useState("admin@cleanslate.test");
  const [password, setPassword] = useState(DEMO_PASSWORD);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (ready && user) {
      router.replace(user.role === "supplier" ? "/supplier" : "/");
    }
  }, [ready, user, router]);

  async function submit(nextEmail: string, nextPassword: string) {
    setBusy(true);
    setError(null);
    try {
      await signIn(nextEmail, nextPassword);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="relative hidden flex-col justify-between bg-primary p-10 text-primary-foreground lg:flex">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary-foreground/15">
            <Database className="h-4 w-4" aria-hidden />
          </div>
          <span className="text-sm font-semibold">CleanSlate</span>
        </div>

        <div className="space-y-5">
          <h1 className="max-w-md text-3xl font-semibold leading-tight">
            Move the data, not the mistakes.
          </h1>
          <p className="max-w-md text-sm text-primary-foreground/80">
            Legacy ERP records arrive dirty: duplicate vendors, orphaned invoices, unbalanced
            ledgers, dead stock. CleanSlate profiles, validates and repairs them, reconciles the
            totals, and only then loads the target schema.
          </p>
          <dl className="grid max-w-md grid-cols-3 gap-4 pt-4 text-sm">
            <div>
              <dt className="text-primary-foreground/70">Records in</dt>
              <dd className="tabular text-lg font-semibold">47,141</dd>
            </div>
            <div>
              <dt className="text-primary-foreground/70">Defects caught</dt>
              <dd className="tabular text-lg font-semibold">6,798</dd>
            </div>
            <div>
              <dt className="text-primary-foreground/70">Reconciled</dt>
              <dd className="tabular text-lg font-semibold">100%</dd>
            </div>
          </dl>
        </div>

        <p className="text-xs text-primary-foreground/60">
          Postgres row level security decides what each role can read. It is enforced by the
          database, not by this screen.
        </p>
      </div>

      <div className="flex items-center justify-center p-6">
        <div className="w-full max-w-md space-y-5">
          <div className="lg:hidden">
            <div className="mb-3 flex items-center gap-2">
              <Database className="h-5 w-5 text-primary" aria-hidden />
              <span className="font-semibold">CleanSlate</span>
            </div>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Sign in</CardTitle>
              <CardDescription>
                Use a demo account below, or type your own credentials.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form
                className="space-y-4"
                onSubmit={(event) => {
                  event.preventDefault();
                  void submit(email, password);
                }}
              >
                <div className="space-y-1.5">
                  <Label htmlFor="email">Email</Label>
                  <Input
                    id="email"
                    type="email"
                    autoComplete="username"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    required
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="password">Password</Label>
                  <Input
                    id="password"
                    type="password"
                    autoComplete="current-password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    required
                  />
                </div>

                {error ? (
                  <p className="flex items-start gap-2 rounded-md bg-destructive/5 px-3 py-2 text-sm text-destructive">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                    {error}
                  </p>
                ) : null}

                <Button type="submit" className="w-full" disabled={busy}>
                  {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
                  Sign in
                </Button>
              </form>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">Demo accounts</CardTitle>
              <CardDescription className="text-xs">
                Password for all of them: <span className="font-mono">{DEMO_PASSWORD}</span>
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-1.5">
              {DEMO_ACCOUNTS.map((account) => (
                <button
                  key={account.email}
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setEmail(account.email);
                    setPassword(DEMO_PASSWORD);
                    void submit(account.email, DEMO_PASSWORD);
                  }}
                  className="flex w-full items-center justify-between gap-3 rounded-md border px-3 py-2 text-left text-sm transition-colors hover:bg-accent disabled:opacity-50"
                >
                  <span className="min-w-0">
                    <span className="block font-medium">{account.role}</span>
                    <span className="block truncate text-xs text-muted-foreground">
                      {account.note}
                    </span>
                  </span>
                  <span className="shrink-0 text-xs text-muted-foreground">Sign in</span>
                </button>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
