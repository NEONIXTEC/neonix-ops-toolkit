import { useEffect, useState } from "react";
import { ChatWidget } from "./ChatWidget";

type Theme = "light" | "dark";

function systemTheme(): Theme {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function savedTheme(): Theme | null {
  try {
    const v = localStorage.getItem("theme");
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;   // storage can be blocked (private window) - fall back to the system setting
  }
}

export function App() {
  const [theme, setTheme] = useState<Theme>(() => savedTheme() ?? systemTheme());

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("theme", theme);
    } catch {
      /* not persisted - the page still works */
    }
  }, [theme]);

  return (
    <main className="page">
      <button
        type="button"
        className="theme-toggle"
        onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        aria-label={theme === "dark" ? "라이트 모드로" : "다크 모드로"}
      >
        {theme === "dark" ? "라이트" : "다크"}
      </button>
      <ChatWidget />
    </main>
  );
}
