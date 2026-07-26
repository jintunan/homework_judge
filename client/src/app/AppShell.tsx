import {
  BarChart3,
  BookOpenCheck,
  CheckSquare2,
  ChevronDown,
  FileStack,
  LayoutDashboard,
  Plus,
  Sparkles,
  UploadCloud,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import {
  Link,
  NavLink,
  Outlet,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import { api } from "@client/lib/api";

const navItems = [
  {
    label: "答案配置",
    icon: BookOpenCheck,
    path: (taskId: string) => `/tasks/${taskId}/answers`,
  },
  {
    label: "批量上传",
    icon: UploadCloud,
    path: (taskId: string) => `/tasks/${taskId}/upload`,
  },
  {
    label: "逐题复核",
    icon: CheckSquare2,
    path: (taskId: string) => `/tasks/${taskId}/review`,
  },
  {
    label: "报告统计",
    icon: BarChart3,
    path: (taskId: string) => `/tasks/${taskId}/reports`,
  },
];

export function AppShell() {
  const params = useParams();
  const taskId = params.taskId;
  const navigate = useNavigate();
  const location = useLocation();
  const tasks = useQuery({
    queryKey: ["tasks"],
    queryFn: api.listTasks,
  });
  const model = useQuery({
    queryKey: ["model-status"],
    queryFn: api.getModelStatus,
    staleTime: 30_000,
  });
  const health = useQuery({
    queryKey: ["health"],
    queryFn: api.getHealth,
    staleTime: 60_000,
  });
  const currentTask = tasks.data?.find((task) => task.id === taskId);
  const teacherName = health.data?.teacherName ?? "本机教师";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link to="/" className="brand" aria-label="知衡批改首页">
          <span className="brand-mark">
            <Sparkles size={20} />
          </span>
          <span>
            <strong>知衡</strong>
            <small>双科批改 Agent</small>
          </span>
        </Link>

        <div className="sidebar-label">工作台</div>
        <nav className="sidebar-nav" aria-label="主导航">
          <NavLink
            to="/"
            end
            className={({ isActive }) =>
              `nav-item ${isActive ? "active" : ""}`
            }
          >
            <LayoutDashboard size={18} />
            <span>任务总览</span>
          </NavLink>
          {taskId
            ? navItems.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink
                    key={item.label}
                    to={item.path(taskId)}
                    className={({ isActive }) =>
                      `nav-item ${isActive ? "active" : ""}`
                    }
                  >
                    <Icon size={18} />
                    <span>{item.label}</span>
                  </NavLink>
                );
              })
            : null}
        </nav>

        <div className="sidebar-spacer" />
        <div className="model-card">
          <div className="model-card-heading">
            <span
              className={`connection-dot ${
                model.data?.configured ? "online" : "offline"
              }`}
            />
            <span>千问视觉模型</span>
          </div>
          <strong>{model.data?.model ?? "正在检查…"}</strong>
          <small>
            {model.data?.configured
              ? `${model.data.regionHint} · 已连接`
              : "未配置 API Key"}
          </small>
        </div>
        <div className="human-review-note">
          <CheckSquare2 size={16} />
          <span>模型只做初评，成绩须由教师确认</span>
        </div>
      </aside>

      <div className="main-area">
        <div className="topbar">
          <button
            type="button"
            className="task-switcher"
            onClick={() => navigate("/")}
          >
            <FileStack size={17} />
            <span>
              {currentTask
                ? `${currentTask.className} · ${currentTask.paperName}`
                : location.pathname === "/"
                  ? "全部批改任务"
                  : "选择批改任务"}
            </span>
            <ChevronDown size={15} />
          </button>
          <Link to="/tasks/new" className="button button-primary button-small">
            <Plus size={16} />
            创建任务
          </Link>
          <div className="teacher-avatar">{teacherName.slice(0, 1)}</div>
          <div className="teacher-meta">
            <strong>{teacherName}</strong>
            <span>
              {currentTask?.subject === "high_school_physics"
                ? "高中物理"
                : currentTask?.subject === "middle_school_math"
                  ? "初中数学"
                  : "数学 · 物理"}
            </span>
          </div>
        </div>
        <main className="page-container">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
