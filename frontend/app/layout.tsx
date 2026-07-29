import type { Metadata } from "next";
import Link from "next/link";
import ResetButton from "@/components/ResetButton";
import "./globals.css";

export const metadata: Metadata = {
  title: "ScriptCast",
  description: "Topic to narrated audio, one pipeline run at a time.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ResetButton />
        <main>
          <h1>
            <Link href="/">ScriptCast</Link>
          </h1>
          <p className="sub">Topic in, narrated audio and upload metadata out.</p>
          {children}
        </main>
      </body>
    </html>
  );
}
