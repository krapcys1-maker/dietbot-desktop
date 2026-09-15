import { useEffect, useState } from "react";
import { api } from "./api";
import { ToastProvider } from "./components/ui";
import AssumptionsPage from "./pages/AssumptionsPage";
import ChatPage from "./pages/ChatPage";
import DishesPage from "./pages/DishesPage";
import GeneratePage from "./pages/GeneratePage";
import ProductsPage from "./pages/ProductsPage";
import SettingsPage from "./pages/SettingsPage";
import ShoppingPage from "./pages/ShoppingPage";
import WeekPage from "./pages/WeekPage";
import type { Status } from "./types";
import { plural, useRoute, usd } from "./util";

const NAV = [
  { key: "tydzien", label: "Tydzień", icon: "📅" },
  { key: "nowy", label: "Nowy plan", icon: "✨" },
  { key: "zakupy", label: "Zakupy", icon: "🛒" },
  { key: "czat", label: "Czat", icon: "💬" },
  { key: "dania", label: "Dania", icon: "🍲" },
  { key: "produkty", label: "Produkty", icon: "🥕" },
  { key: "zalozenia", label: "Założenia", icon: "🎯" },
  { key: "ustawienia", label: "Ustawienia", icon: "⚙️" },
];

export default function App() {
  const route = useRoute();
  const page = route[0] || "tydzien";
  const [status, setStatus] = useState<Status | null>(null);

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
    case "ustawienia":
      content = <SettingsPage status={status} />;
      break;
    default:
      content = <WeekPage planId={route[1]} />;
  }

  return (
    <ToastProvider>
      <div className="app">
        <nav className="sidebar">
          <div className="brand">
            <span className="brand-logo">🥗</span>
            <div>
              <div className="brand-name">DietBot</div>
              <div className="brand-sub">jadłospis na tydzień</div>
            </div>
          </div>
          {NAV.map((n) => (
            <a key={n.key} href={`#/${n.key}`} className={`nav-item ${page === n.key ? "active" : ""}`}>
              <span className="nav-icon">{n.icon}</span>
              {n.label}
            </a>
          ))}
          {status && (
            <div className="sidebar-foot">
              <div>
                {status.approved_dishes} {plural(status.approved_dishes, "danie", "dania", "dań")} · {status.products} {plural(status.products, "produkt", "produkty", "produktów")}
              </div>
              <div title="Wydatki na model językowy">
                LLM: {usd(status.llm.spent_usd)} / {usd(status.llm.budget_usd)}
              </div>
              {status.jobs_busy && <div>⏳ trwa liczenie…</div>}
            </div>
          )}
        </nav>
        <main className="main">{content}</main>
      </div>
    </ToastProvider>
  );
}
