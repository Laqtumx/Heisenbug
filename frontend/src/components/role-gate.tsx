"use client";

import { ShieldAlert } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { useSession } from "@/lib/session";
import type { Capabilities } from "@/lib/types";

/**
 * Hide a page's contents when the role lacks the capability.
 *
 * This is presentation only. The API re-checks the same permission on every
 * request, so a user who reaches the page anyway sees a refusal rather than
 * data. Gating here just avoids showing a screen that cannot load.
 */
export function RoleGate({
  capability,
  children,
}: {
  capability: keyof Capabilities;
  children: React.ReactNode;
}) {
  const { capabilities, user } = useSession();

  if (!capabilities?.[capability]) {
    return (
      <Alert variant="warning">
        <ShieldAlert className="h-4 w-4" aria-hidden />
        <AlertTitle>Not available for your role</AlertTitle>
        <AlertDescription>
          This dashboard needs a permission your role does not have
          {user ? ` (you are signed in as ${user.role.replace(/_/g, " ")})` : ""}. The API refuses
          this data for your account, so there is nothing to show.
        </AlertDescription>
      </Alert>
    );
  }

  return <>{children}</>;
}
