/** 注册 / 登录页。
 *
 * `/workspace` 未登录时显示这里。登录成功后 token 存入 localStorage
 * （本机免登录，30 天有效），回调进入工作台。注册与登录共用同一张表单，
 * 双 tab 切换；错误（用户名重复 / 密码错误）就地显示，不伪装成功。
 */

import { type FormEvent, useEffect, useRef, useState } from "react";

import {
  confirmRegistration,
  login,
  requestPasswordReset,
  requestRegistration,
  resetPassword,
} from "../api/client";
import { t } from "../i18n";

import "./AuthView.css";

/** How many seconds to wait before the code can be resent. */
const RESEND_SECONDS = 60;

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
  /** Registration has two steps: fill the form (username/password/email),
   * then verify the emailed 6-digit code. Login stays single-step. */
  const [registerStep, setRegisterStep] = useState<"form" | "verify">("form");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [sentEmail, setSentEmail] = useState<string | null>(null);
  const [resendIn, setResendIn] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);

  /** 忘记密码流程（login tab 内）：null=正常登录，否则显示重置表单。 */
  const [resetActive, setResetActive] = useState(false);
  const [resetStep, setResetStep] = useState<"email" | "code" | "new-password">(
    "email",
  );
  const [resetEmail, setResetEmail] = useState("");
  const [resetCode, setResetCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newConfirm, setNewConfirm] = useState("");
  const [resetDone, setResetDone] = useState(false);

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) window.clearInterval(timerRef.current);
    };
  }, []);

  function startCountdown(seconds: number) {
    setResendIn(seconds);
    if (timerRef.current !== null) window.clearInterval(timerRef.current);
    timerRef.current = window.setInterval(() => {
      setResendIn((current) => {
        if (current <= 1) {
          if (timerRef.current !== null) {
            window.clearInterval(timerRef.current);
            timerRef.current = null;
          }
          return 0;
        }
        return current - 1;
      });
    }, 1000);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    if (mode === "login") {
      if (resetActive) {
        await handleReset(event);
        return;
      }
      if (!username.trim() || !password) return;
      setSubmitting(true);
      setError(null);
      try {
        await login(username.trim(), password);
        onAuthed(username.trim());
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setSubmitting(false);
      }
      return;
    }

    // Registration phase 1: send the code. No account is created yet.
    if (registerStep === "form") {
      if (password !== confirm) {
        setError(t("两次输入的密码不一致"));
        return;
      }
      if (!email.trim()) {
        setError(t("邮箱格式不正确"));
        return;
      }
      setSubmitting(true);
      setError(null);
      try {
        await requestRegistration({
          username: username.trim(),
          password,
          email: email.trim(),
        });
        setSentEmail(email.trim());
        setRegisterStep("verify");
        startCountdown(RESEND_SECONDS);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setSubmitting(false);
      }
      return;
    }

    // Registration phase 2: verify the code and create the account.
    if (!/^\d{6}$/.test(code.trim())) {
      setError(t("6 位数字验证码"));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await confirmRegistration({
        username: username.trim(),
        password,
        email: sentEmail ?? email.trim(),
        code: code.trim(),
      });
      onAuthed(username.trim());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSubmitting(false);
    }
  }

  async function resendCode() {
    if (!sentEmail || resendIn > 0) return;
    setSubmitting(true);
    setError(null);
    try {
      await requestRegistration({
        username: username.trim(),
        password,
        email: sentEmail,
      });
      startCountdown(RESEND_SECONDS);
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
    setEmail("");
    setCode("");
    setSentEmail(null);
    setResendIn(0);
    setRegisterStep("form");
    setResetActive(false);
    setResetStep("email");
    setResetEmail("");
    setResetCode("");
    setNewPassword("");
    setNewConfirm("");
    setResetDone(false);
  }

  /** 忘记密码：发码 → 验证并设新密码。分三步，表单按钮按步切换语义。 */
  async function handleReset(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    if (resetDone) {
      setResetActive(false);
      setResetStep("email");
      setError(null);
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      if (resetStep === "email") {
        if (!resetEmail.trim()) {
          setError(t("邮箱格式不正确"));
          return;
        }
        await requestPasswordReset(resetEmail.trim());
        setResetStep("code");
        startCountdown(RESEND_SECONDS);
        return;
      }
      if (resetStep === "code") {
        if (!/^\d{6}$/.test(resetCode.trim())) {
          setError(t("6 位数字验证码"));
          return;
        }
        setResetStep("new-password");
        return;
      }
      // new-password：两次一致 → 提交重置。
      if (newPassword !== newConfirm) {
        setError(t("两次输入的密码不一致"));
        return;
      }
      await resetPassword(resetEmail.trim(), resetCode.trim(), newPassword);
      setResetDone(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="auth">
      <div className="auth__card">
        <div className="auth__brand">
          <h1>Poliscope</h1>
          <p className="auth__tagline">
            EpistemoBrain · {t("七人议会争议证据地图")}
          </p>
        </div>

        <div className="auth__switch" role="tablist" aria-label={t("登录或注册")}>
          {(["login", "register"] as const).map((item) => (
            <button
              key={item}
              type="button"
              role="tab"
              aria-selected={mode === item}
              className={"auth__tab" + (mode === item ? " auth__tab--on" : "")}
              onClick={() => switchMode(item)}
            >
              {item === "login" ? t("登录") : t("注册")}
            </button>
          ))}
        </div>

        {mode === "register" ? (
          <p className="auth__step">
            {registerStep === "form" ? t("1/2 · 填写信息") : t("2/2 · 邮箱验证")}
          </p>
        ) : null}

        <form className="auth__form" onSubmit={submit}>
          {mode === "login" && resetActive ? (
            <>
              {resetDone ? (
                <p className="auth__ok">{t("密码已重置，请重新登录")}</p>
              ) : (
                <>
                  {resetStep === "email" ? (
                    <label className="auth__field">
                      {t("邮箱")}
                      <input
                        type="email"
                        value={resetEmail}
                        onChange={(event) => setResetEmail(event.target.value)}
                        placeholder={t("用于接收验证码")}
                        autoComplete="email"
                        disabled={submitting}
                        spellCheck={false}
                      />
                    </label>
                  ) : null}
                  {resetStep === "code" ? (
                    <>
                      <label className="auth__field">
                        {t("验证码")}
                        <input
                          className="auth__code"
                          value={resetCode}
                          onChange={(event) =>
                            setResetCode(event.target.value.replace(/[^\d]/g, ""))
                          }
                          inputMode="numeric"
                          maxLength={6}
                          autoComplete="one-time-code"
                          disabled={submitting}
                          placeholder="000000"
                        />
                      </label>
                      <p className="auth__hint">
                        {t("验证码已发送至 {0}", resetEmail)}
                      </p>
                      <p className="auth__hint">
                        {t("请在 5 分钟内完成验证，注意查收垃圾邮件。")}
                      </p>
                    </>
                  ) : null}
                  {resetStep === "new-password" ? (
                    <>
                      <label className="auth__field">
                        {t("新密码")}
                        <input
                          type="password"
                          value={newPassword}
                          onChange={(event) => setNewPassword(event.target.value)}
                          autoComplete="new-password"
                          disabled={submitting}
                        />
                      </label>
                      <label className="auth__field">
                        {t("确认新密码")}
                        <input
                          type="password"
                          value={newConfirm}
                          onChange={(event) => setNewConfirm(event.target.value)}
                          autoComplete="new-password"
                          disabled={submitting}
                        />
                      </label>
                    </>
                  ) : null}
                </>
              )}
            </>
          ) : (
            <>
              <label className="auth__field">
                {t("用户名")}
                <input
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  placeholder={t("2-64 位字母、数字、_ . -")}
                  autoComplete="username"
                  disabled={submitting}
                  spellCheck={false}
                />
              </label>
              <label className="auth__field">
                {t("密码")}
                <input
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder={t("至少 6 位")}
                  autoComplete={mode === "login" ? "current-password" : "new-password"}
                  disabled={submitting}
                />
              </label>
            </>
          )}
          {mode === "register" && registerStep === "form" ? (
            <>
              <label className="auth__field">
                {t("确认密码")}
                <input
                  type="password"
                  value={confirm}
                  onChange={(event) => setConfirm(event.target.value)}
                  autoComplete="new-password"
                  disabled={submitting}
                />
              </label>
              <label className="auth__field">
                {t("邮箱")}
                <input
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder={t("用于接收验证码")}
                  autoComplete="email"
                  disabled={submitting}
                  spellCheck={false}
                />
              </label>
            </>
          ) : null}

          {error ? (
            <p className="auth__error" role="alert">
              {error}
            </p>
          ) : null}

          {mode === "register" && registerStep === "verify" ? (
            <div className="auth__verify">
              <label className="auth__field">
                {t("验证码")}
                <input
                  className="auth__code"
                  value={code}
                  onChange={(event) =>
                    setCode(event.target.value.replace(/[^\d]/g, ""))
                  }
                  inputMode="numeric"
                  maxLength={6}
                  autoComplete="one-time-code"
                  disabled={submitting}
                  placeholder="000000"
                />
              </label>
              <p className="auth__hint">
                {sentEmail ? t("验证码已发送至 {0}", sentEmail) : ""}
              </p>
              <p className="auth__hint">
                {t("请在 5 分钟内完成验证，注意查收垃圾邮件。")}
              </p>
              <div className="auth__verify-actions">
                <button
                  type="button"
                  className="auth__resend"
                  onClick={resendCode}
                  disabled={submitting || resendIn > 0}
                >
                  {resendIn > 0
                    ? t("重新发送（{0} 秒）", String(resendIn))
                    : t("重新发送")}
                </button>
                <button
                  type="button"
                  className="auth__back"
                  onClick={() => {
                    setRegisterStep("form");
                    setError(null);
                  }}
                  disabled={submitting}
                >
                  {t("返回修改")}
                </button>
              </div>
            </div>
          ) : null}

          <button
            type="submit"
            className="button button--primary auth__submit"
            disabled={
              submitting ||
              (mode === "login" && !resetActive
                ? !username.trim() || !password
                : mode === "register"
                  ? !username.trim() ||
                    !password ||
                    (registerStep === "verify"
                      ? !/^\d{6}$/.test(code.trim())
                      : !email.trim())
                  : resetActive
                    ? resetDone
                      ? false
                      : resetStep === "email"
                        ? !resetEmail.trim()
                        : resetStep === "code"
                          ? !/^\d{6}$/.test(resetCode.trim())
                          : !newPassword || !newConfirm
                    : false)
            }
          >
            {submitting
              ? t("请稍候…")
              : mode === "login"
                ? resetActive
                  ? resetDone
                    ? t("返回登录")
                    : resetStep === "email"
                      ? t("发送重置验证码")
                      : resetStep === "code"
                        ? t("下一步")
                        : t("重置密码")
                  : t("进入工作台")
                : registerStep === "form"
                  ? t("发送验证码并继续")
                  : t("完成注册")}
          </button>
          {mode === "login" && !resetActive ? (
            <button
              type="button"
              className="auth__forgot"
              onClick={() => {
                setResetActive(true);
                setResetStep("email");
                setError(null);
              }}
              disabled={submitting}
            >
              {t("忘记密码？")}
            </button>
          ) : null}
          {mode === "login" && resetActive ? (
            <button
              type="button"
              className="auth__forgot"
              onClick={() => {
                setResetActive(false);
                setError(null);
                setResetStep("email");
                setResetEmail("");
                setResetCode("");
                setNewPassword("");
                setNewConfirm("");
              }}
              disabled={submitting}
            >
              {t("返回登录")}
            </button>
          ) : null}
        </form>

        <p className="auth__note">
          {mode === "login"
            ? t("登录后本机将记住登录状态，下次直接进入工作台。")
            : registerStep === "form"
              ? t("注册后本机自动登录；账号下的任务、知识库与 Skills 相互隔离。")
              : t("2/2 · 邮箱验证")}
        </p>
        <p className="auth__safety">
          {t("本系统为科研辅助工具，不提供医学诊断或医疗建议。")}
        </p>
      </div>
    </div>
  );
}
