"use client";

import { usePathname, useRouter } from "next/navigation";

/** Broadcast so the form clears itself wherever it happens to be mounted. */
export const RESET_EVENT = "scriptcast:reset";

/**
 * Fixed side button that puts you back at an empty form.
 *
 * Deliberately non-destructive: it clears the topic and voice picker and sends
 * you home, but never touches saved runs, scripts, or audio. The API token is
 * kept too — clearing it would just mean retyping it every single time.
 */
export default function ResetButton() {
  const router = useRouter();
  const pathname = usePathname();

  const reset = () => {
    window.dispatchEvent(new CustomEvent(RESET_EVENT));
    if (pathname !== "/") router.push("/");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <button
      type="button"
      className="reset-tab"
      onClick={reset}
      title="Clear the form and start a new run. Your saved runs are kept."
      aria-label="Reset the form and start a new run"
    >
      <span className="reset-tab-text">RESET</span>
    </button>
  );
}
