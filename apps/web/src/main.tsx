import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Landing } from "./landing/Landing";
import { shareTokenFromPath } from "./routing";
import "./styles/base.css";

/** 按路径分发：`/` 是公开落地页（不需要登录），`/workspace` 是研究证据
 * 工作台（未登录时渲染 AuthView 注册/登录页），`/shared/{token}` 是公开
 * 只读分享（同样不需要登录）。两边用整页跳转互连，工作台内部的 ?task=
 * 导航不经过这里，所以不需要路由库或 popstate 监听（YAGNI）。
 *
 * `/shared/{token}` 必须交给 App：只认 `/workspace` 会让分享链接落到落地
 * 页，接收者看到的是一排「进入工作台 / 登录」，也就是要求登录——分享链接
 * 正是靠不需要账号才有意义。 */
function Root() {
  const path = window.location.pathname;
  return path === "/workspace" || shareTokenFromPath(path) !== null ? (
    <App />
  ) : (
    <Landing />
  );
}

const container = document.getElementById("root");
if (!container) {
  throw new Error("missing #root");
}

createRoot(container).render(
  <StrictMode>
    <ErrorBoundary>
      <Root />
    </ErrorBoundary>
  </StrictMode>,
);
