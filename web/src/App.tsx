import { useEffect, useState } from "react";
import { api } from "./api";
import { ToastProvider } from "./components/ui";
import { RatingsProvider } from "./components/DishRating";
import Icon from "./components/Icon";
import Brand from "./components/Brand";
import HomePage from "./pages/HomePage";
import HelpPage from "./pages/HelpPage";
import LibraryPage from "./pages/LibraryPage";
import NotebookPage from "./pages/NotebookPage";
import AssumptionsPage from "./pages/AssumptionsPage";
import ChatPage from "./pages/ChatPage";
import DishesPage from "./pages/DishesPage";
import GeneratePage from "./pages/GeneratePage";
import ProductsPage from "./pages/ProductsPage";
import SettingsPage from "./pages/SettingsPage";
import ShoppingPage from "./pages/ShoppingPage";
import WeekPage from "./pages/WeekPage";
import type { Status } from "./types";
import { loadLocal, saveLocal, useRoute } from "./util";

const NAV = [
  { key: "start", label: "Pulpit", icon: "home" },
  { key: "tydzien", label: "Plan tygodnia", icon: "calendar" },
  { key: "zakupy", label: "Lista zakupów", icon: "basket" },
  { key: "diety", label: "Moje diety", icon: "bookmark" },
  { key: "dania", label: "Biblioteka dań", icon: "dish" },
];

