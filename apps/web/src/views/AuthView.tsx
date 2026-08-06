/** 注册 / 登录页。
 *
 * `/workspace` 未登录时显示这里。登录成功后 token 存入 localStorage
 * （本机免登录，30 天有效），回调进入工作台。注册与登录共用同一张表单，
 * 双 tab 切换；错误（用户名重复 / 密码错误）就地显示，不伪装成功。
 */

import { type FormEvent, useState } from "react";

import { login, register } from "../api/client";

import "./AuthView.css";

export function AuthView({
  onAuthed,
  initialMode = "login",
}: {
  /** 登录/注册成功：把用户名交回 App —— 用户名是草稿命名空间等
   * 按账号隔离功能的键，注册/登录路径上不能是空串。 */
  onAuthed: (username: string) => void;
  /** 落地页等外部入口可以通过 URL ?mode=register 指定初始 tab。 */
  initialMode?: "login" | "register";
}) {
  const [mode, setMode] = useState<"login" | "register">(initialMode);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting || !username.trim() || !password) return;
    if (mode === "register" && password !== confirm) {
      setError("两次输入的密码不一致");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      if (mode === "register") {
        await register(username.trim(), password);
      } else {
        await login(username.trim(), password);
      }
      onAuthed(username.trim());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSubmitting(false);
    }
  }

  function switchMode(next: "login" | "register") {
    setMode(next);
    setError(null);
    setConfirm("");
  }

  return (
    <div className="auth">
      <div className="auth__card">
        <div className="auth__brand">
          <h1>Poliscope</h1>
          <p className="auth__tagline">
            EpistemoBrain · 七人议会争议证据地图
          </p>
        </div>

        <div className="auth__switch" role="tablist" aria-label="登录或注册">
          {(["login", "register"] as const).map((item) => (
            <button
              key={item}
              type="button"
              role="tab"
              aria-selected={mode === item}
              className={"auth__tab" + (mode === item ? " auth__tab--on" : "")}
              onClick={() => switchMode(item)}
            >
              {item === "login" ? "登录" : "注册"}
            </button>
          ))}
        </div>

        <form className="auth__form" onSubmit={submit}>
          <label className="auth__field">
            用户名
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="2-64 位字母、数字、_ . -"
              autoComplete="username"
              disabled={submitting}
              spellCheck={false}
            />
          </label>
          <label className="auth__field">
            密码
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="至少 6 位"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              disabled={submitting}
            />
          </label>
          {mode === "register" ? (
            <label className="auth__field">
              确认密码
              <input
                type="password"
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
                autoComplete="new-password"
                disabled={submitting}
              />
            </label>
          ) : null}

          {error ? (
            <p className="auth__error" role="alert">
              {error}
            </p>
          ) : null}

          <button
            type="submit"
            className="button button--primary auth__submit"
            disabled={submitting || !username.trim() || !password}
          >
            {submitting
              ? "请稍候…"
              : mode === "login"
                ? "进入工作台"
                : "创建账号并进入"}
          </button>
        </form>

        <p className="auth__note">
          {mode === "login"
            ? "登录后本机将记住登录状态，下次直接进入工作台。"
            : "注册后本机自动登录；账号下的任务、知识库与 Skills 相互隔离。"}
        </p>
        <p className="auth__safety">
          本系统为科研辅助工具，不提供医学诊断或医疗建议。
        </p>
      </div>
    </div>
  );
}
