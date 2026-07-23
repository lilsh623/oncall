import { Navigate, Outlet, Route, Routes, Link, useLocation } from "react-router-dom";
import { BrowserRouter } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { IncidentDetailPage } from "../pages/IncidentDetailPage";
import { IncidentListPage } from "../pages/IncidentListPage";
import { LoginPage } from "../pages/LoginPage";
import { UserAdminPage } from "../pages/UserAdminPage";
function Protected() { const { user, logout } = useAuth(); const location = useLocation(); if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />; return <><header><Link to="/incidents">OnCall</Link><span>{user.username} · {user.role}</span>{user.role === "admin" && <Link to="/admin/users">用户管理</Link>}<button onClick={() => void logout()}>退出</button></header><Outlet /></>; }
export function AppRouter() { return <BrowserRouter><Routes><Route path="/login" element={<LoginPage />} /><Route element={<Protected />}><Route path="/incidents" element={<IncidentListPage />} /><Route path="/incidents/:incidentId" element={<IncidentDetailPage />} /><Route path="/admin/users" element={<UserAdminPage />} /></Route><Route path="*" element={<Navigate to="/incidents" replace />} /></Routes></BrowserRouter>; }
