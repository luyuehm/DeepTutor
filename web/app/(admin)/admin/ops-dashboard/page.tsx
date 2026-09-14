"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { fetchAuthStatus } from "@/lib/auth";
import { OpsDashboardFeature } from "@/features/ops-dashboard";

export default function OpsDashboardPage() {
  const router = useRouter();
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    fetchAuthStatus().then((status) => {
      if (!status?.authenticated) {
        router.replace("/login");
        return;
      }
      if (status.role !== "admin") {
        router.replace("/");
        return;
      }
      setChecking(false);
    });
  }, [router]);

  if (checking) {
    return (
      <div className="h-screen bg-[#080B14]" aria-hidden>
        <div className="flex h-full items-center justify-center">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-[rgba(0,229,255,0.3)] border-t-[#00E5FF]" />
        </div>
      </div>
    );
  }

  return <OpsDashboardFeature />;
}
