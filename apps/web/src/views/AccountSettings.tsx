/** 账号设置弹层：头像上传、修改用户名、修改密码。
 *
 * 遵循 Apple 设计语言（tokens.css）：唯一 #0066cc 交互色、pill 按钮、
 * hairline 卡片、负字距、无装饰渐变。
 *
 * 这里刻意不提供注销账户：账号一旦注销就释放了邮箱，而免费体验额度是
 * 每账号一次——「注销后重新注册」会变成反复消耗体验额度的路径。
 */

import { useState } from "react";

import {
  changePassword,
  changeUsername,
  uploadAvatar,
} from "../api/client";
import { t } from "../i18n";

import "./AccountSettings.css";

export function AccountSettings({
  username,
  onClose,
  onAvatarChanged,
}: {
  username: string;
  onClose: () => void;
  onAvatarChanged: () => void;
}) {
  const [avatarBusy, setAvatarBusy] = useState(false);
  const [avatarError, setAvatarError] = useState<string | null>(null);

  const [newName, setNewName] = useState("");
  const [namePassword, setNamePassword] = useState("");
  const [nameBusy, setNameBusy] = useState(false);
  const [nameError, setNameError] = useState<string | null>(null);
  const [nameDone, setNameDone] = useState(false);

  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordDone, setPasswordDone] = useState(false);

  async function onPickAvatar(file: File | undefined) {
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) {
      setAvatarError(t("头像不能超过 2MB"));
      return;
    }
    setAvatarBusy(true);
    setAvatarError(null);
    try {
      await uploadAvatar(file);
      onAvatarChanged();
    } catch (cause) {
      setAvatarError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAvatarBusy(false);
    }
  }

  async function submitName() {
    if (nameBusy || !newName.trim() || !namePassword) return;
    setNameBusy(true);
    setNameError(null);
    setNameDone(false);
    try {
      const result = await changeUsername(newName.trim(), namePassword);
      // 用户名已改；父组件靠页面刷新反映，这里提示即可。
      setNameDone(true);
      setNewName(result.username);
      setNamePassword("");
    } catch (cause) {
      setNameError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setNameBusy(false);
    }
  }

  async function submitPassword() {
    if (passwordBusy || !oldPassword || !newPassword) return;
    if (newPassword !== confirmPassword) {
      setPasswordError(t("两次输入的密码不一致"));
      return;
    }
    setPasswordBusy(true);
    setPasswordError(null);
    setPasswordDone(false);
    try {
      await changePassword(oldPassword, newPassword);
      setPasswordDone(true);
      setOldPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (cause) {
      setPasswordError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPasswordBusy(false);
    }
  }

  return (
    <div className="account-settings" role="dialog" aria-label={t("账号设置")}>
      <div className="account-settings__head">
        <h2>{t("账号设置")}</h2>
        <button type="button" className="account-settings__close" onClick={onClose}>
          ✕
        </button>
      </div>

        {/* 头像 */}
        <section className="account-settings__section">
          <h3>{t("头像")}</h3>
          <label className="account-settings__avatar-pick">
            <input
              type="file"
              accept="image/png,image/jpeg"
              onChange={(event) => onPickAvatar(event.target.files?.[0])}
              disabled={avatarBusy}
            />
            {avatarBusy ? t("请稍候…") : t("上传头像")}
          </label>
          <p className="account-settings__hint">
            {t("头像仅支持 PNG/JPG，且不超过 2MB。")}
          </p>
          {avatarError ? (
            <p className="account-settings__error">{avatarError}</p>
          ) : null}
        </section>

        {/* 修改用户名 */}
        <section className="account-settings__section">
          <h3>{t("修改用户名")}</h3>
          <p className="account-settings__current">
            {t("当前用户名")}：{username}
          </p>
          <input
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            placeholder={t("新用户名")}
            disabled={nameBusy}
            spellCheck={false}
          />
          <input
            type="password"
            value={namePassword}
            onChange={(event) => setNamePassword(event.target.value)}
            placeholder={t("输入原密码")}
            disabled={nameBusy}
          />
          <button
            type="button"
            className="button button--primary"
            onClick={submitName}
            disabled={nameBusy || !newName.trim() || !namePassword}
          >
            {nameBusy ? t("请稍候…") : t("保存")}
          </button>
          {nameDone ? (
            <p className="account-settings__ok">{t("用户名已更新")}</p>
          ) : null}
          {nameError ? (
            <p className="account-settings__error">{nameError}</p>
          ) : null}
        </section>

        {/* 修改密码 */}
        <section className="account-settings__section">
          <h3>{t("修改密码")}</h3>
          <input
            type="password"
            value={oldPassword}
            onChange={(event) => setOldPassword(event.target.value)}
            placeholder={t("原密码")}
            disabled={passwordBusy}
          />
          <input
            type="password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            placeholder={t("新密码")}
            disabled={passwordBusy}
          />
          <input
            type="password"
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
            placeholder={t("确认新密码")}
            disabled={passwordBusy}
          />
          <button
            type="button"
            className="button button--primary"
            onClick={submitPassword}
            disabled={
              passwordBusy || !oldPassword || !newPassword || !confirmPassword
            }
          >
            {passwordBusy ? t("请稍候…") : t("保存")}
          </button>
          {passwordDone ? (
            <p className="account-settings__ok">{t("密码已更新")}</p>
          ) : null}
          {passwordError ? (
            <p className="account-settings__error">{passwordError}</p>
          ) : null}
        </section>
    </div>
  );
}
