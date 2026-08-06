import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { BrowserRouter } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { IncidentDetailPage } from "../pages/IncidentDetailPage";
import { IncidentListPage } from "../pages/IncidentListPage";
import { LoginPage } from "../pages/LoginPage";
import { UserAdminPage } from "../pages/UserAdminPage";
import { ExperiencePage } from "../pages/ExperiencePage";
import { ConversationPage } from "../pages/ConversationPage";
import { EvaluationPage } from "../pages/EvaluationPage";
import { AppShell } from "./AppShell";
import { OperationsPage } from "../pages/OperationsPage";
import { AlertInboxPage } from "../pages/AlertInboxPage";
import { ProjectsPage } from "../pages/ProjectsPage";

function Protected() { const { user, isBootstrapping } = useAuth(); const location = useLocation(); if (isBootstrapping) return <div className="app-loading"><span className="brand-mark">O</span><p>正在连接 OnCall 后端…</p></div>; if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />; return <AppShell />; }
export function AppRouter() { return <BrowserRouter><Routes><Route path="/login" element={<LoginPage />} /><Route element={<Protected />}><Route path="/overview" element={<OperationsPage />} /><Route path="/projects" element={<ProjectsPage />} /><Route path="/alerts" element={<AlertInboxPage />} /><Route path="/incidents" element={<IncidentListPage />} /><Route path="/incidents/:incidentId" element={<IncidentDetailPage />} /><Route path="/assistant" element={<ConversationPage />} /><Route path="/evaluations" element={<EvaluationPage />} /><Route path="/experiences" element={<ExperiencePage />} /><Route path="/admin/users" element={<UserAdminPage />} /></Route><Route path="*" element={<Navigate to="/overview" replace />} /></Routes></BrowserRouter>; }
