import { FileSearch, Plus, ScrollText } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

export function AppShell() {
  return (
    <div className="app">
      <header className="topbar">
        <NavLink to="/" className="brand">
          <span className="brand__mark"><FileSearch size={19} /></span>
          <span>
            <strong>试卷识别台</strong>
            <small>题目与参考答案匹配</small>
          </span>
        </NavLink>
        <nav>
          <NavLink to="/" end><ScrollText size={17} />任务</NavLink>
          <NavLink to="/new"><Plus size={17} />新建识别</NavLink>
        </nav>
      </header>
      <main><Outlet /></main>
    </div>
  );
}

