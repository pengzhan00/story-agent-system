import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useEventFeed } from "./api/events";
import { useI18n } from "./i18n";
import { LAUNCH_SEEN, LaunchPage } from "./pages/LaunchPage";
import { LibraryPage } from "./pages/LibraryPage";
import { ProjectPage } from "./pages/ProjectPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { PromptsPage } from "./pages/PromptsPage";
import { WorkshopPage } from "./pages/WorkshopPage";
import { QueuePage } from "./pages/QueuePage";
import { SystemPage } from "./pages/SystemPage";

/** The first visit of a browser session lands on the launch screen; later visits go straight to the projects. */
function Home() {
  let seen = false;
  try {
    seen = sessionStorage.getItem(LAUNCH_SEEN) === "1";
  } catch {
    seen = false;
  }
  return <Navigate to={seen ? "/projects" : "/launch"} replace />;
}

export function App() {
  const { t, locale, locales, setLocale } = useI18n();
  const feed = useEventFeed();
  const link = ({ isActive }: { isActive: boolean }) => (isActive ? "nav-link active" : "nav-link");
  return (
    <div className="shell">
      <header className="topbar">
        <strong className="brand">{t("web.app.name")}</strong>
        <nav className="nav">
          <NavLink to="/launch" className={link}>
            {t("web.nav.launch")}
          </NavLink>
          <NavLink to="/projects" className={link}>
            {t("web.nav.projects")}
          </NavLink>
          <NavLink to="/workshop" className={link}>
            {t("web.nav.workshop")}
          </NavLink>
          <NavLink to="/library" className={link}>
            {t("web.nav.library")}
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
          <Route path="/" element={<Home />} />
          <Route path="/launch" element={<LaunchPage />} />
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/projects/:id/*" element={<ProjectPage />} />
          <Route path="/workshop" element={<WorkshopPage />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/queue" element={<QueuePage />} />
          <Route path="/prompts" element={<PromptsPage />} />
          <Route path="/system" element={<SystemPage />} />
          <Route path="*" element={<p className="empty">{t("web.not_found")}</p>} />
        </Routes>
      </main>
    </div>
  );
}
