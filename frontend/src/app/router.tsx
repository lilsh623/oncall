import { Navigate, Outlet, Route, Routes, Link, useLocation } from "react-router-dom";
import { BrowserRouter } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { IncidentDetailPage } from "../pages/IncidentDetailPage";
import { IncidentListPage } from "../pages/IncidentListPage";
import { LoginPage } from "../pages/LoginPage";
import { UserAdminPage } from "../pages/UserAdminPage";
import { ExperiencePage } from "../pages/ExperiencePage";
import { ConversationPage } from "../pages/ConversationPage";
import { EvaluationPage } from "../pages/EvaluationPage";
function Protected() { const { user, logout } = useAuth(); const location = useLocation(); if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />; return <><header><Link to="/incidents">OnCall</Link><Link to="/assistant">对话助手</Link><Link to="/evaluations">Agent 评测</Link><Link to="/experiences">经验中心</Link><span>{user.username} · {user.role}</span>{user.role === "admin" && <Link to="/admin/users">用户管理</Link>}<button onClick={() => void logout()}>退出</button></header><Outlet /></>; }
export function AppRouter() { return <BrowserRouter><Routes><Route path="/login" element={<LoginPage />} /><Route element={<Protected />}><Route path="/incidents" element={<IncidentListPage />} /><Route path="/incidents/:incidentId" element={<IncidentDetailPage />} /><Route path="/assistant" element={<ConversationPage />} /><Route path="/evaluations" element={<EvaluationPage />} /><Route path="/experiences" element={<ExperiencePage />} /><Route path="/admin/users" element={<UserAdminPage />} /></Route><Route path="*" element={<Navigate to="/incidents" replace />} /></Routes></BrowserRouter>; }
