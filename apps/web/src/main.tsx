import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { Landing } from "./landing/Landing";
import "./styles/base.css";

/** 按路径分发：`/` 是公开落地页（不设访问口令），`/workspace` 是研究证据
 * 工作台（部署时由 Caddy 在 /workspace* 上保留共享口令）。两边用整页跳转
 * 互连，工作台内部的 ?task= 导航不经过这里，所以不需要路由库或
 * popstate 监听（YAGNI）。 */
function Root() {
  return window.location.pathname === "/workspace" ? <App /> : <Landing />;
}

const container = document.getElementById("root");
if (!container) {
  throw new Error("missing #root");
}

createRoot(container).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
