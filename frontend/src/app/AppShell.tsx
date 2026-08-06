import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";

const navigation = [
  ["/overview", "⌂", "运行总览"],
  ["/alerts", "⌁", "告警收件箱"],
  ["/projects", "▦", "工程接入"],
  ["/incidents", "◇", "事件中心"],
  ["/assistant", "✦", "对话助手"],
  ["/experiences", "◎", "经验中心"],
  ["/evaluations", "✓", "Agent 评测"],
] as const;

const roleNames: Record<string, string> = {
  viewer: "观察员",
  operator: "运维员",
  approver: "审批员",
  admin: "管理员",
};

export function AppShell() {
  const { user, logout } = useAuth();
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">O</span>
          <div><strong>OnCall</strong><small>智能响应中心</small></div>
        </div>
        <nav aria-label="主导航">
          {navigation.map(([to, icon, label]) => (
            <NavLink key={to} to={to} className={({ isActive }) => isActive ? "active" : ""}>
              <span aria-hidden="true">{icon}</span>{label}
            </NavLink>
          ))}
          {user?.role === "admin" && <NavLink to="/admin/users" className={({ isActive }) => isActive ? "active" : ""}><span aria-hidden="true">⚙</span>用户管理</NavLink>}
          <button className="mobile-logout" onClick={() => void logout()}><span aria-hidden="true">↗</span>退出登录</button>
        </nav>
        <div className="sidebar-footer">
          <div className="user-avatar">{user?.username.slice(0, 1).toUpperCase()}</div>
          <div><strong>{user?.username}</strong><small>{roleNames[user?.role ?? ""] ?? user?.role}</small></div>
          <button className="icon-button" title="退出登录" aria-label="退出登录" onClick={() => void logout()}>↗</button>
        </div>
      </aside>
      <div className="workspace">
        <div className="mobile-brand"><span className="brand-mark">O</span><strong>OnCall</strong></div>
        <Outlet />
      </div>
    </div>
  );
}
