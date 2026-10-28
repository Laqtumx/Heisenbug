import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        outline: "text-foreground",
        blocker:
          "border-transparent bg-severity-blocker/12 text-severity-blocker ring-1 ring-inset ring-severity-blocker/25",
        warn: "border-transparent bg-severity-warn/12 text-severity-warn ring-1 ring-inset ring-severity-warn/25",
        info: "border-transparent bg-severity-info/12 text-severity-info ring-1 ring-inset ring-severity-info/25",
        ok: "border-transparent bg-severity-ok/12 text-severity-ok ring-1 ring-inset ring-severity-ok/25",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
