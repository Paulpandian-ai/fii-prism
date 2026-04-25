"use client";

import { Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function SearchBar({ size = "lg" }: { size?: "md" | "lg" }) {
  const router = useRouter();
  const [value, setValue] = useState("");

  function go(symbol: string) {
    const s = symbol.trim().toUpperCase();
    if (!s) return;
    router.push(`/stock/?s=${encodeURIComponent(s)}`);
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        go(value);
      }}
      className={size === "lg" ? "flex w-full max-w-xl items-center gap-2" : "flex items-center gap-2"}
    >
      <div className="relative flex-1">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fii-mute" />
        <Input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Analyze any US stock — try AAPL"
          className={size === "lg" ? "h-12 pl-10 text-base" : "pl-9"}
          autoFocus={size === "lg"}
        />
      </div>
      <Button type="submit" size={size === "lg" ? "lg" : "md"}>
        Open
      </Button>
    </form>
  );
}
