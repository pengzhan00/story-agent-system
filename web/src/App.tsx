import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useEventFeed } from "./api/events";
import { useI18n } from "./i18n";
import { ProjectPage } from "./pages/ProjectPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { PromptsPage } from "./pages/PromptsPage";
import { QueuePage } from "./pages/QueuePage";
import { SystemPage } from "./pages/SystemPage";

export function App() {
  const { t, locale, locales, setLocale } = useI18n();
  const feed = useEventFeed();
  const link = ({ isActive }: { isActive: boolean }) => (isActive ? "nav-link active" : "nav-link");
  return (
    <div className="shell">
      <header className="topbar">
        <strong className="brand">{t("web.app.name")}</strong>
        <nav className="nav">
          <NavLink to="/projects" className={link}>
            {t("web.nav.projects")}
          </NavLink>
          <NavLink to="/queue" className={link}>
            {t("web.nav.queue")}
          </NavLink>
          <NavLink to="/prompts" className={link}>
            {t("web.nav.prompts")}
          </NavLink>
          <NavLink to="/system" className={link}>
            {t("web.nav.system")}
          </NavLink>
        </nav>
        <span className="spacer" />
        <span className={`feed feed-${feed}`} title={t(`web.feed.${feed}`)}>
          {t(`web.feed.${feed}`)}
        </span>
        <select aria-label={t("web.language")} value={locale} onChange={(e) => setLocale(e.target.value)}>
          {locales.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<Navigate to="/projects" replace />} />
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/projects/:id/*" element={<ProjectPage />} />
          <Route path="/queue" element={<QueuePage />} />
          <Route path="/prompts" element={<PromptsPage />} />
          <Route path="/system" element={<SystemPage />} />
          <Route path="*" element={<p className="empty">{t("web.not_found")}</p>} />
        </Routes>
      </main>
    </div>
  );
}
