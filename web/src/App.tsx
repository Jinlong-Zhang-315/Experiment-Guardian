import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import { Bot, FlaskConical, LogOut, Search, Settings, ShieldCheck, UploadCloud } from "lucide-react";
import { api, setActiveSession } from "./api";
import { ErrorNotice } from "./components";
import { ExperimentPage } from "./pages/ExperimentPage";
import { PlanPage } from "./pages/PlanPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SubmissionPage } from "./pages/SubmissionPage";
import { AgentPage } from "./pages/AgentPage";
import type { ProjectList, Session } from "./types";

function LoginView() {
  const location = useLocation();
  const returnTo = `${location.pathname}${location.search}`;
  return (
    <main className="login-view">
      <div className="login-brand"><ShieldCheck aria-hidden /><span>Experiment Guardian</span></div>
      <h1>实验治理工作台</h1>
      <p>提高实验一致性、可追溯性和风险可见性。</p>
      <a className="button primary" href={`/api/v1/auth/login?return_to=${encodeURIComponent(returnTo)}`}>使用团队身份登录</a>
    </main>
  );
}

function ProjectRedirect({ projects }: { projects: ProjectList }) {
  if (!projects.items.length) return <div className="empty-state">当前团队没有可用项目</div>;
  return <Navigate to={`/projects/${projects.items[0].project_id}/settings`} replace />;
}

function Shell({ session, projects }: { session: Session; projects: ProjectList }) {
  const navigate = useNavigate();
  const { projectId } = useParams();
  const selected = projects.items.find((item) => item.project_id === projectId) ?? projects.items[0];
  const base = selected ? `/projects/${selected.project_id}` : "/";
  async function logout() {
    const result = await api<{ logout_url: string }>("/auth/logout", { method: "POST" });
    setActiveSession(null);
    location.assign(result.logout_url);
  }
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><ShieldCheck aria-hidden /><span>Experiment Guardian</span></div>
        <label className="project-picker-label" htmlFor="project-picker">项目</label>
        <select id="project-picker" value={selected?.project_id ?? ""} onChange={(event) => navigate(`/projects/${event.target.value}/settings`)}>
          {projects.items.map((project) => <option value={project.project_id} key={project.project_id}>{project.name}</option>)}
        </select>
        <nav aria-label="项目导航">
          <NavLink to={`${base}/settings`}><Settings aria-hidden />项目设置</NavLink>
          <NavLink to={`${base}/plans`}><FlaskConical aria-hidden />计划审批</NavLink>
          <NavLink to={`${base}/submissions`}><UploadCloud aria-hidden />实验审核</NavLink>
          <NavLink to={`${base}/experiments`}><Search aria-hidden />实验查询</NavLink>
          {session.agent_enabled && <NavLink to={`${base}/agent`}><Bot aria-hidden />治理 Agent</NavLink>}
        </nav>
        <div className="session-block">
          <strong>{session.name}</strong><span>{session.role}</span><span>{session.email}</span>
          <button className="icon-text-button" onClick={logout}><LogOut aria-hidden />退出</button>
        </div>
      </aside>
      <div className="workspace">
        <Routes>
          <Route path="/projects/:projectId/settings" element={<SettingsPage session={session} />} />
          <Route path="/projects/:projectId/plans" element={<PlanPage />} />
          <Route path="/projects/:projectId/submissions" element={<SubmissionPage />} />
          <Route path="/projects/:projectId/experiments" element={<ExperimentPage />} />
          <Route path="/projects/:projectId/agent" element={<AgentPage />} />
          <Route path="*" element={<ProjectRedirect projects={projects} />} />
        </Routes>
      </div>
    </div>
  );
}

export function App() {
  const sessionQuery = useQuery({ queryKey: ["session"], queryFn: () => api<Session>("/auth/me"), retry: false });
  useEffect(() => setActiveSession(sessionQuery.data ?? null), [sessionQuery.data]);
  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: () => api<ProjectList>("/projects"),
    enabled: Boolean(sessionQuery.data),
  });
  if (sessionQuery.isPending) return <div className="boot-screen"><ShieldCheck aria-hidden /><span>正在加载</span></div>;
  if (sessionQuery.error && "status" in sessionQuery.error && sessionQuery.error.status === 401) return <LoginView />;
  if (sessionQuery.error) return <main className="centered"><ErrorNotice error={sessionQuery.error} /></main>;
  if (!sessionQuery.data || projectsQuery.isPending) return <div className="boot-screen"><ShieldCheck aria-hidden /><span>正在加载项目</span></div>;
  if (projectsQuery.error) return <main className="centered"><ErrorNotice error={projectsQuery.error} /></main>;
  return <Shell session={sessionQuery.data} projects={projectsQuery.data ?? { items: [] }} />;
}