export default function App() {
  const route = useRoute();
  const page = route[0] || "start";
  const [status, setStatus] = useState<Status | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  // bez własnego wyboru motyw idzie za ustawieniem systemu (Windows: tryb ciemny aplikacji)
  const systemTheme = () => (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  // „porcja:theme” zapisywało się dawniej przy każdym starcie, więc „light” nie musiało być wyborem użytkownika
  const [chosenTheme, setChosenTheme] = useState(() => loadLocal<string | null>("porcja:motyw", null)
    ?? (loadLocal<string | null>("porcja:theme", null) === "dark" ? "dark" : null));
  const [theme, setTheme] = useState(() => chosenTheme === "dark" || chosenTheme === "light" ? chosenTheme : systemTheme());
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  useEffect(() => {
    if (chosenTheme || !window.matchMedia) return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const follow = () => setTheme(media.matches ? "dark" : "light");
    media.addEventListener?.("change", follow);
    return () => media.removeEventListener?.("change", follow);
  }, [chosenTheme]);
  const toggleTheme = () => {
    const next = theme === "light" ? "dark" : "light";
    setTheme(next);
    setChosenTheme(next);
    saveLocal("porcja:motyw", next);
  };
  useEffect(() => { setMenuOpen(false); document.querySelector(".main")?.scrollTo(0, 0); }, [page]);

  useEffect(() => {
    let alive = true;
    const load = () => api.status().then((s) => alive && setStatus(s)).catch(() => undefined);
    load();
    const t = setInterval(load, 15000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [page]);

  let content;
  switch (page) {
    case "start":
      content = <HomePage status={status} />;
      break;
    case "pomoc":
      content = <HelpPage />;
      break;
    case "diety":
      content = <LibraryPage />;
      break;
    case "nowy":
      content = <GeneratePage status={status} />;
      break;
    case "zakupy":
      content = <ShoppingPage planId={route[1]} />;
      break;
    case "czat":
      content = <ChatPage />;
      break;
    case "dania":
      content = <DishesPage dishId={route[1]} />;
      break;
    case "produkty":
      content = <ProductsPage productId={route[1]} />;
      break;
    case "zalozenia":
      content = <AssumptionsPage />;
      break;
    case "notatnik":
      content = <NotebookPage />;
      break;
    case "ustawienia":
      content = <SettingsPage status={status} onStatusChange={setStatus} />;
      break;
    default:
      content = <WeekPage planId={route[1]} />;
  }

  return (
    <ToastProvider>
      <RatingsProvider>
      <div className="app">
        <a className="skip-link" href="#main-content" onClick={e => { e.preventDefault(); document.getElementById("main-content")?.focus(); }}>Przejdź do treści</a>
        <nav className={`sidebar ${menuOpen ? "menu-open" : ""}`} aria-label="Nawigacja główna">
          <a className="brand-link" href="#/start" aria-label="Porcja — pulpit"><Brand /></a>
          <button className="btn mobile-menu" aria-expanded={menuOpen} aria-controls="navigation-links" aria-label={menuOpen ? "Zamknij menu" : "Otwórz menu"} onClick={() => setMenuOpen(v => !v)}><Icon name={menuOpen ? "close" : "menu"} /></button>
          <div className="navigation-links" id="navigation-links">
          <a href="#/nowy" className={`nav-item nav-new ${page === "nowy" ? "active" : ""}`} aria-current={page === "nowy" ? "page" : undefined}>
            <Icon name="plus" />Nowy plan
          </a>
          <div className="nav-section">Na co dzień</div>
          {NAV.map((n) => (
            <a key={n.key} href={`#/${n.key}`} className={`nav-item ${page === n.key ? "active" : ""}`} aria-current={page === n.key ? "page" : undefined}>
              <Icon name={n.icon} />
              {n.label}
            </a>
          ))}
          <div className="nav-section">Dopasuj do siebie</div>
          <a href="#/notatnik" className={`nav-item ${page === "notatnik" ? "active" : ""}`} aria-current={page === "notatnik" ? "page" : undefined}><Icon name="notebook" />Notatnik preferencji</a>
          <a href="#/zalozenia" className={`nav-item ${page === "zalozenia" ? "active" : ""}`}><Icon name="target" />Cele i preferencje</a>
          <a href="#/ustawienia" className={`nav-item ${page === "ustawienia" ? "active" : ""}`}><Icon name="settings" />Ustawienia</a>
          <details className="nav-extra" key={page === "czat" || page === "produkty" ? "extra" : "main"} open={page === "czat" || page === "produkty" ? true : undefined}>
            <summary>Więcej narzędzi</summary>
            <a href="#/czat" className={`nav-item ${page === "czat" ? "active" : ""}`}><Icon name="chat" />Asystent</a>
            <a href="#/produkty" className={`nav-item ${page === "produkty" ? "active" : ""}`}><Icon name="product" />Baza produktów</a>
          </details>
          <a href="#/pomoc" className={`nav-item nav-help ${page === "pomoc" ? "active" : ""}`}><Icon name="help" />Pomoc i pierwsze kroki</a>
          </div>
          {status && (
            <div className="sidebar-foot">
              <div className="row"><span className="live-dot" />{status.jobs_busy ? "Układamy Twój plan…" : "Dane zapisane lokalnie"}</div>
              <div>Porcja {status.version} · bezpłatna aplikacja</div>
            </div>
          )}
        </nav>
        <main className="main" id="main-content" tabIndex={-1}>
          <div className="topbar no-print"><span>Twoja przestrzeń <span className="topbar-divider">/</span> <b>{NAV.find(n => n.key === page)?.label ?? ({ nowy: "Nowy plan", notatnik: "Notatnik preferencji", zalozenia: "Cele i preferencje", ustawienia: "Ustawienia", pomoc: "Pomoc", czat: "Asystent", produkty: "Produkty" } as Record<string, string>)[page] ?? "Plan tygodnia"}</b></span>
            <div className="row"><a href="#/ustawienia" className="connection-label"><span className={`live-dot ${status?.llm.key_present && status?.llm.enabled ? "" : "neutral"}`} />{status ? status.llm.key_present && status.llm.enabled ? "AI dostępne" : "Tryb bez AI" : "Łączenie…"}</a><button className="btn icon-button" aria-label={theme === "light" ? "Włącz ciemny motyw" : "Włącz jasny motyw"} onClick={toggleTheme}><Icon name={theme === "light" ? "moon" : "sun"} /></button></div>
          </div>
          {content}
          <footer className="page-footer no-print">porcja. <span>Dobrze mieć plan.</span><a href="#/pomoc">Pomoc</a></footer>
        </main>
      </div>
      </RatingsProvider>
    </ToastProvider>
  );
}
